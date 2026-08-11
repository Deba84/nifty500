"""Daily Nifty 500 scan v3 with LIQUIDITY-BASED levels."""
import os
import sys
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from nifty500_list import get_symbol_map
from scanner_engine import scan_stock

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("❌ Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
    sys.exit(1)


def send_telegram(text, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    max_len = 4000
    chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]
    for chunk in chunks:
        try:
            r = requests.post(url, data={
                "chat_id": TELEGRAM_CHAT_ID, "text": chunk,
                "parse_mode": parse_mode, "disable_web_page_preview": True,
            }, timeout=30)
            if not r.ok:
                print(f"⚠️ Telegram error: {r.text}")
        except Exception as e:
            print(f"⚠️ Telegram exception: {e}")


def format_stock_card(r, rank):
    dir_emoji = "🟢" if r["direction"] == "LONG" else "🔴"
    change_emoji = "▲" if r["change_pct"] >= 0 else "▼"
    tv_link = f"https://in.tradingview.com/chart/?symbol=NSE:{r['symbol']}"
    screener_link = f"https://www.screener.in/company/{r['symbol']}/"

    if r["verdict"] == "A+ SUPER":
        badge = "🏆👑 SUPER"
    elif r["verdict"] == "A+ SETUP":
        badge = "🏆 A+"
    else:
        badge = "✅ A"

    score_display = f"[{r['tech_score']}T + {r['fund_score']}F = {r['score']}/15]"

    # BSL/SSL levels (nearest 3)
    bsl_str = " → ".join([f"₹{lvl}" for lvl in r.get('bsl_levels', [])[:3]]) or "None"
    ssl_str = " → ".join([f"₹{lvl}" for lvl in r.get('ssl_levels', [])[:3]]) or "None"

    text = f"""
━━━━━━━━━━━━━━━━━━━━━
<b>#{rank}. {r['symbol']}</b> {dir_emoji} {r['direction']} {badge}
<i>{r['name']} • {r['sector']}</i>
📊 Score: {score_display}

💰 <b>Price:</b> ₹{r['price']}  ({change_emoji} {abs(r['change_pct'])}%)
📊 <b>52W:</b> ₹{r['wk52_low']} - ₹{r['wk52_high']}  (-{r['dist_52wh_pct']}% from high)

🌊 <b>LIQUIDITY MAP:</b>
📈 BSL (targets above): {bsl_str}
📉 SSL (stops below): {ssl_str}

🎯 <b>TRADE PLAN (Liquidity-Based):</b>
• Entry: <b>₹{r['entry']}</b>
• SL: <b>₹{r['sl']}</b>
  <i>↳ {r['sl_reason']}</i>
• TP1: <b>₹{r['tp1']}</b> (R:R = {r['rr1']}:1) — 50% partial
• TP2: <b>₹{r['tp2']}</b> (R:R = {r['rr2']}:1) — trail rest
• Risk: {r['risk_pct']}%

📈 Daily: {r['trend_daily']} | Weekly: {r['trend_weekly']}
"""
    if r.get('fund_data'):
        fd = r['fund_data']
        fund_line = ""
        if fd.get('ROE'): fund_line += f"ROE: {fd['ROE']}% | "
        if fd.get('ROCE'): fund_line += f"ROCE: {fd['ROCE']}% | "
        if fd.get('Stock P/E'): fund_line += f"P/E: {fd['Stock P/E']} | "
        if fd.get('Promoter Holding'): fund_line += f"Promoter: {fd['Promoter Holding']}%"
        if fund_line:
            text += f"💼 <b>Fundamental:</b> {fund_line.rstrip(' | ')}\n"

    if r["reasons"]:
        text += f"\n✨ <b>Why:</b> {' • '.join(r['reasons'][:4])}\n"

    text += f"\n🔗 <a href='{tv_link}'>Chart</a> | <a href='{screener_link}'>Fundamentals</a>\n"
    return text


def main():
    print("🚀 Starting v3 scan (Liquidity-based levels)...")
    start_time = datetime.now()

    sym_map = get_symbol_map()
    symbols = list(sym_map.items())
    print(f"📊 Scanning {len(symbols)} stocks...")

    results = []
    completed = 0
    failed = 0

    def scan_one(item):
        sym, info = item
        try:
            return scan_stock(sym, info, include_fundamental=False)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(scan_one, item): item for item in symbols}
        for future in as_completed(futures):
            r = future.result()
            completed += 1
            if r:
                results.append(r)
            else:
                failed += 1
            if completed % 40 == 0:
                print(f"  Tech: {completed}/{len(symbols)}")

    # Sort by tech_score + R:R quality
    results.sort(key=lambda x: (x["tech_score"], x["rr1"]), reverse=True)
    print(f"✅ Technical done.")

    # Enrich top 25 with fundamentals
    top_candidates = [r for r in results if r["tech_score"] >= 6][:25]
    print(f"💼 Fetching fundamentals for top {len(top_candidates)}...")

    from fundamental_analyzer import get_fundamental_score
    for i, r in enumerate(top_candidates):
        try:
            fund_result = get_fundamental_score(r["symbol"])
            r["fund_score"] = fund_result["score"]
            r["fund_signals"] = fund_result["signals"]
            r["fund_data"] = fund_result["data"]
            r["reasons"].extend(fund_result["reasons"])
            r["score"] = r["tech_score"] + r["fund_score"]
            # Re-verdict with fundamental (needs R:R >= 1.5 also)
            if r["tech_score"] >= 8 and r["fund_score"] >= 3 and r["rr1"] >= 1.5:
                r["verdict"], r["verdict_color"] = "A+ SUPER", "purple"
            if i % 5 == 0:
                print(f"  Fund: {i+1}/{len(top_candidates)}")
        except Exception as e:
            print(f"  ⚠️ {r['symbol']}: {e}")

    # Re-sort by score+RR
    top_candidates.sort(key=lambda x: (x["score"], x["rr1"]), reverse=True)

    duration = (datetime.now() - start_time).total_seconds()
    print(f"✅ Done in {duration:.0f}s")

    if not results:
        send_telegram("⚠️ SCAN FAILED\nData fetch করা যায়নি।")
        return

    super_setup = [r for r in top_candidates if r["verdict"] == "A+ SUPER"]
    aplus = [r for r in top_candidates if r["verdict"] == "A+ SETUP"]
    a_setup = [r for r in results if r["verdict"] == "A SETUP"]

    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    header = f"""
🌟 <b>NIFTY 500 DAILY SCAN v3</b> 🌟
📅 {now_str} IST

📊 <b>Summary:</b>
━━━━━━━━━━━━━━━━━━━━━
🏆👑 SUPER (Tech+Fund+RR): <b>{len(super_setup)}</b>
🏆 A+ Setups: <b>{len(aplus)}</b>
✅ A Setups: <b>{len(a_setup)}</b>
📈 Total Scanned: {len(results)} / {len(symbols)}
━━━━━━━━━━━━━━━━━━━━━

🌊 <b>NEW: Liquidity-Based Levels</b>
• Entry: Current price (verify on chart)
• SL: Below sweep wick + buffer
• TP1/TP2: Next opposite liquidity zones
• Only R:R ≥ 1.5:1 setups shown

💡 SUPER = Tech A+ AND Fund Strong AND R:R ≥ 1.5
"""
    send_telegram(header)

    if super_setup:
        send_telegram(f"\n🏆👑 <b>SUPER SETUPS</b> ({len(super_setup)}) — Best of Best!\n")
        for i, r in enumerate(super_setup[:10], 1):
            send_telegram(format_stock_card(r, i))
    else:
        send_telegram("\n⚠️ আজ কোনো SUPER setup পাওয়া যায়নি।")

    remaining_aplus = [r for r in top_candidates if r["verdict"] == "A+ SETUP"][:8]
    if remaining_aplus:
        send_telegram(f"\n🏆 <b>A+ TECHNICAL SETUPS</b> ({len(remaining_aplus)}):\n")
        for i, r in enumerate(remaining_aplus, 1):
            send_telegram(format_stock_card(r, i))

    footer = """
━━━━━━━━━━━━━━━━━━━━━
⚠️ <b>Disclaimer:</b> শুধুমাত্র শিক্ষামূলক। Trade করার আগে TradingView-এ chart ও Screener.in-এ fundamentals verify করুন।

🎯 <b>Trading Rules:</b>
• Entry price শুধু reference — actual gap check করুন
• SL ও TP আগে থেকে order place করুন
• TP1-এ 50% book, বাকি TP2 বা trailing SL
• 1% risk per trade কঠোরভাবে follow

🤖 Powered by Arena.ai Scanner v3
⏰ Next scan: আগামীকাল বিকেলে
"""
    send_telegram(footer)
    print("✅ All sent!")


if __name__ == "__main__":
    main()

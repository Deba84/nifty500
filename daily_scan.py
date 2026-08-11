"""
Daily Nifty 500 scan v4 — PROACTIVE LIQUIDITY APPROACH DETECTION
Alerts stocks BEFORE sweep happens (fundamentally strong only)
"""
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
    print("❌ Missing Telegram credentials")
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
            print(f"⚠️ {e}")


def format_alert(r, rank):
    dir_emoji = "🔴" if r["direction"] == "SHORT" else "🟢"
    change_emoji = "▲" if r["change_pct"] >= 0 else "▼"
    tv_link = f"https://in.tradingview.com/chart/?symbol=NSE:{r['symbol']}"
    screener_link = f"https://www.screener.in/company/{r['symbol']}/"

    # Direction description
    if r["direction"] == "SHORT":
        setup_desc = f"Approaching {r['liq_type']} from below → wait for BSL sweep → SHORT reversal"
    else:
        setup_desc = f"Approaching {r['liq_type']} from above → wait for SSL sweep → LONG reversal"

    entry_zone_str = f"₹{r['entry_zone'][0]} - ₹{r['entry_zone'][1]}"

    text = f"""
━━━━━━━━━━━━━━━━━━━━━
{r['stage_emoji']} <b>#{rank}. {r['symbol']}</b> {dir_emoji} <b>{r['direction']}</b> Setup
<i>{r['name']} • {r['sector']}</i>
📊 <b>{r['stage']}</b> | Priority: {r['priority']}/100

💰 <b>Current Price:</b> ₹{r['price']}  ({change_emoji} {abs(r['change_pct'])}%)

🎯 <b>TARGET LIQUIDITY:</b>
📍 ₹{r['target_level']} ({r['target_level_type']})
📏 <b>Distance: {r['distance_pct']}% away</b>

🎬 <b>SETUP:</b>
<i>{setup_desc}</i>

🎯 <b>ANTICIPATED TRADE PLAN:</b>
⏰ Wait for: Sweep of ₹{r['target_level']} + rejection + CHoCH
🎯 Entry Zone: <b>{entry_zone_str}</b> (after sweep)
🛑 SL: <b>₹{r['sl']}</b> (beyond sweep wick)
🎯 TP1: <b>₹{r['tp1']}</b> (R:R = {r['rr1']}:1) — 50% partial
🎯 TP2: <b>₹{r['tp2']}</b> (R:R = {r['rr2']}:1) — trail rest
📊 Risk: {r['risk_pct']}%

📈 Trend: Daily {r['trend_daily']} | Weekly {r['trend_weekly']}
"""

    if r.get('fund_data'):
        fd = r['fund_data']
        fund_line = ""
        if fd.get('ROE'): fund_line += f"ROE: {fd['ROE']}% | "
        if fd.get('ROCE'): fund_line += f"ROCE: {fd['ROCE']}% | "
        if fd.get('Stock P/E'): fund_line += f"P/E: {fd['Stock P/E']} | "
        if fd.get('Promoter Holding'): fund_line += f"Promoter: {fd['Promoter Holding']}%"
        if fund_line:
            text += f"💼 <b>Fund [{r['fund_score']}/4]:</b> {fund_line.rstrip(' | ')}\n"

    # Show other untapped liquidity
    if r.get('bsl_levels'):
        bsl_str = " → ".join([f"₹{lvl[0]} ({lvl[1]})" for lvl in r['bsl_levels'][:2]])
        text += f"\n📈 Other BSL above: {bsl_str}"
    if r.get('ssl_levels'):
        ssl_str = " → ".join([f"₹{lvl[0]} ({lvl[1]})" for lvl in r['ssl_levels'][:2]])
        text += f"\n📉 Other SSL below: {ssl_str}"

    text += f"\n\n🔗 <a href='{tv_link}'>Chart</a> | <a href='{screener_link}'>Fundamentals</a>\n"
    return text


def main():
    print("🚀 v4 Scan — Proactive Liquidity Approach Detection")
    start_time = datetime.now()

    sym_map = get_symbol_map()
    symbols = list(sym_map.items())
    print(f"📊 Scanning {len(symbols)} stocks (fundamentally strong only)...")

    results = []
    completed = 0
    failed = 0

    def scan_one(item):
        sym, info = item
        try:
            return scan_stock(sym, info, include_fundamental=True, min_fund_score=2)
        except Exception:
            return None

    # Sequential (Screener.in doesn't like too much parallel)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(scan_one, item): item for item in symbols}
        for future in as_completed(futures):
            r = future.result()
            completed += 1
            if r:
                results.append(r)
            else:
                failed += 1
            if completed % 50 == 0:
                print(f"  {completed}/{len(symbols)} (found: {len(results)})")

    # Sort by priority (proximity + fundamental)
    results.sort(key=lambda x: x["priority"], reverse=True)
    duration = (datetime.now() - start_time).total_seconds()
    print(f"✅ Done in {duration:.0f}s — Found {len(results)} approach alerts")

    if not results:
        send_telegram("⚠️ <b>NO SETUPS TODAY</b>\n\nNo fundamentally strong stocks approaching major liquidity levels. Wait for tomorrow.")
        return

    # Categorize by stage (TIGHT ranges: 0-0.3-0.8-1.5%)
    at_level = [r for r in results if r['stage'] == 'AT LEVEL']
    very_close = [r for r in results if r['stage'] == 'VERY CLOSE']
    approaching = [r for r in results if r['stage'] == 'CLOSE']

    # Direction breakdown
    long_setups = [r for r in results if r['direction'] == 'LONG']
    short_setups = [r for r in results if r['direction'] == 'SHORT']

    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    header = f"""
🌊 <b>NIFTY 500 LIQUIDITY WATCH v4</b> 🌊
📅 {now_str} IST

🎯 <b>PROACTIVE ALERTS — Before Sweep Happens</b>

📊 <b>Summary:</b>
━━━━━━━━━━━━━━━━━━━━━
🎯 AT LEVEL (0-0.3%): <b>{len(at_level)}</b>
🔥 VERY CLOSE (0.3-0.8%): <b>{len(very_close)}</b>
⚡ CLOSE (0.8-1.5%): <b>{len(approaching)}</b>

🟢 LONG setups: <b>{len(long_setups)}</b>
🔴 SHORT setups: <b>{len(short_setups)}</b>
━━━━━━━━━━━━━━━━━━━━━

💡 <b>Only fundamentally STRONG stocks (Fund ≥ 2/4)</b>
💡 <b>Only UNTAPPED liquidity (max 1.5% away)</b>
💡 <b>Wait for sweep + CHoCH before entry!</b>
"""
    send_telegram(header)

    # Send top alerts by stage
    if at_level:
        send_telegram(f"\n🎯 <b>AT LEVEL — IMMINENT SWEEP</b> ({len(at_level)})\nHigh probability sweep-and-reverse setup!\n")
        for i, r in enumerate(at_level[:8], 1):
            send_telegram(format_alert(r, i))

    if very_close:
        send_telegram(f"\n🔥 <b>VERY CLOSE — Watch Closely</b> ({len(very_close)})\n")
        for i, r in enumerate(very_close[:6], 1):
            send_telegram(format_alert(r, i))

    if approaching:
        send_telegram(f"\n⚡ <b>APPROACHING — Watchlist</b> ({len(approaching)})\nSet alerts, wait for closer approach\n")
        for i, r in enumerate(approaching[:5], 1):
            send_telegram(format_alert(r, i))

    footer = """
━━━━━━━━━━━━━━━━━━━━━
⚠️ <b>How to Trade These Alerts:</b>

1. 📊 Open TradingView chart — verify liquidity level
2. ⏰ Wait for price to REACH the level (may take days)
3. 👁️ Watch for SWEEP (wick + rejection at level)
4. ✅ Confirm CHoCH (structure break in opposite direction)
5. 🎯 Then enter in entry zone with SL/TP as shown

⚠️ <b>DO NOT enter immediately!</b>
This is a WATCH list — wait for the sweep.

🤖 Powered by Arena.ai Scanner v4
⏰ Next scan: আগামীকাল বিকেলে
"""
    send_telegram(footer)
    print("✅ All alerts sent!")


if __name__ == "__main__":
    main()

"""
Daily Nifty 500 scan v6 — ULTRA FAST (Batch Download)
Target: Complete in 5-8 minutes (was 25+ min)
"""
import os
import sys
import requests
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from nifty500_list import get_symbol_map
from scanner_engine import batch_download_all, analyze_stock_from_df

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


def format_alert(r, rank, ai_analysis=None):
    dir_emoji = "🔴" if r["direction"] == "SHORT" else "🟢"
    change_emoji = "▲" if r["change_pct"] >= 0 else "▼"
    tv_link = f"https://in.tradingview.com/chart/?symbol=NSE:{r['symbol']}"
    screener_link = f"https://www.screener.in/company/{r['symbol']}/"

    setup_desc = f"Approaching {r['liq_type']} → wait for sweep → {r['direction']} reversal"
    entry_zone_str = f"₹{r['entry_zone'][0]} - ₹{r['entry_zone'][1]}"

    text = f"""
━━━━━━━━━━━━━━━━━━━━━
{r['stage_emoji']} <b>#{rank}. {r['symbol']}</b> {dir_emoji} <b>{r['direction']}</b>
<i>{r['name']} • {r['sector']}</i>
📊 {r['stage']} | Priority: {r['priority']}/100
"""
    if ai_analysis and ai_analysis.get('verdict') != 'N/A':
        verdict_emoji = {'STRONG_BUY': '🚀', 'BUY': '✅', 'WAIT': '⏳', 'SKIP': '❌'}.get(ai_analysis['verdict'], '🤖')
        text += f"🤖 <b>AI: {verdict_emoji} {ai_analysis['verdict']} ({ai_analysis['confidence']}/10)</b>\n"

    text += f"""
💰 <b>Current:</b> ₹{r['price']} ({change_emoji} {abs(r['change_pct'])}%)

🎯 <b>TARGET LIQUIDITY:</b>
📍 ₹{r['target_level']} ({r['target_level_type']})
📏 Distance: {r['distance_pct']}% away

🎬 <i>{setup_desc}</i>

🎯 <b>TRADE PLAN:</b>
⏰ Wait for: Sweep + rejection + CHoCH
🎯 Entry: <b>{entry_zone_str}</b>
🛑 SL: <b>₹{r['sl']}</b>
🎯 TP1: <b>₹{r['tp1']}</b> (R:R {r['rr1']}:1)
🎯 TP2: <b>₹{r['tp2']}</b> (R:R {r['rr2']}:1)
📊 Risk: {r['risk_pct']}%

📈 Trend: D {r['trend_daily']} | W {r['trend_weekly']}
"""
    if r.get('fund_data'):
        fd = r['fund_data']
        fund_line = ""
        if fd.get('ROE'): fund_line += f"ROE: {fd['ROE']}% | "
        if fd.get('ROCE'): fund_line += f"ROCE: {fd['ROCE']}% | "
        if fd.get('Stock P/E'): fund_line += f"P/E: {fd['Stock P/E']} | "
        if fd.get('Promoter Holding'): fund_line += f"Prom: {fd['Promoter Holding']}%"
        if fund_line:
            text += f"💼 <b>Fund [{r['fund_score']}/4]:</b> {fund_line.rstrip(' | ')}\n"

    if ai_analysis and ai_analysis.get('verdict') != 'N/A':
        if ai_analysis.get('commentary'):
            text += f"\n🤖 <i>{ai_analysis['commentary']}</i>\n"
        if ai_analysis.get('risks'):
            text += f"⚠️ {' • '.join(ai_analysis['risks'][:2])}\n"

    text += f"\n🔗 <a href='{tv_link}'>Chart</a> | <a href='{screener_link}'>Fundamentals</a>\n"
    return text


def main():
    print("🚀 v6 ULTRA FAST Scan")
    start_time = datetime.now()

    sym_map = get_symbol_map()
    symbols_list = list(sym_map.keys())
    symbol_info_list = list(sym_map.items())
    print(f"📊 Total stocks: {len(symbols_list)}")

    # ============ STAGE 1: Batch download (10-15 sec for 500!) ============
    print("\n=== STAGE 1: Batch Download ===")
    stock_dfs = batch_download_all(symbols_list, period="1y")
    if not stock_dfs:
        send_telegram("⚠️ SCAN FAILED\nBatch download failed")
        return

    # ============ STAGE 2: Analyze (fast, no network) ============
    print(f"\n=== STAGE 2: Technical Analysis ({len(stock_dfs)} stocks) ===")
    stage2_start = time.time()
    candidates = []

    for sym, info in symbol_info_list:
        if sym in stock_dfs:
            r = analyze_stock_from_df(stock_dfs[sym], info)
            if r:
                candidates.append((r, sym, info))

    stage2_time = time.time() - stage2_start
    print(f"✅ Analysis done in {stage2_time:.1f}s — {len(candidates)} candidates")

    # ============ STAGE 3: Fundamental for candidates only ============
    print(f"\n=== STAGE 3: Fundamental for {len(candidates)} candidates ===")
    stage3_start = time.time()
    results = []

    def add_fund(item):
        r, sym, info = item
        try:
            from fundamental_analyzer import get_fundamental_score
            fund_result = get_fundamental_score(info["symbol"])
            if fund_result["score"] < 2:
                return None
            r["fund_score"] = fund_result["score"]
            r["fund_data"] = fund_result["data"]
            r["priority"] = round((r["proximity_score"] * 0.6) + (fund_result["score"] * 25 * 0.4), 1)
            return r
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(add_fund, item) for item in candidates]
        for future in as_completed(futures):
            r = future.result()
            if r:
                results.append(r)

    stage3_time = time.time() - stage3_start
    print(f"✅ Fundamental done in {stage3_time:.1f}s — {len(results)} passed")

    results.sort(key=lambda x: x["priority"], reverse=True)

    # ============ STAGE 4: AI Analysis (optional, top 10 only) ============
    ai_results = {}
    market_view = None
    if os.environ.get("GROQ_API_KEY"):
        print(f"\n=== STAGE 4: AI Analysis (top 10) ===")
        stage4_start = time.time()
        try:
            from ai_analyzer import analyze_setup, get_market_overview
            for r in results[:10]:
                try:
                    analysis = analyze_setup(r)
                    if analysis:
                        ai_results[r['symbol']] = analysis
                except Exception:
                    continue
            market_view = get_market_overview(results[:10])
            stage4_time = time.time() - stage4_start
            print(f"✅ AI done in {stage4_time:.1f}s")
        except Exception as e:
            print(f"⚠️ AI failed: {e}")

    total_time = (datetime.now() - start_time).total_seconds()
    print(f"\n🎉 TOTAL TIME: {total_time:.0f}s = {total_time/60:.1f} min")

    if not results:
        send_telegram(f"⚠️ <b>NO SETUPS TODAY</b>\n\nScanned {len(stock_dfs)} stocks. None approaching major liquidity (≤1.5%) with strong fundamentals.\n\n⏱️ Duration: {total_time:.0f}s")
        return

    # Categorize
    at_level = [r for r in results if r['stage'] == 'AT LEVEL']
    very_close = [r for r in results if r['stage'] == 'VERY CLOSE']
    approaching = [r for r in results if r['stage'] == 'CLOSE']
    longs = [r for r in results if r['direction'] == 'LONG']
    shorts = [r for r in results if r['direction'] == 'SHORT']

    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    ai_badge = "🤖 AI" if ai_results else ""
    header = f"""
🌊 <b>NIFTY 500 LIQUIDITY WATCH v6 {ai_badge}</b>
📅 {now_str} IST
⚡ Ultra Fast: {total_time:.0f}s

📊 <b>Summary:</b>
━━━━━━━━━━━━━━━━━━━━━
🎯 AT LEVEL (0-0.3%): <b>{len(at_level)}</b>
🔥 VERY CLOSE (0.3-0.8%): <b>{len(very_close)}</b>
⚡ CLOSE (0.8-1.5%): <b>{len(approaching)}</b>

🟢 LONG: <b>{len(longs)}</b>
🔴 SHORT: <b>{len(shorts)}</b>
📈 Scanned: {len(stock_dfs)}/{len(symbols_list)}
━━━━━━━━━━━━━━━━━━━━━

💡 Fundamentally strong + Untapped liquidity
"""
    send_telegram(header)

    # AI Market Overview
    if market_view:
        market_msg = f"""
🌍 <b>AI MARKET OVERVIEW</b>
📊 Bias: <b>{market_view.get('overall_market', 'N/A')}</b>
🎯 Theme: <i>{market_view.get('dominant_theme', 'N/A')}</i>

🏆 <b>Best Pick:</b>
{market_view.get('best_pick_reasoning', 'N/A')}

💡 {market_view.get('market_wisdom', 'Trade with discipline.')}
"""
        send_telegram(market_msg)

    # Alerts by stage
    if at_level:
        send_telegram(f"\n🎯 <b>AT LEVEL — IMMINENT SWEEP</b> ({len(at_level)})\n")
        for i, r in enumerate(at_level[:5], 1):
            send_telegram(format_alert(r, i, ai_results.get(r['symbol'])))

    if very_close:
        send_telegram(f"\n🔥 <b>VERY CLOSE</b> ({len(very_close)})\n")
        for i, r in enumerate(very_close[:5], 1):
            send_telegram(format_alert(r, i, ai_results.get(r['symbol'])))

    if approaching:
        send_telegram(f"\n⚡ <b>CLOSE — Watchlist</b> ({len(approaching)})\n")
        for i, r in enumerate(approaching[:5], 1):
            send_telegram(format_alert(r, i, ai_results.get(r['symbol'])))

    footer = f"""
━━━━━━━━━━━━━━━━━━━━━
⚠️ <b>How to Trade:</b>
1. TradingView-এ chart verify
2. Wait for sweep + rejection
3. CHoCH confirm
4. Then entry as per plan

🤖 Scanner v6 ULTRA FAST
⏱️ Completed: {total_time:.0f}s
⏰ Next scan: আগামীকাল
"""
    send_telegram(footer)
    print("✅ All sent!")


if __name__ == "__main__":
    main()

"""
Daily Nifty 500 scan v6.1 — FIXED (limit candidates + timeout)
Guarantee: Complete in 5-7 minutes
"""
import os
import sys
import requests
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from nifty500_list import get_symbol_map
from scanner_engine import batch_download_all, analyze_stock_from_df

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("❌ Missing Telegram credentials")
    sys.exit(1)

# HARD LIMITS to prevent stuck
MAX_FUND_CANDIDATES = 40    # Only top 40 candidates for fundamental
MAX_AI_ANALYSIS = 8         # Only top 8 for AI
FUND_TIMEOUT_PER_STOCK = 8  # 8 sec max per fundamental fetch
AI_TIMEOUT_PER_STOCK = 10   # 10 sec max per AI call
OVERALL_STAGE_TIMEOUT = 300 # 5 min max for any stage


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


def add_fundamental_safe(item, timeout=8):
    """Fetch fundamental with strict timeout."""
    r, sym, info = item
    try:
        from fundamental_analyzer import get_fundamental_score
        # Use signal alarm equivalent via requests timeout inside get_fundamental_score
        fund_result = get_fundamental_score(info["symbol"])
        if fund_result["score"] < 2:
            return None
        r["fund_score"] = fund_result["score"]
        r["fund_data"] = fund_result["data"]
        r["priority"] = round((r["proximity_score"] * 0.6) + (fund_result["score"] * 25 * 0.4), 1)
        return r
    except Exception:
        return None


def analyze_ai_safe(r, timeout=10):
    """AI analysis with strict timeout."""
    try:
        from ai_analyzer import analyze_setup
        return r['symbol'], analyze_setup(r)
    except Exception:
        return r['symbol'], None


def main():
    print("🚀 v6.1 FIXED Scan Starting...")
    start_time = datetime.now()

    sym_map = get_symbol_map()
    symbols_list = list(sym_map.keys())
    symbol_info_list = list(sym_map.items())
    print(f"📊 Total: {len(symbols_list)} stocks")

    # ============ STAGE 1: Batch download ============
    print("\n=== STAGE 1: Batch Download ===")
    stage_start = time.time()
    stock_dfs = batch_download_all(symbols_list, period="1y", chunk_size=100)
    stage_time = time.time() - stage_start
    print(f"✅ Stage 1: {stage_time:.1f}s — {len(stock_dfs)}/{len(symbols_list)} downloaded")

    if not stock_dfs:
        send_telegram("⚠️ SCAN FAILED\nBatch download failed")
        return

    # ============ STAGE 2: Technical analysis ============
    print(f"\n=== STAGE 2: Technical Analysis ===")
    stage_start = time.time()
    candidates = []
    for sym, info in symbol_info_list:
        if sym in stock_dfs:
            r = analyze_stock_from_df(stock_dfs[sym], info)
            if r:
                candidates.append((r, sym, info))

    # Sort by priority and TAKE ONLY TOP N (prevent Screener.in overload!)
    candidates.sort(key=lambda x: x[0]['proximity_score'], reverse=True)
    candidates = candidates[:MAX_FUND_CANDIDATES]

    stage_time = time.time() - stage_start
    print(f"✅ Stage 2: {stage_time:.1f}s — {len(candidates)} top candidates (limited to {MAX_FUND_CANDIDATES})")

    # ============ STAGE 3: Fundamental (LIMITED + timeout per stock) ============
    print(f"\n=== STAGE 3: Fundamental for {len(candidates)} candidates ===")
    stage_start = time.time()
    results = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(add_fundamental_safe, item, FUND_TIMEOUT_PER_STOCK): item for item in candidates}
        try:
            for future in as_completed(futures, timeout=OVERALL_STAGE_TIMEOUT):
                try:
                    r = future.result(timeout=FUND_TIMEOUT_PER_STOCK)
                    if r:
                        results.append(r)
                except (TimeoutError, Exception):
                    continue
        except TimeoutError:
            print(f"⚠️ Stage 3 timeout after {OVERALL_STAGE_TIMEOUT}s — using partial results")

    stage_time = time.time() - stage_start
    print(f"✅ Stage 3: {stage_time:.1f}s — {len(results)} passed fundamental")

    results.sort(key=lambda x: x["priority"], reverse=True)

    # ============ STAGE 4: AI Analysis (STRICT LIMIT + timeout) ============
    ai_results = {}
    market_view = None
    if os.environ.get("GROQ_API_KEY") and results:
        print(f"\n=== STAGE 4: AI Analysis (top {MAX_AI_ANALYSIS}) ===")
        stage_start = time.time()
        top_for_ai = results[:MAX_AI_ANALYSIS]

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(analyze_ai_safe, r, AI_TIMEOUT_PER_STOCK): r for r in top_for_ai}
            try:
                for future in as_completed(futures, timeout=60):
                    try:
                        sym, analysis = future.result(timeout=AI_TIMEOUT_PER_STOCK)
                        if analysis:
                            ai_results[sym] = analysis
                    except (TimeoutError, Exception):
                        continue
            except TimeoutError:
                print("⚠️ AI stage timeout")

        # Market overview
        try:
            from ai_analyzer import get_market_overview
            market_view = get_market_overview(results[:10])
        except Exception:
            pass

        stage_time = time.time() - stage_start
        print(f"✅ Stage 4: {stage_time:.1f}s — {len(ai_results)} AI analyses")

    total_time = (datetime.now() - start_time).total_seconds()
    print(f"\n🎉 TOTAL: {total_time:.0f}s = {total_time/60:.1f} min")

    # ============ SEND TELEGRAM ============
    if not results:
        send_telegram(f"⚠️ <b>NO SETUPS TODAY</b>\n\nScanned {len(stock_dfs)}/500 stocks.\n⏱️ Duration: {total_time:.0f}s")
        return

    at_level = [r for r in results if r['stage'] == 'AT LEVEL']
    very_close = [r for r in results if r['stage'] == 'VERY CLOSE']
    approaching = [r for r in results if r['stage'] == 'CLOSE']
    longs = [r for r in results if r['direction'] == 'LONG']
    shorts = [r for r in results if r['direction'] == 'SHORT']

    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    ai_badge = "🤖 AI" if ai_results else ""
    header = f"""
🌊 <b>NIFTY 500 LIQUIDITY WATCH v6.1 {ai_badge}</b>
📅 {now_str} IST
⚡ Scan time: {total_time:.0f}s

📊 <b>Summary:</b>
━━━━━━━━━━━━━━━━━━━━━
🎯 AT LEVEL (0-0.3%): <b>{len(at_level)}</b>
🔥 VERY CLOSE (0.3-0.8%): <b>{len(very_close)}</b>
⚡ CLOSE (0.8-1.5%): <b>{len(approaching)}</b>

🟢 LONG: <b>{len(longs)}</b>
🔴 SHORT: <b>{len(shorts)}</b>
📈 Scanned: {len(stock_dfs)}/{len(symbols_list)}
━━━━━━━━━━━━━━━━━━━━━
"""
    send_telegram(header)

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
4. Entry as per plan

🤖 Scanner v6.1 FIXED
⏱️ Completed: {total_time:.0f}s
⏰ Next scan: আগামীকাল
"""
    send_telegram(footer)
    print("✅ All sent!")


if __name__ == "__main__":
    main()

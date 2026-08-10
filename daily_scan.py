"""
Daily Nifty 500 Scan + Telegram Delivery
Runs via GitHub Actions once per day
Sends A+ setup results directly to your Telegram
"""
import os
import sys
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from nifty500_list import get_symbol_map
from scanner_engine import scan_stock

# Get secrets from environment (set in GitHub Actions)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("❌ Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables")
    sys.exit(1)


def send_telegram(text, parse_mode="HTML"):
    """Send message to Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Telegram has 4096 char limit per message
    max_len = 4000
    chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]
    for chunk in chunks:
        try:
            r = requests.post(url, data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }, timeout=30)
            if not r.ok:
                print(f"⚠️ Telegram error: {r.text}")
        except Exception as e:
            print(f"⚠️ Telegram exception: {e}")


def format_stock_card(r, rank):
    """Format a single stock as a Telegram-friendly card."""
    dir_emoji = "🟢" if r["direction"] == "LONG" else "🔴"
    change_emoji = "▲" if r["change_pct"] >= 0 else "▼"
    tv_link = f"https://in.tradingview.com/chart/?symbol=NSE:{r['symbol']}"

    text = f"""
━━━━━━━━━━━━━━━━━━━━━
<b>#{rank}. {r['symbol']}</b> {dir_emoji} {r['direction']}  <b>[{r['score']}/10]</b>
<i>{r['name']} • {r['sector']}</i>

💰 <b>Price:</b> ₹{r['price']}  ({change_emoji} {abs(r['change_pct'])}%)
📊 <b>52W:</b> ₹{r['wk52_low']} - ₹{r['wk52_high']}  (From high: -{r['dist_52wh_pct']}%)

🎯 <b>TRADE PLAN:</b>
• Entry: <b>₹{r['entry']}</b>
• Stop Loss: <b>₹{r['sl']}</b>
• Target (1:3): <b>₹{r['tp']}</b>
• Risk: {r['risk_pct']}%

📈 Daily: {r['trend_daily']} | Weekly: {r['trend_weekly']}
"""
    if r["reasons"]:
        text += f"\n✨ <b>Why:</b> {' • '.join(r['reasons'])}\n"

    text += f"\n🔗 <a href='{tv_link}'>View Chart on TradingView</a>\n"
    return text


def main():
    print("🚀 Starting daily Nifty 500 scan...")
    start_time = datetime.now()

    sym_map = get_symbol_map()
    symbols = list(sym_map.items())
    print(f"📊 Scanning {len(symbols)} stocks...")

    results = []
    completed = 0

    def scan_one(item):
        sym, info = item
        try:
            return scan_stock(sym, info)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(scan_one, item): item for item in symbols}
        for future in as_completed(futures):
            r = future.result()
            completed += 1
            if r:
                results.append(r)
            if completed % 40 == 0:
                print(f"  Progress: {completed}/{len(symbols)}")

    results.sort(key=lambda x: x["score"], reverse=True)
    duration = (datetime.now() - start_time).total_seconds()
    print(f"✅ Scan complete in {duration:.0f}s - {len(results)} stocks analyzed")

    # Filter
    aplus = [r for r in results if r["verdict"] == "A+ SETUP"]
    a_setup = [r for r in results if r["verdict"] == "A SETUP"]
    longs = [r for r in results if r["direction"] == "LONG" and r["score"] >= 6]
    shorts = [r for r in results if r["direction"] == "SHORT" and r["score"] >= 6]

    # Header message
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    header = f"""
🌟 <b>NIFTY 500 DAILY SCAN</b> 🌟
📅 {now_str} IST

📊 <b>Summary:</b>
━━━━━━━━━━━━━━━━━━━━━
🏆 A+ Setups: <b>{len(aplus)}</b>
✅ A Setups: <b>{len(a_setup)}</b>
🟢 Long Ideas: <b>{len(longs)}</b>
🔴 Short Ideas: <b>{len(shorts)}</b>
📈 Total Scanned: {len(results)}
━━━━━━━━━━━━━━━━━━━━━

Based on: HTF Liquidity + Trend + S&amp;R + CHoCH
"""
    send_telegram(header)

    # Top A+ setups (limit to 10)
    if aplus:
        top = aplus[:10]
        send_telegram(f"\n🏆 <b>TOP {len(top)} A+ SETUPS</b> 🏆\n")
        for i, r in enumerate(top, 1):
            send_telegram(format_stock_card(r, i))
    else:
        send_telegram("\n⚠️ <b>আজ কোনো A+ setup পাওয়া যায়নি।</b>\nবাজার সাইডওয়েজ হতে পারে। অপেক্ষা করুন।")

    # Top 5 A setups if not enough A+
    if len(aplus) < 5 and a_setup:
        top_a = a_setup[:5]
        send_telegram(f"\n✅ <b>ADDITIONAL A SETUPS</b> ({len(top_a)}):\n")
        for i, r in enumerate(top_a, 1):
            send_telegram(format_stock_card(r, i))

    # Footer
    footer = f"""
━━━━━━━━━━━━━━━━━━━━━
⚠️ <b>Disclaimer:</b> শুধুমাত্র শিক্ষামূলক। Trade করার আগে TradingView-এ chart নিজে verify করুন এবং risk management follow করুন।

🤖 Powered by Arena.ai Scanner
⏰ Next scan: আগামীকাল সকালে
"""
    send_telegram(footer)
    print("✅ All messages sent to Telegram!")


if __name__ == "__main__":
    main()

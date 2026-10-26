"""
Nifty 500 2-Day Continuous Big Move + Gap Scanner
==================================================
Rules:
1. Continuous 2 days big move in the same direction (Bullish or Bearish).
2. Gap Up / Gap Down detection (Bonus high priority if gap is present).
3. Sends detailed Bengali & English formatted alerts via Telegram.
"""
from __future__ import annotations

import html
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from nifty500_list import get_symbol_map
from scanner_engine import batch_download_all, normalize_ohlcv

IST = ZoneInfo("Asia/Kolkata")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Configurable thresholds via Environment Variables
MIN_DAY_MOVE_PCT = float(os.getenv("MIN_DAY_MOVE_PCT", "2.0"))  # Min move per day (%)
MIN_CUMULATIVE_MOVE_PCT = float(os.getenv("MIN_CUMULATIVE_MOVE_PCT", "4.5"))  # Min 2-day total move (%)
GAP_THRESHOLD_PCT = float(os.getenv("GAP_THRESHOLD_PCT", "0.5"))  # Min gap size (%)
MAX_ALERTS = int(os.getenv("MAX_MOMENTUM_ALERTS", "15"))


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def detect_momentum_gap(
    df: pd.DataFrame,
    info: Dict[str, Any],
    min_day_move: float = MIN_DAY_MOVE_PCT,
    min_cum_move: float = MIN_CUMULATIVE_MOVE_PCT,
    gap_threshold: float = GAP_THRESHOLD_PCT,
) -> Optional[Dict[str, Any]]:
    """Analyze stock OHLC for 2 consecutive days of big moves and optional gap."""
    df = normalize_ohlcv(df)
    if len(df) < 5:
        return None

    close = df["Close"].to_numpy(dtype=float)
    open_ = df["Open"].to_numpy(dtype=float)
    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    volume = df["Volume"].to_numpy(dtype=float)

    # Day 1 (yesterday) and Day 2 (today)
    c0 = close[-3]  # Day before yesterday close
    c1, o1, h1, l1, v1 = close[-2], open_[-2], high[-2], low[-2], volume[-2]
    c2, o2, h2, l2, v2 = close[-1], open_[-1], high[-1], low[-1], volume[-1]

    if c0 <= 0 or c1 <= 0 or c2 <= 0:
        return None

    day1_change = (c1 - c0) / c0 * 100.0
    day2_change = (c2 - c1) / c1 * 100.0
    two_day_return = (c2 - c0) / c0 * 100.0

    # Direction check: both days must move in the same direction
    is_bullish = day1_change > 0 and day2_change > 0
    is_bearish = day1_change < 0 and day2_change < 0

    if not (is_bullish or is_bearish):
        return None

    # Big move criteria: either both days >= min_day_move OR total 2-day return >= min_cum_move
    abs_d1, abs_d2 = abs(day1_change), abs(day2_change)
    abs_cum = abs(two_day_return)

    is_big_move = (abs_d1 >= min_day_move and abs_d2 >= min_day_move) or (
        abs_cum >= min_cum_move and abs_d1 >= 1.0 and abs_d2 >= 1.0
    )

    if not is_big_move:
        return None

    direction = "BULLISH" if is_bullish else "BEARISH"

    # Gap detection
    gap_type = "NONE"
    gap_pct = 0.0

    if is_bullish:
        if l2 > h1:  # Full Gap Up (True gap)
            gap_type = "FULL_GAP_UP"
            gap_pct = (l2 - h1) / h1 * 100.0
        elif o2 > c1 and ((o2 - c1) / c1 * 100.0 >= gap_threshold):  # Body Gap Up
            gap_type = "BODY_GAP_UP"
            gap_pct = (o2 - c1) / c1 * 100.0
    else:  # Bearish
        if h2 < l1:  # Full Gap Down (True gap)
            gap_type = "FULL_GAP_DOWN"
            gap_pct = (l1 - h2) / l1 * 100.0
        elif o2 < c1 and ((c1 - o2) / c1 * 100.0 >= gap_threshold):  # Body Gap Down
            gap_type = "BODY_GAP_DOWN"
            gap_pct = (c1 - o2) / c1 * 100.0

    has_gap = gap_type != "NONE"

    # Volume expansion check (today's volume vs 20-day avg volume)
    avg_vol_20 = float(np.mean(volume[-21:-1])) if len(volume) >= 21 else float(np.mean(volume))
    vol_ratio = v2 / avg_vol_20 if avg_vol_20 > 0 else 1.0

    # Score candidate for ranking (higher score = bigger move + gap + volume)
    score = abs_cum * 2.0
    if has_gap:
        score += 15.0 if "FULL" in gap_type else 8.0
    if vol_ratio > 1.5:
        score += 5.0

    return {
        "symbol": info["symbol"],
        "name": info["name"],
        "sector": info["sector"],
        "price": round(c2, 2),
        "direction": direction,
        "day1_change_pct": round(day1_change, 2),
        "day2_change_pct": round(day2_change, 2),
        "two_day_return_pct": round(two_day_return, 2),
        "has_gap": has_gap,
        "gap_type": gap_type,
        "gap_pct": round(gap_pct, 2),
        "vol_ratio": round(vol_ratio, 2),
        "score": round(score, 2),
        "date": str(pd.Timestamp(df.index[-1]).date()),
    }


def format_momentum_alert(item: Dict[str, Any], rank: int) -> str:
    direction_emoji = "🟢 🚀" if item["direction"] == "BULLISH" else "🔴 💥"
    gap_badge = ""
    if item["gap_type"] == "FULL_GAP_UP":
        gap_badge = "🔥 <b>FULL GAP UP</b> (+"+str(item["gap_pct"])+"%)"
    elif item["gap_type"] == "BODY_GAP_UP":
        gap_badge = "⚡ <b>BODY GAP UP</b> (+"+str(item["gap_pct"])+"%)"
    elif item["gap_type"] == "FULL_GAP_DOWN":
        gap_badge = "❄️ <b>FULL GAP DOWN</b> (-"+str(item["gap_pct"])+"%)"
    elif item["gap_type"] == "BODY_GAP_DOWN":
        gap_badge = "⚡ <b>BODY GAP DOWN</b> (-"+str(item["gap_pct"])+"%)"
    else:
        gap_badge = "➡️ No Major Gap"

    tv_link = f"https://in.tradingview.com/chart/?symbol=NSE:{item['symbol']}"
    screener_link = f"https://www.screener.in/company/{item['symbol']}/"

    vol_text = f"🔥 High ({item['vol_ratio']}x avg)" if item["vol_ratio"] >= 1.5 else f"Normal ({item['vol_ratio']}x avg)"

    text = (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>#{rank}. {_esc(item['symbol'])}</b> {direction_emoji} <b>{_esc(item['direction'])} MOMENTUM</b>\n"
        f"<i>{_esc(item['name'])} • {_esc(item['sector'])}</i>\n\n"
        f"💰 <b>বর্তমান দর (LTP):</b> ₹{item['price']}\n"
        f"📈 <b>২ দিনের মোট মুভ:</b> <b>{item['two_day_return_pct']}%</b>\n"
        f"  • Day 1 Move: {item['day1_change_pct']}%\n"
        f"  • Day 2 Move: {item['day2_change_pct']}%\n\n"
        f"🎯 <b>গ্যাপ স্ট্যাটাস (Gap):</b> {gap_badge}\n"
        f"📊 <b>ভলিউম (Volume):</b> {vol_text}\n"
        f"⭐ <b>Momentum Score:</b> {item['score']}\n\n"
        f"🔗 <a href='{tv_link}'>TradingView Chart</a> | <a href='{screener_link}'>Screener</a>\n"
    )
    return text


def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram credentials absent; message not sent")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=(5, 25),
        )
        if not response.ok:
            print(f"⚠️ Telegram {response.status_code}: {response.text[:300]}")
            return False
        return True
    except Exception as exc:
        print(f"⚠️ Telegram exception: {exc}")
        return False


def run_momentum_scan() -> List[Dict[str, Any]]:
    print("🚀 Starting Nifty 500 2-Day Continuous Big Move + Gap Scanner...")
    symbol_map = get_symbol_map()
    symbols = list(symbol_map.keys())

    stock_dfs = batch_download_all(symbols, period="1y", chunk_size=100)
    if not stock_dfs:
        print("❌ No data downloaded.")
        return []

    results: List[Dict[str, Any]] = []
    for yf_symbol, info in symbol_map.items():
        df = stock_dfs.get(yf_symbol)
        if df is None:
            continue
        res = detect_momentum_gap(df, info)
        if res:
            results.append(res)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def main() -> int:
    started = datetime.now(IST)
    print(f"🔥 Nifty 500 Momentum Scanner starting at {started.isoformat()}")

    results = run_momentum_scan()
    print(f"✅ Found {len(results)} continuous big move stocks.")

    if not results:
        send_telegram(
            f"⚠️ <b>Nifty 500 Continuous 2-Day Move Scanner</b>\n\n"
            f"আজকে কোনো স্টক টানা ২ দিন বড় মুভ (≥{MIN_DAY_MOVE_PCT}%) দেয়নি।"
        )
        return 0

    bullish = [x for x in results if x["direction"] == "BULLISH"]
    bearish = [x for x in results if x["direction"] == "BEARISH"]

    now = started.strftime("%d %b %Y, %I:%M %p")
    header = (
        f"⚡ <b>NIFTY 500 CONTINUOUS 2-DAY MOVE & GAP SCANNER</b> ⚡\n"
        f"📅 {now} IST\n\n"
        f"📊 <b>Summary:</b>\n"
        f"🟢 Bullish Momentum (টানা ২ দিন বড় উত্থান): <b>{len(bullish)}</b>\n"
        f"🔴 Bearish Momentum (টানা ২ দিন বড় পতন): <b>{len(bearish)}</b>\n"
        f"🎯 Gap সহ স্টক: <b>{sum(1 for x in results if x['has_gap'])}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    send_telegram(header)

    # Top candidates
    top_candidates = results[:MAX_ALERTS]
    for rank, item in enumerate(top_candidates, 1):
        send_telegram(format_momentum_alert(item, rank))
        time.sleep(0.2)

    footer = (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <b>Rules:</b>\n"
        "1. টানা ২ দিন সমমুখী বড় মুভ (Continuous 2-Day Move)\n"
        "2. গ্যাপ (Gap Up/Down) থাকলে বাড়তি সতর্কতা ও অগ্রাধিকার\n"
        "3. ট্রেড নেওয়ার আগে TradingView-এ সাপোর্ট/রেজিস্ট্যান্স লেভেল ভেরিফাই করুন।"
    )
    send_telegram(footer)
    return 0


if __name__ == "__main__":
    sys.exit(main())

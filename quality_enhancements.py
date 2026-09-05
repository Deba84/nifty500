"""Market-regime, liquidity and data-quality features for the scanner.

The core SMC engine remains deterministic.  This module adds cross-sectional
context and execution-quality penalties without introducing a predictive
black box or look-ahead data.
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


MIN_AVG_DAILY_TURNOVER = float(os.getenv("MIN_AVG_DAILY_TURNOVER", "10000000"))
MIN_PRICE = float(os.getenv("MIN_TRADABLE_PRICE", "20"))
MIN_RELATIVE_VOLUME = float(os.getenv("MIN_RELATIVE_VOLUME", "0.50"))
MAX_GAP_PCT = float(os.getenv("MAX_GAP_PCT", "4.0"))
MAX_ATR_PCT = float(os.getenv("MAX_ATR_PCT", "8.0"))
REGIME_PENALTY = int(os.getenv("COUNTER_REGIME_PENALTY", "5"))
SECTOR_PENALTY = int(os.getenv("COUNTER_SECTOR_PENALTY", "3"))


def _close_return(close: pd.Series, bars: int) -> Optional[float]:
    if len(close) <= bars:
        return None
    start = float(close.iloc[-bars - 1])
    end = float(close.iloc[-1])
    if start <= 0 or not np.isfinite(start) or not np.isfinite(end):
        return None
    return end / start - 1.0


def _trend(close: pd.Series, fast: int = 20, slow: int = 50) -> str:
    if len(close) < slow:
        return "Unknown"
    fast_mean = float(close.tail(fast).mean())
    slow_mean = float(close.tail(slow).mean())
    current = float(close.iloc[-1])
    if current > fast_mean > slow_mean:
        return "Uptrend"
    if current < fast_mean < slow_mean:
        return "Downtrend"
    return "Sideways"


def build_market_context(
    stock_dfs: Mapping[str, pd.DataFrame],
    symbol_map: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Build a market/sector snapshot from the same completed daily bars.

    This is deliberately cross-sectional rather than an invented index proxy.
    Every stock is evaluated only through the last completed row available in
    its downloaded dataframe.
    """
    rows = []
    for symbol, df in stock_dfs.items():
        if df is None or df.empty or "Close" not in df:
            continue
        close = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if len(close) < 50:
            continue
        info = symbol_map.get(symbol, {})
        ret20 = _close_return(close, 20)
        rows.append(
            {
                "symbol": symbol,
                "sector": info.get("sector", "Unknown"),
                "trend": _trend(close),
                "ret20": ret20,
                "above_20": float(close.iloc[-1]) > float(close.tail(20).mean()),
                "above_50": float(close.iloc[-1]) > float(close.tail(50).mean()),
            }
        )

    if not rows:
        return {
            "sample_size": 0,
            "breadth": {"above_20_pct": None, "above_50_pct": None, "trend": "Unknown"},
            "sector_breadth": {},
            "median_return_20": None,
        }

    total = len(rows)
    above20 = sum(bool(row["above_20"]) for row in rows)
    above50 = sum(bool(row["above_50"]) for row in rows)
    returns = [row["ret20"] for row in rows if row["ret20"] is not None]
    sector_rows: Dict[str, list] = defaultdict(list)
    for row in rows:
        sector_rows[str(row["sector"])].append(row)

    sector_breadth = {}
    for sector, members in sector_rows.items():
        sector_breadth[sector] = {
            "sample_size": len(members),
            "above_20_pct": round(100 * sum(x["above_20"] for x in members) / len(members), 1),
            "above_50_pct": round(100 * sum(x["above_50"] for x in members) / len(members), 1),
            "median_return_20": round(
                100 * float(np.median([x["ret20"] for x in members if x["ret20"] is not None])),
                2,
            )
            if any(x["ret20"] is not None for x in members)
            else None,
        }

    breadth20 = 100 * above20 / total
    breadth50 = 100 * above50 / total
    market_trend = (
        "BULLISH" if breadth20 >= 60 and breadth50 >= 55
        else "BEARISH" if breadth20 <= 40 and breadth50 <= 45
        else "MIXED"
    )
    return {
        "sample_size": total,
        "breadth": {
            "above_20_pct": round(breadth20, 1),
            "above_50_pct": round(breadth50, 1),
            "trend": market_trend,
        },
        "sector_breadth": sector_breadth,
        "median_return_20": round(100 * float(np.median(returns)), 2) if returns else None,
    }


def execution_quality(
    df: pd.DataFrame,
    direction: str,
    sector: str = "Unknown",
    market_context: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, Any], int, list[str], bool]:
    """Return features, deterministic penalty, flags and eligibility.

    Positive evidence is reported, but only objectively poor conditions reduce
    the setup score.  This prevents a noisy proxy from inflating a technical
    signal score.
    """
    recent = df.copy()
    close = pd.to_numeric(recent["Close"], errors="coerce")
    high = pd.to_numeric(recent["High"], errors="coerce")
    low = pd.to_numeric(recent["Low"], errors="coerce")
    open_ = pd.to_numeric(recent["Open"], errors="coerce")
    volume = pd.to_numeric(recent.get("Volume", 0), errors="coerce").fillna(0.0)
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = float(true_range.tail(14).mean()) if len(true_range.dropna()) else 0.0
    current = float(close.iloc[-1])
    avg_volume20 = float(volume.tail(20).mean()) if len(volume) else 0.0
    avg_turnover20 = float((close * volume).tail(20).mean()) if len(volume) else 0.0
    relative_volume = float(volume.iloc[-1] / avg_volume20) if avg_volume20 > 0 else None
    atr_pct = atr14 / current * 100 if current > 0 else None
    gap_pct = (
        abs(float(open_.iloc[-1]) - float(prev_close.iloc[-1])) / float(prev_close.iloc[-1]) * 100
        if len(close) > 1 and float(prev_close.iloc[-1]) > 0
        else None
    )
    ret20 = _close_return(close, 20)

    features: Dict[str, Any] = {
        "avg_volume_20": round(avg_volume20, 2),
        "avg_daily_turnover": round(avg_turnover20, 2),
        "relative_volume": round(relative_volume, 2) if relative_volume is not None else None,
        "atr_14_pct": round(atr_pct, 2) if atr_pct is not None else None,
        "gap_pct": round(gap_pct, 2) if gap_pct is not None else None,
        "return_20_pct": round(100 * ret20, 2) if ret20 is not None else None,
        "trend": _trend(close),
    }
    penalty = 0
    flags: list[str] = []
    eligible = True

    if current < MIN_PRICE:
        flags.append("LOW_PRICE")
        penalty += 2
    if avg_turnover20 > 0 and avg_turnover20 < MIN_AVG_DAILY_TURNOVER:
        flags.append("LOW_LIQUIDITY")
        penalty += 8
    if relative_volume is not None and relative_volume < MIN_RELATIVE_VOLUME:
        flags.append("LOW_RELATIVE_VOLUME")
        penalty += 3
    if gap_pct is not None and gap_pct > MAX_GAP_PCT:
        flags.append("GAP_RISK")
        penalty += 5
    if atr_pct is not None and atr_pct > MAX_ATR_PCT:
        flags.append("HIGH_VOLATILITY")
        penalty += 3
    if avg_turnover20 <= 0 and volume.sum() > 0:
        flags.append("TURNOVER_UNKNOWN")
        penalty += 2

    context = market_context or {}
    breadth = context.get("breadth", {}) or {}
    market_trend = breadth.get("trend", "Unknown")
    if (direction == "LONG" and market_trend == "BEARISH") or (
        direction == "SHORT" and market_trend == "BULLISH"
    ):
        flags.append("COUNTER_MARKET_REGIME")
        penalty += REGIME_PENALTY

    sector_data = (context.get("sector_breadth", {}) or {}).get(sector, {}) or {}
    sector20 = sector_data.get("above_20_pct")
    if sector20 is not None and (
        (direction == "LONG" and sector20 < 40)
        or (direction == "SHORT" and sector20 > 60)
    ):
        flags.append("COUNTER_SECTOR_REGIME")
        penalty += SECTOR_PENALTY

    if not np.isfinite(current) or current <= 0 or not np.isfinite(atr14):
        flags.append("INVALID_EXECUTION_DATA")
        eligible = False

    return features, min(20, penalty), sorted(set(flags)), eligible
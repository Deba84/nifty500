"""
Nifty 500 Liquidity Scanner v6.2
=================================
Deterministic SMC + price-action engine.

Design principles
-----------------
* Batch data only: no per-stock Yahoo calls.
* Pre-sweep alerts remain watch alerts, never entries.
* A second price-action state machine detects sweep -> reclaim ->
  displacement -> CHoCH.
* A level is "untapped" only when every candle since its formation has
  been checked, not merely the last N candles.
* Traditional indicators are not used.  All features are derived directly
  from OHLC structure.
"""
from __future__ import annotations

import math
import os
import time
import warnings
from collections import Counter
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ----------------------------- Configuration -----------------------------
MAX_DISTANCE_PCT = float(os.getenv("MAX_LIQ_DISTANCE_PCT", "1.5"))
MIN_RR = float(os.getenv("MIN_RR", "2.0"))
SWEEP_BUFFER_PCT = float(os.getenv("SWEEP_BUFFER_PCT", "0.002"))  # 0.20%
NEAR_TEST_PCT = float(os.getenv("NEAR_TEST_PCT", "0.25"))
EQUAL_LEVEL_TOLERANCE_PCT = float(os.getenv("EQUAL_LEVEL_TOLERANCE_PCT", "0.50"))
CONFLUENCE_TOLERANCE_PCT = float(os.getenv("CONFLUENCE_TOLERANCE_PCT", "0.35"))
MIN_HISTORY_BARS = int(os.getenv("MIN_HISTORY_BARS", "120"))
CONFIRMATION_LOOKBACK = int(os.getenv("CONFIRMATION_LOOKBACK", "6"))
RECLAIM_WINDOW = int(os.getenv("RECLAIM_WINDOW", "2"))
CHOCH_WINDOW = int(os.getenv("CHOCH_WINDOW", "5"))
REFERENCE_SL_BUFFER_PCT = float(os.getenv("REFERENCE_SL_BUFFER_PCT", "0.015"))

TYPE_POINTS = {
    "EQUAL_HIGHS": 8,
    "EQUAL_LOWS": 8,
    "52W_HIGH": 8,
    "52W_LOW": 8,
    "PMH": 7,
    "PML": 7,
    "PWH": 6,
    "PWL": 6,
    "SWING_HIGH": 5,
    "SWING_LOW": 5,
}

STATE_PRIORITY = {
    "TRIGGER_READY": 4,
    "CHOCH_WAIT_DISPLACEMENT": 3,
    "RECLAIMED_WAIT_CHOCH": 2,
    "SWEPT_WAIT_RECLAIM": 1,
    "PRE_SWEEP": 0,
}


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _f(value: Any, default: float = 0.0) -> float:
    return float(value) if _finite(value) else default


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Return a sorted, numeric, single-ticker OHLCV dataframe."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()

    out = df.copy()
    # A single ticker occasionally retains a redundant MultiIndex.
    if isinstance(out.columns, pd.MultiIndex):
        if len(out.columns.levels) >= 2:
            # Prefer the level containing OHLC labels.
            level0 = set(map(str, out.columns.get_level_values(0)))
            level1 = set(map(str, out.columns.get_level_values(1)))
            wanted = {"Open", "High", "Low", "Close", "Volume"}
            if wanted.intersection(level0):
                out.columns = out.columns.get_level_values(0)
            elif wanted.intersection(level1):
                out.columns = out.columns.get_level_values(1)

    required = ["Open", "High", "Low", "Close"]
    if not all(c in out.columns for c in required):
        return pd.DataFrame()
    if "Volume" not in out.columns:
        out["Volume"] = 0.0

    out = out[["Open", "High", "Low", "Close", "Volume"]]
    for col in out.columns:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=required)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out[(out[required] > 0).all(axis=1)]
    return out


# ----------------------------- Market structure -----------------------------
def find_swings(df: pd.DataFrame, lookback: int = 5) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """Return confirmed swing highs/lows as (integer_position, price)."""
    if df is None or len(df) < (lookback * 2 + 1):
        return [], []
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    swing_highs: List[Tuple[int, float]] = []
    swing_lows: List[Tuple[int, float]] = []

    for i in range(lookback, len(df) - lookback):
        high_window = highs[i - lookback : i + lookback + 1]
        low_window = lows[i - lookback : i + lookback + 1]
        # Equality is intentional: equal liquidity can contain equal pivots.
        if highs[i] >= np.nanmax(high_window):
            swing_highs.append((i, float(highs[i])))
        if lows[i] <= np.nanmin(low_window):
            swing_lows.append((i, float(lows[i])))
    return swing_highs, swing_lows


def detect_trend(df: pd.DataFrame, lookback: int = 5) -> str:
    """Structure-only trend label (HH/HL/LH/LL), with no indicator."""
    highs, lows = find_swings(df, lookback)
    if len(highs) < 2 or len(lows) < 2:
        return "Unclear"
    h = [x[1] for x in highs[-3:]]
    l = [x[1] for x in lows[-3:]]
    hh = all(h[i] < h[i + 1] for i in range(len(h) - 1))
    hl = all(l[i] < l[i + 1] for i in range(len(l) - 1))
    lh = all(h[i] > h[i + 1] for i in range(len(h) - 1))
    ll = all(l[i] > l[i + 1] for i in range(len(l) - 1))
    if hh and hl:
        return "Uptrend"
    if lh and ll:
        return "Downtrend"
    if hh or hl:
        return "Weak Uptrend"
    if lh or ll:
        return "Weak Downtrend"
    return "Sideways"


def _cluster_pivots(
    pivots: Sequence[Tuple[int, float]],
    tolerance_pct: float = EQUAL_LEVEL_TOLERANCE_PCT,
) -> List[List[Tuple[int, float]]]:
    """Cluster pivots without allowing chained clusters wider than tolerance."""
    groups: List[List[Tuple[int, float]]] = []
    for idx, price in sorted(pivots, key=lambda x: x[1]):
        placed = False
        for group in groups:
            prices = [p for _, p in group] + [price]
            centre = float(np.median(prices))
            width = ((max(prices) - min(prices)) / centre * 100) if centre else 999
            if width <= tolerance_pct:
                group.append((idx, price))
                placed = True
                break
        if not placed:
            groups.append([(idx, price)])

    result: List[List[Tuple[int, float]]] = []
    for group in groups:
        ordered = sorted({(int(i), float(p)) for i, p in group}, key=lambda x: x[0])
        # Require two structurally separate touches.
        if len(ordered) >= 2 and any(
            ordered[j][0] - ordered[i][0] >= 3
            for i in range(len(ordered))
            for j in range(i + 1, len(ordered))
        ):
            result.append(ordered)
    return result


def _new_level(
    side: str,
    target: float,
    zone_low: float,
    zone_high: float,
    kind: str,
    formation_idx: int,
    touch_indices: Optional[Sequence[int]] = None,
    touch_prices: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    touches = sorted(set(int(x) for x in (touch_indices or [formation_idx])))
    prices = [float(x) for x in (touch_prices or [target])]
    centre = float(np.median(prices)) if prices else float(target)
    width = ((max(prices) - min(prices)) / centre * 100) if len(prices) > 1 and centre else 0.0
    return {
        "side": side,
        "target_price": float(target),
        "zone_low": float(zone_low),
        "zone_high": float(zone_high),
        "primary_type": kind,
        "source_types": [kind],
        "formation_idx": int(formation_idx),
        "touch_indices": touches,
        "touch_count": len(touches),
        "cluster_width_pct": float(width),
        "confluence_count": 0,
    }


def _equal_levels(df: pd.DataFrame, offset: int, lookback: int = 180) -> List[Dict[str, Any]]:
    recent = df.iloc[offset:]
    highs, lows = find_swings(recent, lookback=5)
    levels: List[Dict[str, Any]] = []

    for group in _cluster_pivots(highs):
        absolute = [(offset + i, p) for i, p in group]
        ordered = sorted(absolute, key=lambda x: x[0])
        # Formation needs the second time-separated touch.
        second_pos = next(
            (j for j in range(1, len(ordered)) if ordered[j][0] - ordered[0][0] >= 3),
            None,
        )
        if second_pos is None:
            continue
        accepted = ordered[: second_pos + 1]
        target = max(p for _, p in accepted)
        # Later pivots can strengthen the pool, but a >0.2% external breach is a sweep.
        for idx, price in ordered[second_pos + 1 :]:
            if price > target * (1 + SWEEP_BUFFER_PCT):
                break
            accepted.append((idx, price))
            target = max(target, price)
        prices = [p for _, p in accepted]
        levels.append(
            _new_level(
                "BSL", max(prices), min(prices), max(prices), "EQUAL_HIGHS",
                accepted[second_pos][0], [i for i, _ in accepted], prices,
            )
        )

    for group in _cluster_pivots(lows):
        absolute = [(offset + i, p) for i, p in group]
        ordered = sorted(absolute, key=lambda x: x[0])
        second_pos = next(
            (j for j in range(1, len(ordered)) if ordered[j][0] - ordered[0][0] >= 3),
            None,
        )
        if second_pos is None:
            continue
        accepted = ordered[: second_pos + 1]
        target = min(p for _, p in accepted)
        for idx, price in ordered[second_pos + 1 :]:
            if price < target * (1 - SWEEP_BUFFER_PCT):
                break
            accepted.append((idx, price))
            target = min(target, price)
        prices = [p for _, p in accepted]
        levels.append(
            _new_level(
                "SSL", min(prices), min(prices), max(prices), "EQUAL_LOWS",
                accepted[second_pos][0], [i for i, _ in accepted], prices,
            )
        )
    return levels


def _period_level(df: pd.DataFrame, period: str, side: str, kind: str) -> Optional[Dict[str, Any]]:
    """Previous completed week/month level using calendar periods, not row slices."""
    if len(df) < 30:
        return None
    if period == "week":
        keys = df.index.to_period("W-FRI")
    else:
        keys = df.index.to_period("M")
    unique = list(dict.fromkeys(keys))
    if len(unique) < 2:
        return None
    previous = unique[-2]
    mask = keys == previous
    positions = np.flatnonzero(mask)
    if len(positions) == 0:
        return None
    subset = df.iloc[positions]
    if side == "BSL":
        label = subset["High"].idxmax()
        value = float(subset.loc[label, "High"])
    else:
        label = subset["Low"].idxmin()
        value = float(subset.loc[label, "Low"])
    touch_idx = int(df.index.get_indexer([label])[0])
    # PWH/PWL/PMH/PML becomes knowable only when that period closes.
    formation_idx = int(positions[-1])
    return _new_level(
        side, value, value, value, kind, formation_idx,
        touch_indices=[touch_idx], touch_prices=[value],
    )


def _merge_confluent_levels(levels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge independent sources near the same pool while preserving primary metadata."""
    merged: List[Dict[str, Any]] = []
    for side in ("BSL", "SSL"):
        side_levels = sorted((x for x in levels if x["side"] == side), key=lambda x: x["target_price"])
        groups: List[List[Dict[str, Any]]] = []
        for level in side_levels:
            if not groups:
                groups.append([level])
                continue
            group_prices = [x["target_price"] for x in groups[-1]] + [level["target_price"]]
            centre = float(np.median(group_prices))
            width = (max(group_prices) - min(group_prices)) / centre * 100 if centre else 999
            if width <= CONFLUENCE_TOLERANCE_PCT:
                groups[-1].append(level)
            else:
                groups.append([level])

        for group in groups:
            primary = max(
                group,
                key=lambda x: (TYPE_POINTS.get(x["primary_type"], 0), x.get("touch_count", 1)),
            )
            item = deepcopy(primary)
            source_types = sorted({s for x in group for s in x.get("source_types", [])})
            item["source_types"] = source_types
            item["confluence_count"] = max(0, len(source_types) - 1)
            item["zone_low"] = min(x["zone_low"] for x in group)
            item["zone_high"] = max(x["zone_high"] for x in group)
            item["target_price"] = item["zone_high"] if side == "BSL" else item["zone_low"]
            merged.append(item)
    return merged


def first_sweep_index(df: pd.DataFrame, level: Dict[str, Any]) -> Optional[int]:
    """Find the first true external breach after a level was formed."""
    start = max(0, int(level["formation_idx"]) + 1)
    target = float(level["target_price"])
    if start >= len(df):
        return None
    if level["side"] == "BSL":
        values = df["High"].to_numpy(dtype=float)
        threshold = target * (1 + SWEEP_BUFFER_PCT)
        hits = np.flatnonzero(values[start:] > threshold)
    else:
        values = df["Low"].to_numpy(dtype=float)
        threshold = target * (1 - SWEEP_BUFFER_PCT)
        hits = np.flatnonzero(values[start:] < threshold)
    return int(start + hits[0]) if len(hits) else None


def _count_near_test_episodes(df: pd.DataFrame, level: Dict[str, Any], end_idx: int) -> int:
    """Count separate historical approaches, excluding level-forming touches and last 3 bars."""
    start = max(0, int(level["formation_idx"]) + 1)
    end = min(end_idx, len(df) - 4)
    if end < start:
        return 0
    target = float(level["target_price"])
    touch_set = set(level.get("touch_indices", []))
    near_indices: List[int] = []
    for i in range(start, end + 1):
        if any(abs(i - t) <= 1 for t in touch_set):
            continue
        if level["side"] == "BSL":
            distance = max(0.0, (target - float(df["High"].iloc[i])) / target * 100)
        else:
            distance = max(0.0, (float(df["Low"].iloc[i]) - target) / target * 100)
        if distance <= NEAR_TEST_PCT:
            near_indices.append(i)
    if not near_indices:
        return 0
    episodes = 1
    for previous, current in zip(near_indices, near_indices[1:]):
        if current - previous > 2:
            episodes += 1
    return episodes


def build_liquidity_levels(df: pd.DataFrame, lookback_bars: int = 180) -> List[Dict[str, Any]]:
    """Build BSL/SSL levels with formation, touch, confluence and sweep metadata."""
    df = normalize_ohlcv(df)
    if len(df) < MIN_HISTORY_BARS:
        return []
    n = len(df)
    offset = max(0, n - lookback_bars)
    levels: List[Dict[str, Any]] = _equal_levels(df, offset, lookback_bars)

    recent = df.iloc[offset:]
    swing_highs, swing_lows = find_swings(recent, lookback=7)
    for local_idx, price in swing_highs[-15:]:
        idx = offset + local_idx
        levels.append(_new_level("BSL", price, price, price, "SWING_HIGH", idx))
    for local_idx, price in swing_lows[-15:]:
        idx = offset + local_idx
        levels.append(_new_level("SSL", price, price, price, "SWING_LOW", idx))

    for args in (
        ("week", "BSL", "PWH"), ("week", "SSL", "PWL"),
        ("month", "BSL", "PMH"), ("month", "SSL", "PML"),
    ):
        level = _period_level(df, *args)
        if level:
            levels.append(level)

    tail_start = max(0, n - 250)
    tail = df.iloc[tail_start:]
    high_label = tail["High"].idxmax()
    low_label = tail["Low"].idxmin()
    high_idx = int(df.index.get_indexer([high_label])[0])
    low_idx = int(df.index.get_indexer([low_label])[0])
    high = float(df["High"].iloc[high_idx])
    low = float(df["Low"].iloc[low_idx])
    levels.append(_new_level("BSL", high, high, high, "52W_HIGH", high_idx))
    levels.append(_new_level("SSL", low, low, low, "52W_LOW", low_idx))

    levels = _merge_confluent_levels(levels)
    annotated: List[Dict[str, Any]] = []
    for level in levels:
        item = deepcopy(level)
        sweep_idx = first_sweep_index(df, item)
        item["sweep_idx"] = sweep_idx
        item["swept_since_formation"] = sweep_idx is not None
        item["active"] = sweep_idx is None
        item["age_bars"] = max(0, n - 1 - int(item["formation_idx"]))
        item["formation_date"] = str(pd.Timestamp(df.index[int(item["formation_idx"])]).date())
        item["touch_dates"] = [
            str(pd.Timestamp(df.index[i]).date())
            for i in item.get("touch_indices", []) if 0 <= i < n
        ]
        item["prior_near_test_episodes"] = _count_near_test_episodes(
            df, item, sweep_idx - 1 if sweep_idx is not None else n - 1
        )
        if sweep_idx is not None:
            item["sweep_date"] = str(pd.Timestamp(df.index[sweep_idx]).date())
        item["level_id"] = (
            f"{item['side']}:{item['primary_type']}:{item['formation_date']}:"
            f"{item['target_price']:.4f}"
        )
        annotated.append(item)
    return annotated


# ----------------------------- Setup measurements -----------------------------
def distance_to_level(close: float, level: Dict[str, Any]) -> float:
    target = float(level["target_price"])
    if close <= 0:
        return 999.0
    if level["side"] == "BSL":
        return (target - close) / close * 100
    return (close - target) / close * 100


def stage_from_distance(distance: float) -> Tuple[str, str]:
    if distance <= 0.30:
        return "AT LEVEL", "🎯"
    if distance <= 0.80:
        return "VERY CLOSE", "🔥"
    if distance <= MAX_DISTANCE_PCT:
        return "CLOSE", "⚡"
    return "FAR", "⏸️"


def status_from_score(score: float) -> str:
    if score >= 85:
        return "PRIME_WATCH"
    if score >= 75:
        return "WATCH"
    if score >= 65:
        return "WAIT"
    return "SKIP"


def liquidity_quality_score(level: Dict[str, Any]) -> Tuple[int, Dict[str, int]]:
    type_score = TYPE_POINTS.get(level.get("primary_type", ""), 0)
    touches = int(level.get("touch_count", 1))
    if touches <= 1:
        touch_score = 4
    elif touches == 2:
        touch_score = 5
    elif touches == 3:
        touch_score = 7
    else:
        touch_score = 6

    width = float(level.get("cluster_width_pct", 0))
    if touches <= 1:
        tightness = 3
    elif width <= 0.15:
        tightness = 5
    elif width <= 0.30:
        tightness = 4
    elif width <= 0.50:
        tightness = 2
    else:
        tightness = 0

    near_tests = int(level.get("prior_near_test_episodes", 0))
    cleanliness = {0: 7, 1: 4, 2: 2}.get(near_tests, 0)
    confluence = 3 if level.get("confluence_count", 0) >= 2 else 2 if level.get("confluence_count", 0) == 1 else 0
    breakdown = {
        "type": type_score,
        "touches": touch_score,
        "tightness": tightness,
        "cleanliness": cleanliness,
        "confluence": confluence,
    }
    return min(30, sum(breakdown.values())), breakdown


def approach_features(df: pd.DataFrame, level: Dict[str, Any]) -> Dict[str, Any]:
    """Measure whether price is genuinely moving toward a fixed level."""
    close = df["Close"].to_numpy(dtype=float)
    if len(close) < 7:
        return {"valid": False, "score": 0, "flags": ["INSUFFICIENT_APPROACH_DATA"]}

    def dist_at(i: int) -> float:
        return distance_to_level(float(close[i]), level)

    now = dist_at(-1)
    d3 = dist_at(-4)
    d5 = dist_at(-6)
    gap3 = d3 - now
    gap5 = d5 - now
    changes = np.diff(close[-4:])
    if level["side"] == "BSL":
        directional = int(np.sum(changes > 0))
    else:
        directional = int(np.sum(changes < 0))

    moving = gap3 >= 0.10 or gap5 >= 0.15 or directional >= 2
    flags: List[str] = []
    if gap3 > 2.50:
        gap_score = 5
        flags.append("FAST_APPROACH")
    elif gap3 >= 0.50:
        gap_score = 8
    elif gap3 >= 0.20:
        gap_score = 6
    elif gap3 >= 0.10:
        gap_score = 4
    elif gap5 >= 0.15:
        gap_score = 3
    elif directional >= 2:
        gap_score = 2
    else:
        gap_score = 0

    directional_score = {3: 4, 2: 3, 1: 1, 0: 0}.get(directional, 0)
    tests = int(level.get("prior_near_test_episodes", 0))
    clean_score = 5 if tests == 0 else 2 if tests == 1 else 0
    if tests:
        flags.append("REPEATED_NEAR_TESTS")
    distance_score = 3 if 0.30 <= now <= 0.80 else 2 if 0 <= now <= MAX_DISTANCE_PCT else 0
    score = min(20, gap_score + directional_score + clean_score + distance_score)
    return {
        "valid": bool(moving and 0 <= now <= MAX_DISTANCE_PCT),
        "moving_toward": bool(moving),
        "distance_pct": round(now, 3),
        "distance_3d_pct": round(d3, 3),
        "distance_5d_pct": round(d5, 3),
        "gap_closed_3d": round(gap3, 3),
        "gap_closed_5d": round(gap5, 3),
        "directional_closes_3": directional,
        "score": score,
        "flags": flags,
        "breakdown": {
            "gap_closure": gap_score,
            "directional_closes": directional_score,
            "clean_path": clean_score,
            "distance": distance_score,
        },
    }


def htf_context_score(
    direction: str,
    trend_daily: str,
    trend_weekly: str,
    range_position: float,
) -> Tuple[int, Dict[str, int], List[str]]:
    if direction == "LONG":
        weekly_map = {"Uptrend": 7, "Weak Uptrend": 6, "Sideways": 4, "Weak Downtrend": 2, "Downtrend": 0, "Unclear": 2, "Unknown": 2}
        daily_map = {"Weak Downtrend": 5, "Downtrend": 4, "Sideways": 3, "Weak Uptrend": 2, "Uptrend": 1, "Unclear": 2}
        location = 3 if range_position <= 35 else 2 if range_position <= 55 else 1 if range_position <= 70 else 0
    else:
        weekly_map = {"Downtrend": 7, "Weak Downtrend": 6, "Sideways": 4, "Weak Uptrend": 2, "Uptrend": 0, "Unclear": 2, "Unknown": 2}
        daily_map = {"Weak Uptrend": 5, "Uptrend": 4, "Sideways": 3, "Weak Downtrend": 2, "Downtrend": 1, "Unclear": 2}
        location = 3 if range_position >= 65 else 2 if range_position >= 45 else 1 if range_position >= 30 else 0
    weekly = weekly_map.get(trend_weekly, 2)
    daily = daily_map.get(trend_daily, 2)
    flags: List[str] = []
    if weekly <= 2:
        flags.append("COUNTER_WEEKLY_STRUCTURE")
    if weekly + daily < 7:
        flags.append("STRUCTURE_MIXED")
    return min(15, weekly + daily + location), {"weekly": weekly, "daily": daily, "range_location": location}, flags


def data_integrity_score(df: pd.DataFrame, market_session_date: Optional[Any] = None) -> Tuple[int, List[str]]:
    flags: List[str] = []
    latest = pd.Timestamp(df.index[-1]).date()
    expected = pd.Timestamp(market_session_date).date() if market_session_date is not None else latest
    fresh = latest == expected
    if not fresh:
        flags.append("STALE_DATA")
    enough = len(df) >= 200
    if not enough:
        flags.append("SHORT_HISTORY")
    recent = df.tail(60)
    valid_recent = (
        not recent.index.duplicated().any()
        and not recent[["Open", "High", "Low", "Close"]].isna().any().any()
        and bool((recent[["Open", "High", "Low", "Close"]] > 0).all().all())
    )
    score = (2 if fresh else 0) + (1 if enough else 0) + (1 if valid_recent else 0) + 1
    return min(5, score), flags


def _dedupe_targets(levels: Iterable[Dict[str, Any]], direction: str, entry: float) -> List[Dict[str, Any]]:
    side = "BSL" if direction == "LONG" else "SSL"
    valid = []
    for level in levels:
        if not level.get("active") or level.get("side") != side:
            continue
        price = float(level["target_price"])
        if (direction == "LONG" and price <= entry) or (direction == "SHORT" and price >= entry):
            continue
        valid.append(level)
    valid.sort(key=lambda x: x["target_price"], reverse=(direction == "SHORT"))
    result: List[Dict[str, Any]] = []
    for level in valid:
        if not result or abs(level["target_price"] - result[-1]["target_price"]) / result[-1]["target_price"] >= 0.005:
            result.append(level)
    return result


def _trade_score(plan: Dict[str, Any]) -> Tuple[int, Dict[str, int], List[str]]:
    rr1 = float(plan["rr1"])
    rr2 = float(plan["rr2"])
    rr1_points = 7 if rr1 >= 4 else 6 if rr1 >= 3 else 5 if rr1 >= 2.5 else 4 if rr1 >= 2 else 0
    rr2_points = 3 if rr2 >= 4 else 2 if rr2 >= 3 else 1 if rr2 >= 2 else 0
    target_points = 3 if plan.get("tp2_structural") else 0
    risk_points = 2 if 0 < float(plan["risk_pct"]) <= 2.0 else 0
    flags: List[str] = []
    if rr1 < 2.5:
        flags.append("BORDERLINE_RR")
    if not plan.get("tp2_structural"):
        flags.append("SYNTHETIC_TP2")
    parts = {"rr1": rr1_points, "rr2": rr2_points, "targets": target_points, "risk": risk_points}
    return min(15, sum(parts.values())), parts, flags


def calculate_reference_trade_plan(
    level: Dict[str, Any], all_levels: List[Dict[str, Any]], current_price: float
) -> Optional[Dict[str, Any]]:
    """Pre-sweep reference plan. It must be revalidated after the sweep."""
    direction = "SHORT" if level["side"] == "BSL" else "LONG"
    target = float(level["target_price"])
    entry_low, entry_high = target * 0.998, target * 1.002
    if direction == "LONG":
        sl = target * (1 - REFERENCE_SL_BUFFER_PCT)
        conservative_entry = entry_high
    else:
        sl = target * (1 + REFERENCE_SL_BUFFER_PCT)
        conservative_entry = entry_low

    targets = _dedupe_targets(all_levels, direction, conservative_entry)
    if not targets:
        return None
    tp1_level = targets[0]
    tp1 = float(tp1_level["target_price"])
    tp2_level = next(
        (
            x for x in targets[1:]
            if abs(float(x["target_price"]) - tp1) / tp1 * 100 >= 2.0
        ),
        None,
    )
    if not tp2_level:
        return None  # High-quality watch alerts require two real opposing pools.
    tp2 = float(tp2_level["target_price"])
    tp2_structural = True
    tp2_type = tp2_level["primary_type"]

    if direction == "LONG":
        risk = conservative_entry - sl
        reward1, reward2 = tp1 - conservative_entry, tp2 - conservative_entry
        ordered = tp1 > conservative_entry and tp2 > tp1 * 1.02
    else:
        risk = sl - conservative_entry
        reward1, reward2 = conservative_entry - tp1, conservative_entry - tp2
        ordered = tp1 < conservative_entry and tp2 < tp1 * 0.98
    if risk <= 0 or reward1 <= 0 or not ordered:
        return None
    rr1, rr2 = reward1 / risk, reward2 / risk
    if rr1 < MIN_RR:
        return None
    plan = {
        "plan_type": "REFERENCE_PRE_SWEEP",
        "entry_zone": [round(entry_low, 2), round(entry_high, 2)],
        "entry_ref": round(conservative_entry, 2),
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "tp1_type": tp1_level["primary_type"],
        "tp2_type": tp2_type,
        "tp2_structural": tp2_structural,
        "risk_pct": round(risk / conservative_entry * 100, 2),
        "rr1": round(rr1, 2),
        "rr2": round(rr2, 2),
    }
    score, breakdown, flags = _trade_score(plan)
    plan["score"] = score
    plan["score_breakdown"] = breakdown
    plan["flags"] = flags
    return plan


# ----------------------------- Confirmation engine -----------------------------
def detect_price_action_confirmation(df: pd.DataFrame, level: Dict[str, Any]) -> Dict[str, Any]:
    """Detect sweep -> reclaim -> displacement -> CHoCH using daily OHLC only."""
    sweep_idx = level.get("sweep_idx")
    if sweep_idx is None:
        return {"state": "PRE_SWEEP", "confirmation_score": 0}
    sweep_idx = int(sweep_idx)
    n = len(df)
    target = float(level["target_price"])
    direction = "SHORT" if level["side"] == "BSL" else "LONG"
    sweep_row = df.iloc[sweep_idx]
    if direction == "LONG":
        sweep_extreme = float(sweep_row["Low"])
        penetration = (target - sweep_extreme) / target * 100
        reclaim_boundary = float(level.get("zone_high", target))
    else:
        sweep_extreme = float(sweep_row["High"])
        penetration = (sweep_extreme - target) / target * 100
        reclaim_boundary = float(level.get("zone_low", target))

    reclaim_idx: Optional[int] = None
    reclaim_end = min(n - 1, sweep_idx + RECLAIM_WINDOW)
    for i in range(sweep_idx, reclaim_end + 1):
        close_i = float(df["Close"].iloc[i])
        if (direction == "LONG" and close_i > reclaim_boundary) or (
            direction == "SHORT" and close_i < reclaim_boundary
        ):
            reclaim_idx = i
            break

    sweep_age = n - 1 - sweep_idx
    if reclaim_idx is None:
        return {
            "state": "SWEPT_WAIT_RECLAIM" if sweep_age <= RECLAIM_WINDOW else "FAILED_NO_RECLAIM",
            "direction": direction,
            "sweep_idx": sweep_idx,
            "sweep_date": str(pd.Timestamp(df.index[sweep_idx]).date()),
            "sweep_age_bars": sweep_age,
            "sweep_extreme": round(sweep_extreme, 2),
            "sweep_penetration_pct": round(penetration, 2),
            "reclaimed": False,
            "displacement": False,
            "choch_confirmed": False,
            "confirmation_score": min(20, 20 if penetration <= 1 else 15 if penetration <= 2 else 8),
        }

    # Rejection candle quality.
    row = df.iloc[reclaim_idx]
    candle_range = max(float(row["High"] - row["Low"]), 1e-9)
    body = max(abs(float(row["Close"] - row["Open"])), 1e-9)
    close_location = float((row["Close"] - row["Low"]) / candle_range)
    if direction == "LONG":
        wick = max(0.0, float(min(row["Open"], row["Close"]) - row["Low"]))
        close_quality = close_location >= 0.65
    else:
        wick = max(0.0, float(row["High"] - max(row["Open"], row["Close"])))
        close_quality = close_location <= 0.35
    wick_body_ratio = wick / body

    # Internal CHoCH level must have been a confirmed pivot by the sweep date.
    pre_sweep = df.iloc[: sweep_idx + 1]
    internal_highs, internal_lows = find_swings(pre_sweep, lookback=3)
    if direction == "LONG":
        choch_level = internal_highs[-1][1] if internal_highs else None
    else:
        choch_level = internal_lows[-1][1] if internal_lows else None

    displacement_idx: Optional[int] = None
    choch_idx: Optional[int] = None
    search_end = min(n - 1, sweep_idx + CHOCH_WINDOW)
    bodies = (df["Close"] - df["Open"]).abs().to_numpy(dtype=float)
    for i in range(reclaim_idx, search_end + 1):
        if i == 0:
            continue
        median_body = float(np.median(bodies[max(0, i - 5) : i])) if i > 0 else 0.0
        row_i = df.iloc[i]
        if direction == "LONG":
            displaced = (
                row_i["Close"] > row_i["Open"]
                and row_i["Close"] > df["High"].iloc[i - 1]
                and abs(row_i["Close"] - row_i["Open"]) >= max(median_body * 1.2, 1e-9)
            )
            broke = choch_level is not None and row_i["Close"] > choch_level
        else:
            displaced = (
                row_i["Close"] < row_i["Open"]
                and row_i["Close"] < df["Low"].iloc[i - 1]
                and abs(row_i["Close"] - row_i["Open"]) >= max(median_body * 1.2, 1e-9)
            )
            broke = choch_level is not None and row_i["Close"] < choch_level
        if displaced and displacement_idx is None:
            displacement_idx = i
        if broke and choch_idx is None:
            choch_idx = i

    # A materially deeper second raid before CHoCH invalidates the first confirmation sequence.
    invalidated = False
    invalidation_end = choch_idx if choch_idx is not None else min(n - 1, sweep_idx + CHOCH_WINDOW)
    if reclaim_idx + 1 <= invalidation_end:
        if direction == "LONG":
            invalidated = bool(
                (df["Low"].iloc[reclaim_idx + 1 : invalidation_end + 1] < sweep_extreme * (1 - SWEEP_BUFFER_PCT)).any()
            )
        else:
            invalidated = bool(
                (df["High"].iloc[reclaim_idx + 1 : invalidation_end + 1] > sweep_extreme * (1 + SWEEP_BUFFER_PCT)).any()
            )

    sweep_points = 20 if 0.20 <= penetration <= 1.0 else 15 if penetration <= 2.0 else 8
    reclaim_speed = reclaim_idx - sweep_idx
    reclaim_points = 15 if reclaim_speed == 0 else 12 if reclaim_speed == 1 else 10
    reclaim_points += 5 if wick_body_ratio >= 1.0 else 3 if wick_body_ratio >= 0.5 else 0
    reclaim_points += 5 if close_quality else 0
    reclaim_points = min(25, reclaim_points)
    displacement_points = 20 if displacement_idx is not None else 0
    choch_points = 25 if choch_idx is not None else 0

    if invalidated:
        state = "FAILED_INVALIDATED"
    elif choch_idx is not None and displacement_idx is not None:
        state = "TRIGGER_READY"
    elif choch_idx is not None:
        state = "CHOCH_WAIT_DISPLACEMENT"
    else:
        state = "RECLAIMED_WAIT_CHOCH"

    return {
        "state": state,
        "direction": direction,
        "sweep_idx": sweep_idx,
        "sweep_date": str(pd.Timestamp(df.index[sweep_idx]).date()),
        "sweep_age_bars": sweep_age,
        "sweep_extreme": round(sweep_extreme, 2),
        "sweep_penetration_pct": round(penetration, 2),
        "reclaimed": True,
        "reclaim_idx": reclaim_idx,
        "reclaim_date": str(pd.Timestamp(df.index[reclaim_idx]).date()),
        "reclaim_speed_bars": reclaim_speed,
        "wick_body_ratio": round(wick_body_ratio, 2),
        "close_location": round(close_location, 2),
        "displacement": displacement_idx is not None,
        "displacement_idx": displacement_idx,
        "choch_level": round(float(choch_level), 2) if choch_level is not None else None,
        "choch_confirmed": choch_idx is not None,
        "choch_idx": choch_idx,
        "choch_date": str(pd.Timestamp(df.index[choch_idx]).date()) if choch_idx is not None else None,
        "invalidated": invalidated,
        "confirmation_score_before_rr": sweep_points + reclaim_points + displacement_points + choch_points,
        "confirmation_score": min(90, sweep_points + reclaim_points + displacement_points + choch_points),
    }


def calculate_confirmed_trade_plan(
    df: pd.DataFrame,
    level: Dict[str, Any],
    confirmation: Dict[str, Any],
    all_levels: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Recalculate entry/SL/R:R from actual sweep wick and CHoCH structure."""
    choch_idx = confirmation.get("choch_idx")
    choch_level = confirmation.get("choch_level")
    if choch_idx is None or choch_level is None:
        return None
    direction = confirmation["direction"]
    entry_low = float(choch_level) * 0.998
    entry_high = float(choch_level) * 1.002
    choch_close = float(df["Close"].iloc[int(choch_idx)])
    if direction == "LONG":
        entry_ref = max(entry_high, choch_close)
        sl = float(confirmation["sweep_extreme"]) * (1 - SWEEP_BUFFER_PCT)
    else:
        entry_ref = min(entry_low, choch_close)
        sl = float(confirmation["sweep_extreme"]) * (1 + SWEEP_BUFFER_PCT)

    targets = _dedupe_targets(all_levels, direction, entry_ref)
    if not targets:
        return None
    tp1_level = targets[0]
    tp1 = float(tp1_level["target_price"])
    tp2_level = next(
        (x for x in targets[1:] if abs(float(x["target_price"]) - tp1) / tp1 * 100 >= 2.0),
        None,
    )
    if not tp2_level:
        return None  # Confirmation alerts require two structural targets.
    tp2 = float(tp2_level["target_price"])
    if direction == "LONG":
        risk, reward1, reward2 = entry_ref - sl, tp1 - entry_ref, tp2 - entry_ref
        ordered = tp2 > tp1 > entry_ref
    else:
        risk, reward1, reward2 = sl - entry_ref, entry_ref - tp1, entry_ref - tp2
        ordered = tp2 < tp1 < entry_ref
    if not ordered or risk <= 0 or reward1 <= 0:
        return None
    rr1, rr2 = reward1 / risk, reward2 / risk
    if rr1 < MIN_RR:
        return None
    plan = {
        "plan_type": "CONFIRMED_STRUCTURE",
        "entry_zone": [round(entry_low, 2), round(entry_high, 2)],
        "entry_ref": round(entry_ref, 2),
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "tp1_type": tp1_level["primary_type"],
        "tp2_type": tp2_level["primary_type"],
        "tp2_structural": True,
        "risk_pct": round(risk / entry_ref * 100, 2),
        "rr1": round(rr1, 2),
        "rr2": round(rr2, 2),
    }
    score, breakdown, flags = _trade_score(plan)
    plan["score"] = score
    plan["score_breakdown"] = breakdown
    plan["flags"] = flags
    return plan


# ----------------------------- Candidate assembly -----------------------------
def _common_context(df: pd.DataFrame) -> Dict[str, Any]:
    trend_daily = detect_trend(df, lookback=5)
    weekly = df.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()
    trend_weekly = detect_trend(weekly, lookback=3) if len(weekly) >= 10 else "Unknown"
    tail = df.tail(250)
    wk52_high = float(tail["High"].max())
    wk52_low = float(tail["Low"].min())
    current = float(df["Close"].iloc[-1])
    range_position = ((current - wk52_low) / (wk52_high - wk52_low) * 100) if wk52_high > wk52_low else 50.0
    change = (current - float(df["Close"].iloc[-2])) / float(df["Close"].iloc[-2]) * 100
    return {
        "trend_daily": trend_daily,
        "trend_weekly": trend_weekly,
        "wk52_high": wk52_high,
        "wk52_low": wk52_low,
        "range_position": range_position,
        "price": current,
        "change_pct": change,
    }


def _base_result(
    info: Dict[str, Any],
    context: Dict[str, Any],
    level: Dict[str, Any],
    approach: Dict[str, Any],
    plan: Dict[str, Any],
    score_breakdown: Dict[str, int],
    score_details: Dict[str, Any],
    flags: List[str],
    signal_state: str,
    confirmation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    direction = "SHORT" if level["side"] == "BSL" else "LONG"
    distance = max(0.0, distance_to_level(context["price"], level))
    stage, emoji = stage_from_distance(distance)
    if signal_state != "PRE_SWEEP":
        state_labels = {
            "SWEPT_WAIT_RECLAIM": ("SWEPT", "🧹"),
            "RECLAIMED_WAIT_CHOCH": ("WAIT CHoCH", "🔁"),
            "CHOCH_WAIT_DISPLACEMENT": ("WAIT DISPLACEMENT", "🧭"),
            "TRIGGER_READY": ("TRIGGER READY", "✅"),
        }
        stage, emoji = state_labels.get(signal_state, (signal_state, "📌"))

    technical_score = int(sum(score_breakdown.values()))
    liquidity_public = {
        "level_id": level["level_id"],
        "primary_type": level["primary_type"],
        "source_types": level["source_types"],
        "formation_date": level["formation_date"],
        "touch_dates": level.get("touch_dates", []),
        "touch_count": int(level.get("touch_count", 1)),
        "cluster_width_pct": round(float(level.get("cluster_width_pct", 0)), 3),
        "age_bars": int(level.get("age_bars", 0)),
        "prior_near_test_episodes": int(level.get("prior_near_test_episodes", 0)),
        "confluence_count": int(level.get("confluence_count", 0)),
        "swept_since_formation": bool(level.get("swept_since_formation")),
    }
    return {
        "symbol": info["symbol"],
        "name": info["name"],
        "sector": info["sector"],
        "data_as_of": str(pd.Timestamp(score_details["data_date"]).date()),
        "price": round(context["price"], 2),
        "change_pct": round(context["change_pct"], 2),
        "signal_state": signal_state,
        "state_priority": STATE_PRIORITY.get(signal_state, 0),
        "entry_status": (
            "TRIGGER_READY_VERIFY_CHART" if signal_state == "TRIGGER_READY"
            else "WAIT_SWEEP_REJECTION_CHOCH" if signal_state == "PRE_SWEEP"
            else signal_state
        ),
        "stage": stage,
        "stage_emoji": emoji,
        "distance_pct": round(distance, 2),
        "target_level": round(float(level["target_price"]), 2),
        "target_level_type": " + ".join(level["source_types"]),
        "liq_type": level["side"],
        "direction": direction,
        "liquidity": liquidity_public,
        "price_action": approach,
        "confirmation": confirmation or {},
        "entry_zone": plan["entry_zone"],
        "entry_ref": plan["entry_ref"],
        "sl": plan["sl"],
        "tp1": plan["tp1"],
        "tp2": plan["tp2"],
        "tp1_type": plan["tp1_type"],
        "tp2_type": plan["tp2_type"],
        "plan_type": plan["plan_type"],
        "risk_pct": plan["risk_pct"],
        "rr1": plan["rr1"],
        "rr2": plan["rr2"],
        "trend_daily": context["trend_daily"],
        "trend_weekly": context["trend_weekly"],
        "range_position": round(context["range_position"], 1),
        "wk52_high": round(context["wk52_high"], 2),
        "wk52_low": round(context["wk52_low"], 2),
        "score_breakdown": score_breakdown,
        "score_details": score_details,
        "technical_score": technical_score,
        "fund_score": 0,
        "fund_points": 0,
        "fund_data": None,
        "setup_score": technical_score,
        "final_score": technical_score,
        "base_status": "PENDING_FUNDAMENTAL",
        "final_status": "PENDING_FUNDAMENTAL",
        "priority": technical_score,
        "quality_flags": sorted(set(flags)),
    }


def _make_pre_sweep_candidate(
    df: pd.DataFrame,
    info: Dict[str, Any],
    levels: List[Dict[str, Any]],
    context: Dict[str, Any],
    data_score: int,
    data_flags: List[str],
) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for level in levels:
        if not level.get("active"):
            continue
        distance = distance_to_level(context["price"], level)
        if distance < 0 or distance > MAX_DISTANCE_PCT:
            continue
        approach = approach_features(df, level)
        if not approach.get("valid"):
            continue
        plan = calculate_reference_trade_plan(level, levels, context["price"])
        if not plan:
            continue
        direction = "SHORT" if level["side"] == "BSL" else "LONG"
        liq_score, liq_details = liquidity_quality_score(level)
        htf_score, htf_details, htf_flags = htf_context_score(
            direction, context["trend_daily"], context["trend_weekly"], context["range_position"]
        )
        score_breakdown = {
            "liquidity": liq_score,
            "approach": int(approach["score"]),
            "htf_context": htf_score,
            "trade_geometry": int(plan["score"]),
            "data_integrity": data_score,
        }
        score_details = {
            "liquidity": liq_details,
            "approach": approach.get("breakdown", {}),
            "htf_context": htf_details,
            "trade_geometry": plan.get("score_breakdown", {}),
            "data_date": df.index[-1],
        }
        flags = data_flags + approach.get("flags", []) + htf_flags + plan.get("flags", [])
        result = _base_result(
            info, context, level, approach, plan, score_breakdown, score_details, flags, "PRE_SWEEP"
        )
        candidates.append(result)
    if not candidates:
        return None
    return max(candidates, key=lambda x: (x["technical_score"], -x["distance_pct"]))


def _make_post_sweep_candidate(
    df: pd.DataFrame,
    info: Dict[str, Any],
    levels: List[Dict[str, Any]],
    context: Dict[str, Any],
    data_score: int,
    data_flags: List[str],
) -> Optional[Dict[str, Any]]:
    n = len(df)
    candidates: List[Dict[str, Any]] = []
    for level in levels:
        sweep_idx = level.get("sweep_idx")
        if sweep_idx is None or n - 1 - int(sweep_idx) > CONFIRMATION_LOOKBACK:
            continue
        if int(sweep_idx) < 6:
            continue
        # The preceding close had to be a genuine pre-sweep approach candidate.
        pre_df = df.iloc[: int(sweep_idx)]
        pre_distance = distance_to_level(float(pre_df["Close"].iloc[-1]), level)
        if pre_distance < 0 or pre_distance > MAX_DISTANCE_PCT:
            continue
        approach = approach_features(pre_df, level)
        if not approach.get("moving_toward"):
            continue

        confirmation = detect_price_action_confirmation(df, level)
        state = confirmation.get("state")
        if state in {"FAILED_NO_RECLAIM", "FAILED_INVALIDATED"}:
            continue
        # A completed trigger is sent only on the CHoCH session, preventing stale repeats.
        if state == "TRIGGER_READY" and confirmation.get("choch_idx") != n - 1:
            continue

        reference_plan = calculate_reference_trade_plan(level, levels, context["price"])
        confirmed_plan = (
            calculate_confirmed_trade_plan(df, level, confirmation, levels)
            if state == "TRIGGER_READY" else None
        )
        if state == "TRIGGER_READY" and not confirmed_plan:
            continue
        plan = confirmed_plan or reference_plan
        if not plan:
            continue

        direction = confirmation["direction"]
        liq_score, liq_details = liquidity_quality_score(level)
        htf_score, htf_details, htf_flags = htf_context_score(
            direction, context["trend_daily"], context["trend_weekly"], context["range_position"]
        )
        score_breakdown = {
            "liquidity": liq_score,
            "approach": int(approach.get("score", 0)),
            "htf_context": htf_score,
            "trade_geometry": int(plan["score"]),
            "data_integrity": data_score,
        }
        rr_confirmation = 10 if state == "TRIGGER_READY" and plan["rr1"] >= 3 else 7 if state == "TRIGGER_READY" else 0
        confirmation["rr_confirmation_points"] = rr_confirmation
        confirmation["confirmation_score"] = min(
            100, int(confirmation.get("confirmation_score", 0)) + rr_confirmation
        )
        score_details = {
            "liquidity": liq_details,
            "approach": approach.get("breakdown", {}),
            "htf_context": htf_details,
            "trade_geometry": plan.get("score_breakdown", {}),
            "data_date": df.index[-1],
        }
        flags = data_flags + approach.get("flags", []) + htf_flags + plan.get("flags", [])
        result = _base_result(
            info, context, level, approach, plan, score_breakdown, score_details, flags, state, confirmation
        )
        candidates.append(result)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda x: (
            STATE_PRIORITY.get(x["signal_state"], 0),
            x.get("confirmation", {}).get("confirmation_score", 0),
            x["technical_score"],
        ),
    )


def analyze_stock_from_df(
    df: pd.DataFrame,
    info: Dict[str, Any],
    market_session_date: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Analyze one already-downloaded dataframe. No network calls are made."""
    try:
        df = normalize_ohlcv(df)
        if len(df) < MIN_HISTORY_BARS:
            return None
        data_score, data_flags = data_integrity_score(df, market_session_date)
        if "STALE_DATA" in data_flags:
            return None
        context = _common_context(df)
        levels = build_liquidity_levels(df)
        if not levels:
            return None

        post = _make_post_sweep_candidate(df, info, levels, context, data_score, data_flags)
        pre = _make_pre_sweep_candidate(df, info, levels, context, data_score, data_flags)
        if post and pre:
            # Confirmation states are more actionable than another pre-sweep pool.
            return post if post["state_priority"] > pre["state_priority"] else max(
                [post, pre], key=lambda x: x["technical_score"]
            )
        return post or pre
    except Exception as exc:
        if os.getenv("SCANNER_DEBUG", "false").lower() == "true":
            print(f"⚠️ analyze {info.get('symbol', '?')}: {exc}")
        return None


# ----------------------------- Batch download -----------------------------
def batch_download_all(symbols_list: Sequence[str], period: str = "1y", chunk_size: int = 100) -> Dict[str, pd.DataFrame]:
    """Download in chunks; deliberately no slow individual-call fallback."""
    import yfinance as yf  # Lazy import keeps deterministic unit tests lightweight.

    print(f"⚡ Batch downloading {len(symbols_list)} stocks in chunks of {chunk_size}...")
    start = time.time()
    result: Dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols_list), chunk_size):
        chunk = list(symbols_list[i : i + chunk_size])
        number = i // chunk_size + 1
        total = (len(symbols_list) + chunk_size - 1) // chunk_size
        print(f"  Chunk {number}/{total}: {len(chunk)} stocks...")
        try:
            data = yf.download(
                chunk,
                period=period,
                interval="1d",
                progress=False,
                group_by="ticker",
                threads=True,
                auto_adjust=True,
                timeout=30,
            )
            for symbol in chunk:
                try:
                    raw = data if len(chunk) == 1 else data[symbol]
                    clean = normalize_ohlcv(raw)
                    if len(clean) >= MIN_HISTORY_BARS:
                        result[symbol] = clean
                except Exception:
                    continue
        except Exception as exc:
            print(f"  ⚠️ Chunk {number} failed: {exc}")
    elapsed = time.time() - start
    print(f"✅ Batch download: {elapsed:.1f}s — {len(result)}/{len(symbols_list)} valid")
    return result


def infer_market_session_date(stock_dfs: Dict[str, pd.DataFrame]) -> Optional[pd.Timestamp]:
    """Use the universe's modal latest session, avoiding local-calendar assumptions."""
    dates = []
    for df in stock_dfs.values():
        clean = normalize_ohlcv(df)
        if not clean.empty:
            dates.append(pd.Timestamp(clean.index[-1]).normalize())
    if not dates:
        return None
    counts = Counter(dates)
    return max(counts, key=lambda x: (counts[x], x))


# Compatibility helper for older imports/documentation.
def find_untapped_major_liquidity(df: pd.DataFrame) -> Tuple[List[Tuple[float, str, int]], List[Tuple[float, str, int]]]:
    levels = [x for x in build_liquidity_levels(df) if x.get("active")]
    bsl = [(round(x["target_price"], 2), x["primary_type"], TYPE_POINTS.get(x["primary_type"], 0)) for x in levels if x["side"] == "BSL"]
    ssl = [(round(x["target_price"], 2), x["primary_type"], TYPE_POINTS.get(x["primary_type"], 0)) for x in levels if x["side"] == "SSL"]
    return sorted(bsl), sorted(ssl, reverse=True)

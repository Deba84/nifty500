"""
Scanner v4 — PROACTIVE LIQUIDITY APPROACH DETECTION
Focus: Fundamentally strong stocks APPROACHING major liquidity levels
Goal: Alert BEFORE sweep happens, prepare for reversal
"""
import yfinance as yf
import pandas as pd
import numpy as np
import warnings
import time
import random
warnings.filterwarnings('ignore')

from nifty500_list import get_symbol_map
from fundamental_analyzer import get_fundamental_score


# ============ CORE FUNCTIONS ============

def find_swings(df, lookback=5):
    highs = df['High'].values
    lows = df['Low'].values
    n = len(df)
    swing_highs, swing_lows = [], []
    for i in range(lookback, n - lookback):
        if highs[i] == max(highs[i-lookback:i+lookback+1]):
            swing_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-lookback:i+lookback+1]):
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows


def detect_trend(df, lookback=5):
    swing_highs, swing_lows = find_swings(df, lookback)
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "Unclear"
    last_highs = [h[1] for h in swing_highs[-3:]] if len(swing_highs) >= 3 else [h[1] for h in swing_highs[-2:]]
    last_lows = [l[1] for l in swing_lows[-3:]] if len(swing_lows) >= 3 else [l[1] for l in swing_lows[-2:]]
    hh = all(last_highs[i] < last_highs[i+1] for i in range(len(last_highs)-1))
    hl = all(last_lows[i] < last_lows[i+1] for i in range(len(last_lows)-1))
    lh = all(last_highs[i] > last_highs[i+1] for i in range(len(last_highs)-1))
    ll = all(last_lows[i] > last_lows[i+1] for i in range(len(last_lows)-1))
    if hh and hl: return "Uptrend"
    elif lh and ll: return "Downtrend"
    elif hh or hl: return "Weak Uptrend"
    elif lh or ll: return "Weak Downtrend"
    else: return "Sideways"


def find_equal_highs_lows(df, tolerance_pct=0.5, lookback_bars=120):
    recent = df.tail(lookback_bars)
    highs = recent['High'].values
    lows = recent['Low'].values
    equal_highs, equal_lows = [], []
    for i in range(len(highs)):
        for j in range(i+2, len(highs)):
            if abs(highs[i] - highs[j]) / highs[i] * 100 < tolerance_pct:
                equal_highs.append(max(highs[i], highs[j])); break
    for i in range(len(lows)):
        for j in range(i+2, len(lows)):
            if abs(lows[i] - lows[j]) / lows[i] * 100 < tolerance_pct:
                equal_lows.append(min(lows[i], lows[j])); break
    return sorted(set([round(x, 2) for x in equal_highs])), sorted(set([round(x, 2) for x in equal_lows]))


def get_key_levels(df):
    if len(df) < 25: return {}
    pdh = df['High'].iloc[-2] if len(df) >= 2 else None
    pdl = df['Low'].iloc[-2] if len(df) >= 2 else None
    week_ago = df.iloc[-10:-5]
    pwh = week_ago['High'].max() if len(week_ago) > 0 else None
    pwl = week_ago['Low'].min() if len(week_ago) > 0 else None
    if len(df) >= 40:
        month_ago = df.iloc[-40:-20]
        pmh = month_ago['High'].max(); pml = month_ago['Low'].min()
    else: pmh = pml = None
    return {"PDH": pdh, "PDL": pdl, "PWH": pwh, "PWL": pwl, "PMH": pmh, "PML": pml}


# ============ MAJOR LIQUIDITY DETECTION ============

def check_recently_swept(df, level, side='low', lookback=10):
    """
    Check if this level was ALREADY swept in recent candles.
    IMPORTANT: If swept, level is USELESS as target — must exclude!
    """
    recent = df.tail(lookback)
    for idx, row in recent.iterrows():
        if side == 'low':
            # SSL: sweep if low went below level (regardless of close)
            if row['Low'] < level * 0.998:  # 0.2% below
                return True
        else:
            # BSL: sweep if high went above level
            if row['High'] > level * 1.002:  # 0.2% above
                return True
    return False


def find_untapped_major_liquidity(df, lookback_sweep=15):
    """
    Find MAJOR liquidity levels that are STILL UNTAPPED.
    Returns:
        - BSL levels above current price (not yet swept)
        - SSL levels below current price (not yet swept)
    """
    current_price = df['Close'].iloc[-1]

    # Equal Highs / Lows (strongest liquidity — retail stop clusters)
    eq_highs, eq_lows = find_equal_highs_lows(df, tolerance_pct=0.5, lookback_bars=180)

    # Major Swing Highs / Lows (last 6 months structure)
    swing_highs, swing_lows = find_swings(df, lookback=7)
    major_swing_highs = list(set([round(h[1], 2) for h in swing_highs[-15:]]))
    major_swing_lows = list(set([round(l[1], 2) for l in swing_lows[-15:]]))

    # Key levels
    levels = get_key_levels(df)

    # 52-week extremes
    wk52_high = df['High'].tail(250).max() if len(df) >= 250 else df['High'].max()
    wk52_low = df['Low'].tail(250).min() if len(df) >= 250 else df['Low'].min()

    # Collect BSL candidates (above price only)
    bsl_pool = []
    for h in eq_highs:
        if h > current_price:
            bsl_pool.append((h, 'Equal Highs', 3))  # Strength 3 = highest
    for h in major_swing_highs:
        if h > current_price:
            bsl_pool.append((h, 'Swing High', 2))
    if levels.get('PWH') and levels['PWH'] > current_price:
        bsl_pool.append((levels['PWH'], 'PWH', 2))
    if levels.get('PMH') and levels['PMH'] > current_price:
        bsl_pool.append((levels['PMH'], 'PMH', 2))
    if wk52_high > current_price:
        bsl_pool.append((wk52_high, '52W High', 3))

    # SSL candidates (below price only)
    ssl_pool = []
    for l in eq_lows:
        if l < current_price:
            ssl_pool.append((l, 'Equal Lows', 3))
    for l in major_swing_lows:
        if l < current_price:
            ssl_pool.append((l, 'Swing Low', 2))
    if levels.get('PWL') and levels['PWL'] < current_price:
        ssl_pool.append((levels['PWL'], 'PWL', 2))
    if levels.get('PML') and levels['PML'] < current_price:
        ssl_pool.append((levels['PML'], 'PML', 2))
    if wk52_low < current_price:
        ssl_pool.append((wk52_low, '52W Low', 3))

    # FILTER: Remove already-swept levels
    untapped_bsl = []
    for level, kind, strength in bsl_pool:
        if not check_recently_swept(df, level, 'high', lookback=15):
            untapped_bsl.append((level, kind, strength))

    untapped_ssl = []
    for level, kind, strength in ssl_pool:
        if not check_recently_swept(df, level, 'low', lookback=15):
            untapped_ssl.append((level, kind, strength))

    # Cluster nearby levels (within 1%)
    def cluster(items, ascending=True):
        if not items: return []
        sorted_items = sorted(items, key=lambda x: x[0], reverse=not ascending)
        clusters = []
        current = [sorted_items[0]]
        for item in sorted_items[1:]:
            avg_price = sum(c[0] for c in current) / len(current)
            if abs(item[0] - avg_price) / avg_price < 0.01:  # within 1%
                current.append(item)
            else:
                best = max(current, key=lambda x: x[2])
                clusters.append(best)
                current = [item]
        if current:
            best = max(current, key=lambda x: x[2])
            clusters.append(best)
        return clusters

    return cluster(untapped_bsl, True), cluster(untapped_ssl, False)


def calculate_approach_score(current_price, bsl_list, ssl_list):
    """
    Calculate how close price is to MAJOR untapped liquidity.
    Returns:
        - proximity_score (0-100): higher = closer to liquidity
        - nearest_level: (price, kind, strength, distance%, direction)
        - trade_direction: 'SHORT' if approaching BSL, 'LONG' if approaching SSL
    """
    nearest_bsl = None
    nearest_ssl = None

    if bsl_list:
        # Nearest BSL above
        bsl_list_sorted = sorted(bsl_list, key=lambda x: x[0])
        nearest_bsl = bsl_list_sorted[0]  # closest above
        bsl_dist_pct = ((nearest_bsl[0] - current_price) / current_price) * 100
    else:
        bsl_dist_pct = 999

    if ssl_list:
        # Nearest SSL below
        ssl_list_sorted = sorted(ssl_list, key=lambda x: x[0], reverse=True)
        nearest_ssl = ssl_list_sorted[0]  # closest below
        ssl_dist_pct = ((current_price - nearest_ssl[0]) / current_price) * 100
    else:
        ssl_dist_pct = 999

    # Which is closer?
    if bsl_dist_pct < ssl_dist_pct:
        # Approaching BSL from below → potential SHORT setup after sweep
        nearest = nearest_bsl
        distance = bsl_dist_pct
        direction = 'SHORT'
        liq_type = 'BSL'
    else:
        # Approaching SSL from above → potential LONG setup after sweep
        nearest = nearest_ssl
        distance = ssl_dist_pct
        direction = 'LONG'
        liq_type = 'SSL'

    # Proximity score (0-100)
    # 0% distance = 100 score
    # 1.5% distance = 0 score (max range)
    if distance <= 1.5:
        proximity_score = int(100 * (1 - distance / 1.5))
    else:
        proximity_score = 0

    # Alert stages — TIGHT ranges for immediate action
    if distance < 0.3:
        stage = "AT LEVEL"
        stage_emoji = "🎯"
    elif distance < 0.8:
        stage = "VERY CLOSE"
        stage_emoji = "🔥"
    elif distance < 1.5:
        stage = "CLOSE"
        stage_emoji = "⚡"
    else:
        stage = "FAR"
        stage_emoji = "⏳"

    return {
        "proximity_score": proximity_score,
        "nearest_level": nearest,
        "distance_pct": round(distance, 2),
        "direction": direction,
        "liq_type": liq_type,
        "stage": stage,
        "stage_emoji": stage_emoji,
    }


def calculate_reversal_trade_plan(current_price, approach_info, df, is_large_cap=True):
    """
    Reversal-based trade plan:
    - We're WAITING for sweep, so trade is anticipation
    - Entry: After sweep + rejection (mental note current level)
    - SL: On opposite side of major liquidity (if wrong)
    - TP: Next major opposite liquidity
    """
    direction = approach_info['direction']
    nearest_level = approach_info['nearest_level']  # (price, kind, strength)
    if not nearest_level:
        return None

    liq_price = nearest_level[0]
    buffer_pct = 0.005 if is_large_cap else 0.010  # 0.5% or 1%

    if direction == 'SHORT':
        # Approaching BSL from below → wait for BSL sweep → SHORT
        # Anticipated entry: Just below BSL after sweep rejection
        entry_zone_low = liq_price * 0.998
        entry_zone_high = liq_price * 1.002
        # SL: Above sweep wick (add buffer for the wick + safety)
        sl = liq_price * (1 + buffer_pct * 3)  # ~1.5% above liquidity
        # TP: Next SSL below current price (need to find from full liquidity map)
        bsl, ssl = find_untapped_major_liquidity(df)
        if ssl:
            ssl_sorted = sorted(ssl, key=lambda x: x[0], reverse=True)
            tp1 = ssl_sorted[0][0]
            tp2 = ssl_sorted[1][0] if len(ssl_sorted) >= 2 else current_price * 0.92
        else:
            tp1 = current_price * 0.97
            tp2 = current_price * 0.94

    else:  # LONG
        # Approaching SSL from above → wait for SSL sweep → LONG
        entry_zone_low = liq_price * 0.998
        entry_zone_high = liq_price * 1.002
        sl = liq_price * (1 - buffer_pct * 3)
        bsl, ssl = find_untapped_major_liquidity(df)
        if bsl:
            bsl_sorted = sorted(bsl, key=lambda x: x[0])
            tp1 = bsl_sorted[0][0]
            tp2 = bsl_sorted[1][0] if len(bsl_sorted) >= 2 else current_price * 1.08
        else:
            tp1 = current_price * 1.03
            tp2 = current_price * 1.06

    # R:R calculation (from anticipated entry near liquidity)
    entry_ref = liq_price  # anticipated entry price
    if direction == 'SHORT':
        risk = sl - entry_ref
        reward1 = entry_ref - tp1
        reward2 = entry_ref - tp2
    else:
        risk = entry_ref - sl
        reward1 = tp1 - entry_ref
        reward2 = tp2 - entry_ref

    rr1 = reward1 / risk if risk > 0 else 0
    rr2 = reward2 / risk if risk > 0 else 0

    return {
        "entry_zone": [round(entry_zone_low, 2), round(entry_zone_high, 2)],
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "risk_pct": round((risk / entry_ref) * 100, 2),
        "rr1": round(rr1, 2),
        "rr2": round(rr2, 2),
    }


def fetch_history(symbol, period="1y", retries=3):
    for attempt in range(retries):
        try:
            t = yf.Ticker(symbol)
            df = t.history(period=period, interval="1d", auto_adjust=True)
            if len(df) >= 60:
                return df
        except Exception:
            pass
        if attempt < retries - 1:
            time.sleep(random.uniform(1.0, 2.5))
    return None


def scan_stock(symbol, info, include_fundamental=True, min_fund_score=3):
    """
    v4 Scan: PROACTIVE liquidity approach detection
    Only return stocks that:
    1. Are fundamentally strong (Fund score >= min_fund_score)
    2. Are approaching a major untapped liquidity level
    """
    try:
        df = fetch_history(symbol, period="1y")
        if df is None or len(df) < 60:
            return None
        current_price = df['Close'].iloc[-1]
        change_pct = ((df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100 if len(df) >= 2 else 0

        # ============ 1. FIND UNTAPPED LIQUIDITY ============
        untapped_bsl, untapped_ssl = find_untapped_major_liquidity(df)

        if not untapped_bsl and not untapped_ssl:
            return None  # No untapped liquidity nearby — skip

        # ============ 2. CALCULATE APPROACH ============
        approach = calculate_approach_score(current_price, untapped_bsl, untapped_ssl)

        # SKIP if too far from any liquidity (TIGHT: max 1.5%)
        if approach['distance_pct'] > 1.5:
            return None  # More than 1.5% away — not actionable yet

        # ============ 3. FUNDAMENTAL CHECK (MANDATORY) ============
        fund_data = None
        fund_score = 0
        fund_reasons = []
        if include_fundamental:
            fund_result = get_fundamental_score(info["symbol"])
            fund_score = fund_result["score"]
            fund_data = fund_result["data"]
            fund_reasons = fund_result["reasons"]

            # STRICT FILTER: Must be fundamentally strong for big players to bother
            if fund_score < min_fund_score:
                return None

        # ============ 4. TRADE PLAN (Reversal Anticipation) ============
        is_large_cap = current_price > 500 and current_price < 10000
        trade_plan = calculate_reversal_trade_plan(current_price, approach, df, is_large_cap)
        if not trade_plan:
            return None

        # Skip weak R:R
        if trade_plan['rr1'] < 1.5:
            return None

        # ============ 5. ADDITIONAL CONTEXT ============
        trend_daily = detect_trend(df, lookback=5)
        df_weekly = df.resample('W').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
        trend_weekly = detect_trend(df_weekly, lookback=3) if len(df_weekly) >= 10 else "Unknown"

        wk52_high = df['High'].tail(250).max() if len(df) >= 250 else df['High'].max()
        wk52_low = df['Low'].tail(250).min() if len(df) >= 250 else df['Low'].min()

        # Alert priority score
        priority = (approach['proximity_score'] * 0.6) + (fund_score * 25 * 0.4)

        return {
            "symbol": info["symbol"],
            "name": info["name"],
            "sector": info["sector"],
            "price": round(current_price, 2),
            "change_pct": round(change_pct, 2),

            # Approach info
            "stage": approach['stage'],
            "stage_emoji": approach['stage_emoji'],
            "distance_pct": approach['distance_pct'],
            "target_level": round(approach['nearest_level'][0], 2),
            "target_level_type": approach['nearest_level'][1],
            "liq_type": approach['liq_type'],
            "direction": approach['direction'],
            "proximity_score": approach['proximity_score'],
            "priority": round(priority, 1),

            # Fundamental
            "fund_score": fund_score,
            "fund_data": fund_data,
            "fund_reasons": fund_reasons,

            # Trade plan (reversal-based)
            "entry_zone": trade_plan['entry_zone'],
            "sl": trade_plan['sl'],
            "tp1": trade_plan['tp1'],
            "tp2": trade_plan['tp2'],
            "risk_pct": trade_plan['risk_pct'],
            "rr1": trade_plan['rr1'],
            "rr2": trade_plan['rr2'],

            # Context
            "trend_daily": trend_daily,
            "trend_weekly": trend_weekly,
            "wk52_high": round(wk52_high, 2),
            "wk52_low": round(wk52_low, 2),
            "bsl_levels": [(round(x[0], 2), x[1]) for x in untapped_bsl[:3]],
            "ssl_levels": [(round(x[0], 2), x[1]) for x in untapped_ssl[:3]],
        }
    except Exception as e:
        return None

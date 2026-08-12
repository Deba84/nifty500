"""
Scanner v6 — ULTRA FAST (Batch Download Optimization)
- Downloads all 500 stocks in ONE batch call (~10 sec vs 15+ min)
- Same v4 logic (liquidity approach + fundamental filter)
- Optimized for GitHub Actions 30-min timeout
"""
import yfinance as yf
import pandas as pd
import numpy as np
import warnings
import time
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


def check_recently_swept(df, level, side='low', lookback=10):
    recent = df.tail(lookback)
    for idx, row in recent.iterrows():
        if side == 'low':
            if row['Low'] < level * 0.998:
                return True
        else:
            if row['High'] > level * 1.002:
                return True
    return False


def find_untapped_major_liquidity(df):
    current_price = df['Close'].iloc[-1]
    eq_highs, eq_lows = find_equal_highs_lows(df, tolerance_pct=0.5, lookback_bars=180)
    swing_highs, swing_lows = find_swings(df, lookback=7)
    major_swing_highs = list(set([round(h[1], 2) for h in swing_highs[-15:]]))
    major_swing_lows = list(set([round(l[1], 2) for l in swing_lows[-15:]]))
    levels = get_key_levels(df)
    wk52_high = df['High'].tail(250).max() if len(df) >= 250 else df['High'].max()
    wk52_low = df['Low'].tail(250).min() if len(df) >= 250 else df['Low'].min()

    bsl_pool = []
    for h in eq_highs:
        if h > current_price:
            bsl_pool.append((h, 'Equal Highs', 3))
    for h in major_swing_highs:
        if h > current_price:
            bsl_pool.append((h, 'Swing High', 2))
    if levels.get('PWH') and levels['PWH'] > current_price:
        bsl_pool.append((levels['PWH'], 'PWH', 2))
    if levels.get('PMH') and levels['PMH'] > current_price:
        bsl_pool.append((levels['PMH'], 'PMH', 2))
    if wk52_high > current_price:
        bsl_pool.append((wk52_high, '52W High', 3))

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

    untapped_bsl = []
    for level, kind, strength in bsl_pool:
        if not check_recently_swept(df, level, 'high', lookback=15):
            untapped_bsl.append((level, kind, strength))
    untapped_ssl = []
    for level, kind, strength in ssl_pool:
        if not check_recently_swept(df, level, 'low', lookback=15):
            untapped_ssl.append((level, kind, strength))

    def cluster(items, ascending=True):
        if not items: return []
        sorted_items = sorted(items, key=lambda x: x[0], reverse=not ascending)
        clusters = []
        current = [sorted_items[0]]
        for item in sorted_items[1:]:
            avg_price = sum(c[0] for c in current) / len(current)
            if abs(item[0] - avg_price) / avg_price < 0.01:
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
    nearest_bsl = None
    nearest_ssl = None
    if bsl_list:
        nearest_bsl = sorted(bsl_list, key=lambda x: x[0])[0]
        bsl_dist_pct = ((nearest_bsl[0] - current_price) / current_price) * 100
    else:
        bsl_dist_pct = 999
    if ssl_list:
        nearest_ssl = sorted(ssl_list, key=lambda x: x[0], reverse=True)[0]
        ssl_dist_pct = ((current_price - nearest_ssl[0]) / current_price) * 100
    else:
        ssl_dist_pct = 999

    if bsl_dist_pct < ssl_dist_pct:
        nearest = nearest_bsl
        distance = bsl_dist_pct
        direction = 'SHORT'
        liq_type = 'BSL'
    else:
        nearest = nearest_ssl
        distance = ssl_dist_pct
        direction = 'LONG'
        liq_type = 'SSL'

    if distance <= 1.5:
        proximity_score = int(100 * (1 - distance / 1.5))
    else:
        proximity_score = 0

    if distance < 0.3:
        stage = "AT LEVEL"; stage_emoji = "🎯"
    elif distance < 0.8:
        stage = "VERY CLOSE"; stage_emoji = "🔥"
    elif distance < 1.5:
        stage = "CLOSE"; stage_emoji = "⚡"
    else:
        stage = "FAR"; stage_emoji = "⏳"

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
    direction = approach_info['direction']
    nearest_level = approach_info['nearest_level']
    if not nearest_level:
        return None
    liq_price = nearest_level[0]
    buffer_pct = 0.005 if is_large_cap else 0.010

    if direction == 'SHORT':
        entry_zone_low = liq_price * 0.998
        entry_zone_high = liq_price * 1.002
        sl = liq_price * (1 + buffer_pct * 3)
        bsl, ssl = find_untapped_major_liquidity(df)
        if ssl:
            ssl_sorted = sorted(ssl, key=lambda x: x[0], reverse=True)
            tp1 = ssl_sorted[0][0]
            tp2 = ssl_sorted[1][0] if len(ssl_sorted) >= 2 else current_price * 0.92
        else:
            tp1 = current_price * 0.97
            tp2 = current_price * 0.94
    else:
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

    entry_ref = liq_price
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


# ============ BATCH DOWNLOAD (KEY OPTIMIZATION!) ============

def batch_download_all(symbols_list, period="1y"):
    """
    Download ALL stocks in ONE batch call.
    500 stocks in ~10 seconds vs 500+ seconds individual!
    """
    print(f"⚡ Batch downloading {len(symbols_list)} stocks (period={period})...")
    start = time.time()

    try:
        data = yf.download(
            symbols_list,
            period=period,
            interval="1d",
            progress=False,
            group_by='ticker',
            threads=True,
            auto_adjust=True,
        )
        elapsed = time.time() - start
        print(f"✅ Batch download done in {elapsed:.1f}s")

        # Split back into individual dataframes
        result = {}
        for sym in symbols_list:
            try:
                if len(symbols_list) == 1:
                    df = data
                else:
                    df = data[sym].dropna()
                if len(df) >= 60:
                    result[sym] = df
            except Exception:
                continue
        print(f"✅ Extracted {len(result)}/{len(symbols_list)} valid dataframes")
        return result
    except Exception as e:
        print(f"❌ Batch failed: {e}")
        return {}


def analyze_stock_from_df(df, info):
    """Analyze pre-downloaded dataframe (no yfinance call)."""
    try:
        if df is None or len(df) < 60:
            return None
        current_price = df['Close'].iloc[-1]
        change_pct = ((df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100 if len(df) >= 2 else 0

        # 1. Find untapped liquidity
        untapped_bsl, untapped_ssl = find_untapped_major_liquidity(df)
        if not untapped_bsl and not untapped_ssl:
            return None

        # 2. Approach check
        approach = calculate_approach_score(current_price, untapped_bsl, untapped_ssl)
        if approach['distance_pct'] > 1.5:
            return None  # too far

        # 3. Trade plan
        is_large_cap = current_price > 500 and current_price < 10000
        trade_plan = calculate_reversal_trade_plan(current_price, approach, df, is_large_cap)
        if not trade_plan or trade_plan['rr1'] < 1.5:
            return None

        # 4. Context
        trend_daily = detect_trend(df, lookback=5)
        df_weekly = df.resample('W').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
        trend_weekly = detect_trend(df_weekly, lookback=3) if len(df_weekly) >= 10 else "Unknown"

        wk52_high = df['High'].tail(250).max() if len(df) >= 250 else df['High'].max()
        wk52_low = df['Low'].tail(250).min() if len(df) >= 250 else df['Low'].min()

        return {
            "symbol": info["symbol"],
            "name": info["name"],
            "sector": info["sector"],
            "price": round(current_price, 2),
            "change_pct": round(change_pct, 2),
            "stage": approach['stage'],
            "stage_emoji": approach['stage_emoji'],
            "distance_pct": approach['distance_pct'],
            "target_level": round(approach['nearest_level'][0], 2),
            "target_level_type": approach['nearest_level'][1],
            "liq_type": approach['liq_type'],
            "direction": approach['direction'],
            "proximity_score": approach['proximity_score'],
            "entry_zone": trade_plan['entry_zone'],
            "sl": trade_plan['sl'],
            "tp1": trade_plan['tp1'],
            "tp2": trade_plan['tp2'],
            "risk_pct": trade_plan['risk_pct'],
            "rr1": trade_plan['rr1'],
            "rr2": trade_plan['rr2'],
            "trend_daily": trend_daily,
            "trend_weekly": trend_weekly,
            "wk52_high": round(wk52_high, 2),
            "wk52_low": round(wk52_low, 2),
            "fund_score": 0,
            "fund_data": None,
            "priority": approach['proximity_score'],
        }
    except Exception:
        return None

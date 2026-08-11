"""Scanner engine v3 — LIQUIDITY-BASED Entry/SL/TP (PDF playbook match)."""
import yfinance as yf
import pandas as pd
import numpy as np
import warnings
import time
import random
warnings.filterwarnings('ignore')

from nifty500_list import get_symbol_map
from fundamental_analyzer import get_fundamental_score


# ============ CORE STRUCTURE FUNCTIONS ============

def find_swings(df, lookback=5):
    """Find swing highs and lows using fractal method."""
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
        return "Unclear", 0
    last_highs = [h[1] for h in swing_highs[-3:]] if len(swing_highs) >= 3 else [h[1] for h in swing_highs[-2:]]
    last_lows = [l[1] for l in swing_lows[-3:]] if len(swing_lows) >= 3 else [l[1] for l in swing_lows[-2:]]
    hh = all(last_highs[i] < last_highs[i+1] for i in range(len(last_highs)-1))
    hl = all(last_lows[i] < last_lows[i+1] for i in range(len(last_lows)-1))
    lh = all(last_highs[i] > last_highs[i+1] for i in range(len(last_highs)-1))
    ll = all(last_lows[i] > last_lows[i+1] for i in range(len(last_lows)-1))
    if hh and hl: return "Uptrend", 100
    elif lh and ll: return "Downtrend", 100
    elif hh or hl: return "Weak Uptrend", 60
    elif lh or ll: return "Weak Downtrend", 60
    else: return "Sideways", 30


def find_equal_highs_lows(df, tolerance_pct=0.5, lookback_bars=60):
    """Find equal highs (BSL) and equal lows (SSL) — liquidity pools."""
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


def detect_liquidity_sweep(df, level, side='low', lookback=5):
    """Detect sweep with candle info returned (for SL placement)."""
    recent = df.tail(lookback)
    for idx, (i, row) in enumerate(recent.iterrows()):
        if side == 'low':
            if row['Low'] < level and row['Close'] > level:
                wick = level - row['Low']
                body = abs(row['Close'] - row['Open'])
                if wick > body * 0.5:
                    return True, level, row['Low'], row['High']  # sweep_low is the wick extreme
        else:
            if row['High'] > level and row['Close'] < level:
                wick = row['High'] - level
                body = abs(row['Close'] - row['Open'])
                if wick > body * 0.5:
                    return True, level, row['Low'], row['High']  # sweep_high is the wick extreme
    return False, None, None, None


def detect_choch(df, lookback=5):
    swing_highs, swing_lows = find_swings(df, lookback)
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return None
    current_price = df['Close'].iloc[-1]
    last_high = swing_highs[-1][1]
    last_low = swing_lows[-1][1]
    prev_high = swing_highs[-2][1] if len(swing_highs) >= 2 else None
    prev_low = swing_lows[-2][1] if len(swing_lows) >= 2 else None
    if prev_high and last_high < prev_high and current_price > last_high:
        return "Bullish CHoCH"
    if prev_low and last_low > prev_low and current_price < last_low:
        return "Bearish CHoCH"
    return None


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


# ============ LIQUIDITY-BASED ENTRY/SL/TP ============

def find_major_liquidity_levels(df, min_distance_pct=1.5, cluster_pct=1.0):
    """
    MAJOR Liquidity levels only:
    - Filter out minor noise (min 1.5% away from current price)
    - Cluster nearby levels (within 1%) into single zone
    - Weight by strength (equal highs > swing highs > single points)
    """
    current_price = df['Close'].iloc[-1]

    # Weekly & Monthly swings (bigger structure)
    swing_highs, swing_lows = find_swings(df, lookback=7)  # bigger lookback = major swings
    major_swing_highs = [h[1] for h in swing_highs[-10:]]
    major_swing_lows = [l[1] for l in swing_lows[-10:]]

    # Equal highs/lows (strong retail stop clusters — HIGH PRIORITY)
    eq_highs, eq_lows = find_equal_highs_lows(df, tolerance_pct=0.5, lookback_bars=120)

    # Key levels
    levels = get_key_levels(df)

    # 52-week
    wk52_high = df['High'].tail(250).max() if len(df) >= 250 else df['High'].max()
    wk52_low = df['Low'].tail(250).min() if len(df) >= 250 else df['Low'].min()

    # Collect BSL (above price) with strength weights
    bsl_candidates = []
    # Equal highs = strongest (2x weight for clustering)
    for h in eq_highs:
        if h > current_price * (1 + min_distance_pct/100):
            bsl_candidates.append((h, 3))  # strength 3
    # Major swing highs
    for h in major_swing_highs:
        if h > current_price * (1 + min_distance_pct/100):
            bsl_candidates.append((h, 2))
    # Key day/week/month highs
    for name in ['PDH', 'PWH', 'PMH']:
        v = levels.get(name)
        if v and v > current_price * (1 + min_distance_pct/100):
            bsl_candidates.append((v, 2))
    if wk52_high > current_price * (1 + min_distance_pct/100):
        bsl_candidates.append((wk52_high, 3))  # 52W high is very important

    # SSL (below price)
    ssl_candidates = []
    for l in eq_lows:
        if l < current_price * (1 - min_distance_pct/100):
            ssl_candidates.append((l, 3))
    for l in major_swing_lows:
        if l < current_price * (1 - min_distance_pct/100):
            ssl_candidates.append((l, 2))
    for name in ['PDL', 'PWL', 'PML']:
        v = levels.get(name)
        if v and v < current_price * (1 - min_distance_pct/100):
            ssl_candidates.append((v, 2))
    if wk52_low < current_price * (1 - min_distance_pct/100):
        ssl_candidates.append((wk52_low, 3))

    # Cluster nearby levels (within cluster_pct)
    def cluster_levels(candidates, ascending=True):
        if not candidates:
            return []
        sorted_c = sorted(candidates, key=lambda x: x[0], reverse=not ascending)
        clusters = []
        current_cluster = [sorted_c[0]]
        for level, strength in sorted_c[1:]:
            avg = sum(l for l, s in current_cluster) / len(current_cluster)
            if abs(level - avg) / avg * 100 < cluster_pct:
                current_cluster.append((level, strength))
            else:
                # Save current cluster (use max strength level as representative)
                best = max(current_cluster, key=lambda x: x[1])
                clusters.append(best[0])
                current_cluster = [(level, strength)]
        if current_cluster:
            best = max(current_cluster, key=lambda x: x[1])
            clusters.append(best[0])
        return clusters

    major_bsl = cluster_levels(bsl_candidates, ascending=True)
    major_ssl = cluster_levels(ssl_candidates, ascending=False)

    return [round(x, 2) for x in major_bsl], [round(x, 2) for x in major_ssl], wk52_high, wk52_low


# Backward compat alias
def find_all_liquidity_levels(df):
    return find_major_liquidity_levels(df)


def calculate_liquidity_based_levels(df, direction='long', is_large_cap=True):
    """
    LIQUIDITY-BASED Entry/SL/TP calculation.
    
    Rules (from PDF playbook):
    - Entry = Current close (or CHoCH break candle)
    - SL = Beyond sweep wick extreme + buffer
    - TP1 = Next major opposite liquidity (partial 50%)
    - TP2 = Second major opposite liquidity (remaining)
    - Minimum R:R filter: 1:2 (else skip)
    """
    current_price = df['Close'].iloc[-1]
    all_bsl, all_ssl, wk52_high, wk52_low = find_all_liquidity_levels(df)

    # Buffer: large-cap 0.3%, mid/small-cap 0.7%
    buffer_pct = 0.003 if is_large_cap else 0.007

    result = {
        "entry": round(current_price, 2),
        "direction": direction,
        "all_bsl_levels": all_bsl[:5],  # 5 closest BSL levels
        "all_ssl_levels": all_ssl[:5],  # 5 closest SSL levels
    }

    if direction == 'long':
        # ============ SL Calculation ============
        # Priority 1: Sweep wick (if bullish sweep detected)
        sweep_low = None
        # Check equal lows for sweep
        eq_highs, eq_lows = find_equal_highs_lows(df)
        for lvl in eq_lows + [get_key_levels(df).get("PDL"), get_key_levels(df).get("PWL")]:
            if lvl:
                swept, level, sw_low, sw_high = detect_liquidity_sweep(df, lvl, 'low', lookback=5)
                if swept:
                    sweep_low = sw_low
                    break

        if sweep_low is not None:
            # SL below sweep wick + buffer
            sl = sweep_low * (1 - buffer_pct)
            sl_reason = f"Below sweep wick low (₹{sweep_low:.2f})"
        else:
            # Fallback: nearest SSL - buffer
            if all_ssl:
                nearest_ssl = all_ssl[0]  # closest SSL below price
                sl = nearest_ssl * (1 - buffer_pct)
                sl_reason = f"Below nearest SSL (₹{nearest_ssl:.2f})"
            else:
                # Last resort: recent low
                sl = df['Low'].tail(10).min() * (1 - buffer_pct)
                sl_reason = "Below 10-day low"

        risk = current_price - sl

        # ============ TP Calculation — LIQUIDITY TARGETS ============
        # TP1: nearest BSL (first opposite liquidity)
        # TP2: second BSL (major target)
        if len(all_bsl) >= 2:
            tp1 = all_bsl[0]
            tp2 = all_bsl[1]
            tp_reason = f"TP1={all_bsl[0]:.2f} (nearest BSL), TP2={all_bsl[1]:.2f} (next liquidity)"
        elif len(all_bsl) == 1:
            tp1 = all_bsl[0]
            tp2 = wk52_high if wk52_high > tp1 else tp1 * 1.05
            tp_reason = f"TP1={all_bsl[0]:.2f} (only BSL), TP2=52W High"
        else:
            # No BSL above — use fixed 1:3
            tp1 = current_price + (risk * 2)
            tp2 = current_price + (risk * 3)
            tp_reason = "No BSL above — using 1:2 and 1:3 R:R"

    else:  # SHORT
        # ============ SL Calculation ============
        sweep_high = None
        eq_highs, eq_lows = find_equal_highs_lows(df)
        for lvl in eq_highs + [get_key_levels(df).get("PDH"), get_key_levels(df).get("PWH")]:
            if lvl:
                swept, level, sw_low, sw_high = detect_liquidity_sweep(df, lvl, 'high', lookback=5)
                if swept:
                    sweep_high = sw_high
                    break

        if sweep_high is not None:
            sl = sweep_high * (1 + buffer_pct)
            sl_reason = f"Above sweep wick high (₹{sweep_high:.2f})"
        else:
            if all_bsl:
                nearest_bsl = all_bsl[0]
                sl = nearest_bsl * (1 + buffer_pct)
                sl_reason = f"Above nearest BSL (₹{nearest_bsl:.2f})"
            else:
                sl = df['High'].tail(10).max() * (1 + buffer_pct)
                sl_reason = "Above 10-day high"

        risk = sl - current_price

        # TP: SSL targets
        if len(all_ssl) >= 2:
            tp1 = all_ssl[0]
            tp2 = all_ssl[1]
            tp_reason = f"TP1={all_ssl[0]:.2f} (nearest SSL), TP2={all_ssl[1]:.2f} (next liquidity)"
        elif len(all_ssl) == 1:
            tp1 = all_ssl[0]
            tp2 = wk52_low if wk52_low < tp1 else tp1 * 0.95
            tp_reason = f"TP1={all_ssl[0]:.2f} (only SSL), TP2=52W Low"
        else:
            tp1 = current_price - (risk * 2)
            tp2 = current_price - (risk * 3)
            tp_reason = "No SSL below — using 1:2 and 1:3 R:R"

    # ============ R:R Calculation ============
    if direction == 'long':
        reward1 = tp1 - current_price
        reward2 = tp2 - current_price
    else:
        reward1 = current_price - tp1
        reward2 = current_price - tp2

    rr1 = reward1 / risk if risk > 0 else 0
    rr2 = reward2 / risk if risk > 0 else 0
    risk_pct = (risk / current_price) * 100

    result.update({
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "risk": round(risk, 2),
        "risk_pct": round(risk_pct, 2),
        "rr1": round(rr1, 2),
        "rr2": round(rr2, 2),
        "sl_reason": sl_reason,
        "tp_reason": tp_reason,
    })

    return result


# ============ TECHNICAL SCORING ============

def calculate_technical_score(df, info):
    score = 0
    signals = []
    reasons = []
    if len(df) < 60: return 0, [], []
    current_price = df['Close'].iloc[-1]
    volume_avg = df['Volume'].tail(20).mean()
    current_volume = df['Volume'].iloc[-1]

    trend, _ = detect_trend(df, lookback=5)
    if trend == "Uptrend":
        score += 2; signals.append(("Trend", "🟢 Strong Uptrend", "pass")); reasons.append("Daily uptrend")
    elif trend == "Downtrend":
        score += 1; signals.append(("Trend", "🔴 Strong Downtrend", "pass-short")); reasons.append("Daily downtrend")
    elif "Weak" in trend:
        score += 1; signals.append(("Trend", f"🟡 {trend}", "warn"))
    else:
        signals.append(("Trend", "⚪ Sideways", "neutral"))

    levels = get_key_levels(df)
    near_level = None
    for name, lvl in levels.items():
        if lvl and abs(current_price - lvl) / current_price < 0.02:
            near_level = (name, lvl); break
    if near_level:
        score += 2; signals.append(("Key Level", f"🎯 Near {near_level[0]} ₹{near_level[1]:.2f}", "pass"))
        reasons.append(f"Near {near_level[0]}")
    else:
        signals.append(("Key Level", "⚪ No major level", "neutral"))

    eq_highs, eq_lows = find_equal_highs_lows(df)
    liq_note = ""
    if eq_highs:
        untapped = [h for h in eq_highs if h > current_price and (h - current_price)/current_price < 0.10]
        if untapped: liq_note += f"BSL ₹{min(untapped):.2f} above. "
    if eq_lows:
        untapped = [l for l in eq_lows if l < current_price and (current_price - l)/current_price < 0.10]
        if untapped: liq_note += f"SSL ₹{max(untapped):.2f} below."
    if liq_note:
        score += 1; signals.append(("Liquidity Pool", f"💧 {liq_note}", "pass"))
    else:
        signals.append(("Liquidity Pool", "⚪ No clear pools", "neutral"))

    sweep_bullish = False; sweep_bearish = False
    for lvl in eq_lows + [levels.get("PDL"), levels.get("PWL")]:
        if lvl:
            swept, _, _, _ = detect_liquidity_sweep(df, lvl, 'low', lookback=5)
            if swept: sweep_bullish = True; break
    for lvl in eq_highs + [levels.get("PDH"), levels.get("PWH")]:
        if lvl:
            swept, _, _, _ = detect_liquidity_sweep(df, lvl, 'high', lookback=5)
            if swept: sweep_bearish = True; break
    if sweep_bullish:
        score += 2; signals.append(("Sweep", "⚡ Bullish Sweep", "pass")); reasons.append("SSL swept")
    elif sweep_bearish:
        score += 2; signals.append(("Sweep", "⚡ Bearish Sweep", "pass-short")); reasons.append("BSL swept")
    else:
        signals.append(("Sweep", "⚪ No recent sweep", "neutral"))

    choch = detect_choch(df, lookback=5)
    if choch:
        score += 2; signals.append(("CHoCH", f"🔄 {choch}", "pass")); reasons.append(choch)
    else:
        signals.append(("CHoCH", "⚪ No CHoCH", "neutral"))

    if current_volume > volume_avg * 1.5:
        score += 1; signals.append(("Volume", "📊 High volume", "pass"))
    else:
        signals.append(("Volume", "⚪ Normal volume", "neutral"))

    ma50 = df['Close'].tail(50).mean()
    if current_price > ma50:
        signals.append(("50D MA", f"🟢 Above ₹{ma50:.2f}", "pass"))
    else:
        signals.append(("50D MA", f"🔴 Below ₹{ma50:.2f}", "warn"))

    return score, signals, reasons


def fetch_history(symbol, period="1y", retries=3):
    """Fetch 1 year data for better liquidity mapping."""
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


def scan_stock(symbol, info, include_fundamental=True):
    """Main scan with LIQUIDITY-BASED Entry/SL/TP."""
    try:
        df = fetch_history(symbol, period="1y")
        if df is None or len(df) < 60:
            return None
        df_weekly = df.resample('W').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
        current_price = df['Close'].iloc[-1]
        change_pct = ((df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100 if len(df) >= 2 else 0

        # Technical
        tech_score, tech_signals, tech_reasons = calculate_technical_score(df, info)
        w_trend, _ = detect_trend(df_weekly, lookback=3) if len(df_weekly) >= 10 else ("Unknown", 0)
        if w_trend == "Uptrend":
            tech_score += 1; tech_signals.append(("Weekly Trend", "🟢 Weekly Uptrend", "pass"))
        elif w_trend == "Downtrend":
            tech_signals.append(("Weekly Trend", "🔴 Weekly Downtrend", "warn"))

        # Fundamental
        fund_score = 0
        fund_signals = []
        fund_reasons = []
        fund_data = None
        if include_fundamental:
            fund_result = get_fundamental_score(info["symbol"])
            fund_score = fund_result["score"]
            fund_signals = fund_result["signals"]
            fund_reasons = fund_result["reasons"]
            fund_data = fund_result["data"]

        total_score = tech_score + fund_score

        # Direction
        bullish = sum(1 for s in tech_signals if s[2] == 'pass')
        bearish = sum(1 for s in tech_signals if s[2] == 'pass-short')
        direction = 'long' if bullish >= bearish else 'short'

        # === LIQUIDITY-BASED LEVELS ===
        # Determine if large cap (price >500 typically) — rough proxy
        is_large_cap = current_price > 500 and current_price < 10000
        levels_data = calculate_liquidity_based_levels(df, direction, is_large_cap)

        wk52_high = df['High'].tail(250).max() if len(df) >= 250 else df['High'].max()
        wk52_low = df['Low'].tail(250).min() if len(df) >= 250 else df['Low'].min()

        # Verdict
        # Additional filter: R:R must be at least 1.5:1 for even A rating
        min_rr_pass = levels_data["rr1"] >= 1.5

        if tech_score >= 8 and fund_score >= 3 and min_rr_pass:
            verdict, vc = "A+ SUPER", "purple"
        elif tech_score >= 8 and min_rr_pass:
            verdict, vc = "A+ SETUP", "gold"
        elif tech_score >= 6 and min_rr_pass:
            verdict, vc = "A SETUP", "green"
        elif tech_score >= 4:
            verdict, vc = "WATCH", "orange"
        else:
            verdict, vc = "SKIP", "red"

        return {
            "symbol": info["symbol"], "name": info["name"], "sector": info["sector"],
            "price": round(current_price, 2), "change_pct": round(change_pct, 2),
            "score": total_score, "tech_score": tech_score, "fund_score": fund_score,
            "verdict": verdict, "verdict_color": vc,
            "direction": direction.upper(),
            "trend_daily": detect_trend(df, 5)[0], "trend_weekly": w_trend,

            # === NEW: Liquidity-based levels ===
            "entry": levels_data["entry"],
            "sl": levels_data["sl"],
            "tp1": levels_data["tp1"],
            "tp2": levels_data["tp2"],
            "risk_pct": levels_data["risk_pct"],
            "rr1": levels_data["rr1"],
            "rr2": levels_data["rr2"],
            "sl_reason": levels_data["sl_reason"],
            "tp_reason": levels_data["tp_reason"],
            "bsl_levels": levels_data["all_bsl_levels"],
            "ssl_levels": levels_data["all_ssl_levels"],

            "signals": tech_signals,
            "fund_signals": fund_signals,
            "reasons": tech_reasons + fund_reasons,
            "fund_data": fund_data,
            "wk52_high": round(wk52_high, 2), "wk52_low": round(wk52_low, 2),
            "dist_52wh_pct": round(((wk52_high - current_price) / current_price) * 100, 1),
        }
    except Exception:
        return None

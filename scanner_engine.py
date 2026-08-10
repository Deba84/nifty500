"""Scanner engine with anti-block session (curl_cffi + browser impersonation)."""
import yfinance as yf
import pandas as pd
import numpy as np
import warnings
import time
import random
warnings.filterwarnings('ignore')

from nifty500_list import get_symbol_map

# Latest yfinance handles anti-block internally when curl_cffi is installed
# No need for custom session - yfinance auto-detects it


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
    return list(set([round(x, 2) for x in equal_highs])), list(set([round(x, 2) for x in equal_lows]))


def detect_liquidity_sweep(df, level, side='low', lookback=5):
    recent = df.tail(lookback)
    for idx, row in recent.iterrows():
        if side == 'low':
            if row['Low'] < level and row['Close'] > level:
                wick = level - row['Low']
                body = abs(row['Close'] - row['Open'])
                if wick > body * 0.5: return True, level, idx
        else:
            if row['High'] > level and row['Close'] < level:
                wick = row['High'] - level
                body = abs(row['Close'] - row['Open'])
                if wick > body * 0.5: return True, level, idx
    return False, None, None


def detect_choch(df, lookback=5):
    swing_highs, swing_lows = find_swings(df, lookback)
    if len(swing_highs) < 2 or len(swing_lows) < 2: return None
    current_price = df['Close'].iloc[-1]
    last_high = swing_highs[-1][1]
    last_low = swing_lows[-1][1]
    prev_high = swing_highs[-2][1] if len(swing_highs) >= 2 else None
    prev_low = swing_lows[-2][1] if len(swing_lows) >= 2 else None
    if prev_high and last_high < prev_high and current_price > last_high: return "Bullish CHoCH"
    if prev_low and last_low > prev_low and current_price < last_low: return "Bearish CHoCH"
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


def calculate_confluence_score(df, info):
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
            swept, _, _ = detect_liquidity_sweep(df, lvl, 'low', lookback=5)
            if swept: sweep_bullish = True; break
    for lvl in eq_highs + [levels.get("PDH"), levels.get("PWH")]:
        if lvl:
            swept, _, _ = detect_liquidity_sweep(df, lvl, 'high', lookback=5)
            if swept: sweep_bearish = True; break
    if sweep_bullish:
        score += 2; signals.append(("Sweep", "⚡ Bullish Sweep", "pass")); reasons.append("SSL swept - bullish")
    elif sweep_bearish:
        score += 2; signals.append(("Sweep", "⚡ Bearish Sweep", "pass-short")); reasons.append("BSL swept - bearish")
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


def calculate_entry_sl_tp(df, direction='long'):
    current = df['Close'].iloc[-1]
    recent_low = df['Low'].tail(10).min()
    recent_high = df['High'].tail(10).max()
    if direction == 'long':
        entry = current; sl = recent_low * 0.995
        risk = entry - sl; tp = entry + (risk * 3)
    else:
        entry = current; sl = recent_high * 1.005
        risk = sl - entry; tp = entry - (risk * 3)
    return entry, sl, tp, risk


def fetch_history(symbol, period="6mo", retries=3):
    """Fetch history with retries (yfinance auto-uses curl_cffi if installed)."""
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


def scan_stock(symbol, info):
    try:
        df = fetch_history(symbol, period="6mo")
        if df is None or len(df) < 60:
            return None
        df_weekly = df.resample('W').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
        current_price = df['Close'].iloc[-1]
        change_pct = ((df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100 if len(df) >= 2 else 0
        score, signals, reasons = calculate_confluence_score(df, info)
        w_trend, _ = detect_trend(df_weekly, lookback=3) if len(df_weekly) >= 10 else ("Unknown", 0)
        if w_trend == "Uptrend":
            score += 1; signals.append(("Weekly Trend", "🟢 Weekly Uptrend", "pass"))
        elif w_trend == "Downtrend":
            signals.append(("Weekly Trend", "🔴 Weekly Downtrend", "warn"))

        bullish = sum(1 for s in signals if s[2] == 'pass')
        bearish = sum(1 for s in signals if s[2] == 'pass-short')
        direction = 'long' if bullish >= bearish else 'short'
        entry, sl, tp, risk = calculate_entry_sl_tp(df, direction)
        risk_pct = (risk / entry) * 100
        wk52_high = df['High'].tail(250).max() if len(df) >= 250 else df['High'].max()
        wk52_low = df['Low'].tail(250).min() if len(df) >= 250 else df['Low'].min()

        if score >= 8: verdict, vc = "A+ SETUP", "gold"
        elif score >= 6: verdict, vc = "A SETUP", "green"
        elif score >= 4: verdict, vc = "WATCH", "orange"
        else: verdict, vc = "SKIP", "red"

        return {
            "symbol": info["symbol"], "name": info["name"], "sector": info["sector"],
            "price": round(current_price, 2), "change_pct": round(change_pct, 2),
            "score": score, "verdict": verdict, "verdict_color": vc,
            "direction": direction.upper(),
            "trend_daily": detect_trend(df, 5)[0], "trend_weekly": w_trend,
            "entry": round(entry, 2), "sl": round(sl, 2), "tp": round(tp, 2),
            "risk_pct": round(risk_pct, 2), "rr": 3.0,
            "signals": signals, "reasons": reasons,
            "wk52_high": round(wk52_high, 2), "wk52_low": round(wk52_low, 2),
            "dist_52wh_pct": round(((wk52_high - current_price) / current_price) * 100, 1),
        }
    except Exception:
        return None

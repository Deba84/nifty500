"""
Nifty 500 Swing Trading Scanner Engine
Implements the PDF playbook rules:
- Trend Analysis (structure based - HH/HL, LH/LL)
- Support & Resistance (swing points, PWH/PWL)
- HTF Liquidity (Equal Highs/Lows, Prev Week H/L, sweep detection)
- CHoCH detection (trend reversal)
- Confluence scoring (0-10)
"""
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

from nifty500_list import get_yf_symbols, get_symbol_map


def find_swings(df, lookback=5):
    """Find swing highs and lows using fractal method."""
    highs = df['High'].values
    lows = df['Low'].values
    n = len(df)
    swing_highs = []
    swing_lows = []
    for i in range(lookback, n - lookback):
        # Swing high: highest in window
        if highs[i] == max(highs[i-lookback:i+lookback+1]):
            swing_highs.append((i, highs[i]))
        # Swing low
        if lows[i] == min(lows[i-lookback:i+lookback+1]):
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows


def detect_trend(df, lookback=5):
    """Detect trend from swing structure."""
    swing_highs, swing_lows = find_swings(df, lookback)
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "Unclear", 0
    last_highs = [h[1] for h in swing_highs[-3:]] if len(swing_highs) >= 3 else [h[1] for h in swing_highs[-2:]]
    last_lows = [l[1] for l in swing_lows[-3:]] if len(swing_lows) >= 3 else [l[1] for l in swing_lows[-2:]]

    hh = all(last_highs[i] < last_highs[i+1] for i in range(len(last_highs)-1))
    hl = all(last_lows[i] < last_lows[i+1] for i in range(len(last_lows)-1))
    lh = all(last_highs[i] > last_highs[i+1] for i in range(len(last_highs)-1))
    ll = all(last_lows[i] > last_lows[i+1] for i in range(len(last_lows)-1))

    if hh and hl:
        strength = 100
        return "Uptrend", strength
    elif lh and ll:
        return "Downtrend", 100
    elif hh or hl:
        return "Weak Uptrend", 60
    elif lh or ll:
        return "Weak Downtrend", 60
    else:
        return "Sideways", 30


def find_equal_highs_lows(df, tolerance_pct=0.5, lookback_bars=60):
    """Find equal highs (BSL) and equal lows (SSL) - liquidity pools."""
    recent = df.tail(lookback_bars)
    highs = recent['High'].values
    lows = recent['Low'].values
    equal_highs = []
    equal_lows = []
    # For each high, check if any other high is within tolerance
    for i in range(len(highs)):
        for j in range(i+2, len(highs)):
            if abs(highs[i] - highs[j]) / highs[i] * 100 < tolerance_pct:
                equal_highs.append(max(highs[i], highs[j]))
                break
    for i in range(len(lows)):
        for j in range(i+2, len(lows)):
            if abs(lows[i] - lows[j]) / lows[i] * 100 < tolerance_pct:
                equal_lows.append(min(lows[i], lows[j]))
                break
    return list(set([round(x, 2) for x in equal_highs])), list(set([round(x, 2) for x in equal_lows]))


def detect_liquidity_sweep(df, level, side='low', lookback=5):
    """Check if recent candles swept a liquidity level and rejected."""
    recent = df.tail(lookback)
    for idx, row in recent.iterrows():
        if side == 'low':
            # Wick went below level but close is back above
            if row['Low'] < level and row['Close'] > level:
                wick_size = level - row['Low']
                body = abs(row['Close'] - row['Open'])
                if wick_size > body * 0.5:  # Long wick
                    return True, level, idx
        else:
            if row['High'] > level and row['Close'] < level:
                wick_size = row['High'] - level
                body = abs(row['Close'] - row['Open'])
                if wick_size > body * 0.5:
                    return True, level, idx
    return False, None, None


def detect_choch(df, lookback=5):
    """Detect Change of Character (trend reversal signal)."""
    swing_highs, swing_lows = find_swings(df, lookback)
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return None
    current_price = df['Close'].iloc[-1]
    # Bullish CHoCH: was downtrend, now breaks last swing high
    last_high = swing_highs[-1][1]
    last_low = swing_lows[-1][1]
    prev_high = swing_highs[-2][1] if len(swing_highs) >= 2 else None
    prev_low = swing_lows[-2][1] if len(swing_lows) >= 2 else None

    # Bullish CHoCH: series of LH-LL, now price breaks above last LH
    if prev_high and last_high < prev_high:  # downtrend prior
        if current_price > last_high:
            return "Bullish CHoCH"
    # Bearish CHoCH: series of HH-HL, now price breaks below last HL
    if prev_low and last_low > prev_low:  # uptrend prior
        if current_price < last_low:
            return "Bearish CHoCH"
    return None


def get_key_levels(df):
    """Get PDH, PDL, PWH, PWL, PMH, PML."""
    if len(df) < 25:
        return {}
    # Previous day (last complete day = second-to-last row)
    pdh = df['High'].iloc[-2] if len(df) >= 2 else None
    pdl = df['Low'].iloc[-2] if len(df) >= 2 else None
    # Previous week (last 5-10 trading days back)
    week_ago = df.iloc[-10:-5]
    pwh = week_ago['High'].max() if len(week_ago) > 0 else None
    pwl = week_ago['Low'].min() if len(week_ago) > 0 else None
    # Previous month (last ~20-40 bars back)
    if len(df) >= 40:
        month_ago = df.iloc[-40:-20]
        pmh = month_ago['High'].max()
        pml = month_ago['Low'].min()
    else:
        pmh = pml = None
    return {"PDH": pdh, "PDL": pdl, "PWH": pwh, "PWL": pwl, "PMH": pmh, "PML": pml}


def calculate_confluence_score(df, info):
    """Calculate A+ setup confluence score based on PDF playbook."""
    score = 0
    signals = []
    reasons = []

    if len(df) < 60:
        return 0, [], []

    current_price = df['Close'].iloc[-1]
    volume_avg = df['Volume'].tail(20).mean()
    current_volume = df['Volume'].iloc[-1]

    # 1. Trend Analysis (Daily)
    trend, strength = detect_trend(df, lookback=5)
    if trend == "Uptrend":
        score += 2
        signals.append(("Trend", "🟢 Strong Uptrend (HH-HL)", "pass"))
        reasons.append("Daily trend uptrend")
    elif trend == "Downtrend":
        score += 1  # Can short
        signals.append(("Trend", "🔴 Strong Downtrend", "pass-short"))
        reasons.append("Daily downtrend (short opportunity)")
    elif "Weak" in trend:
        score += 1
        signals.append(("Trend", f"🟡 {trend}", "warn"))
    else:
        signals.append(("Trend", "⚪ Sideways", "neutral"))

    # 2. Price near key liquidity level
    levels = get_key_levels(df)
    near_level = None
    for name, lvl in levels.items():
        if lvl and abs(current_price - lvl) / current_price < 0.02:  # within 2%
            near_level = (name, lvl)
            break
    if near_level:
        score += 2
        signals.append(("Key Level", f"🎯 Near {near_level[0]} @ ₹{near_level[1]:.2f}", "pass"))
        reasons.append(f"Price near {near_level[0]}")
    else:
        signals.append(("Key Level", "⚪ No major level nearby", "neutral"))

    # 3. Equal Highs / Lows (liquidity pools)
    eq_highs, eq_lows = find_equal_highs_lows(df)
    liquidity_note = ""
    if eq_highs:
        # Check if there's untapped BSL above
        untapped = [h for h in eq_highs if h > current_price and (h - current_price)/current_price < 0.10]
        if untapped:
            liquidity_note += f"BSL ₹{min(untapped):.2f} above. "
    if eq_lows:
        untapped = [l for l in eq_lows if l < current_price and (current_price - l)/current_price < 0.10]
        if untapped:
            liquidity_note += f"SSL ₹{max(untapped):.2f} below."
    if liquidity_note:
        score += 1
        signals.append(("Liquidity Pool", f"💧 {liquidity_note}", "pass"))
    else:
        signals.append(("Liquidity Pool", "⚪ No clear pools", "neutral"))

    # 4. Liquidity Sweep detection (recent 5 days)
    sweep_bullish = False
    sweep_bearish = False
    for lvl in eq_lows + [levels.get("PDL"), levels.get("PWL")]:
        if lvl:
            swept, _, _ = detect_liquidity_sweep(df, lvl, 'low', lookback=5)
            if swept:
                sweep_bullish = True
                break
    for lvl in eq_highs + [levels.get("PDH"), levels.get("PWH")]:
        if lvl:
            swept, _, _ = detect_liquidity_sweep(df, lvl, 'high', lookback=5)
            if swept:
                sweep_bearish = True
                break
    if sweep_bullish:
        score += 2
        signals.append(("Sweep", "⚡ Bullish Sweep (SSL taken)", "pass"))
        reasons.append("SSL swept - bullish reversal likely")
    elif sweep_bearish:
        score += 2
        signals.append(("Sweep", "⚡ Bearish Sweep (BSL taken)", "pass-short"))
        reasons.append("BSL swept - bearish reversal likely")
    else:
        signals.append(("Sweep", "⚪ No recent sweep", "neutral"))

    # 5. CHoCH
    choch = detect_choch(df, lookback=5)
    if choch:
        score += 2
        signals.append(("CHoCH", f"🔄 {choch}", "pass"))
        reasons.append(f"{choch} confirmed")
    else:
        signals.append(("CHoCH", "⚪ No CHoCH yet", "neutral"))

    # 6. Volume confirmation
    if current_volume > volume_avg * 1.5:
        score += 1
        signals.append(("Volume", "📊 High volume (1.5x+ avg)", "pass"))
    else:
        signals.append(("Volume", "⚪ Normal volume", "neutral"))

    # 7. Above/below 50-day average (basic trend filter)
    ma50 = df['Close'].tail(50).mean()
    if current_price > ma50:
        signals.append(("50D Avg", f"🟢 Above 50D avg (₹{ma50:.2f})", "pass"))
    else:
        signals.append(("50D Avg", f"🔴 Below 50D avg (₹{ma50:.2f})", "warn"))

    return score, signals, reasons


def calculate_entry_sl_tp(df, direction='long'):
    """Suggest Entry, SL, TP based on recent structure."""
    current = df['Close'].iloc[-1]
    recent_low = df['Low'].tail(10).min()
    recent_high = df['High'].tail(10).max()
    if direction == 'long':
        entry = current
        sl = recent_low * 0.995  # 0.5% below recent low
        risk = entry - sl
        tp = entry + (risk * 3)  # 1:3 R:R
        return entry, sl, tp, risk
    else:
        entry = current
        sl = recent_high * 1.005
        risk = sl - entry
        tp = entry - (risk * 3)
        return entry, sl, tp, risk


def scan_stock(symbol, info):
    """Complete analysis of one stock."""
    try:
        t = yf.Ticker(symbol)
        df = t.history(period="6mo", interval="1d")
        if len(df) < 60:
            return None
        # Weekly data for HTF
        df_weekly = df.resample('W').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()

        current_price = df['Close'].iloc[-1]
        change_pct = ((df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100 if len(df) >= 2 else 0

        # Daily analysis
        score, signals, reasons = calculate_confluence_score(df, info)

        # Weekly trend confirmation
        w_trend, _ = detect_trend(df_weekly, lookback=3) if len(df_weekly) >= 10 else ("Unknown", 0)
        if w_trend == "Uptrend":
            score += 1
            signals.append(("Weekly Trend", "🟢 Weekly Uptrend", "pass"))
        elif w_trend == "Downtrend":
            signals.append(("Weekly Trend", "🔴 Weekly Downtrend", "warn"))
        else:
            signals.append(("Weekly Trend", f"⚪ Weekly {w_trend}", "neutral"))

        # Determine direction based on signals
        bullish_signals = sum(1 for s in signals if 'pass' == s[2])
        bearish_signals = sum(1 for s in signals if 'pass-short' == s[2])
        direction = 'long' if bullish_signals >= bearish_signals else 'short'

        entry, sl, tp, risk = calculate_entry_sl_tp(df, direction)
        rr = 3.0
        risk_pct = (risk / entry) * 100

        # 52-week high/low
        wk52_high = df['High'].tail(250).max() if len(df) >= 250 else df['High'].max()
        wk52_low = df['Low'].tail(250).min() if len(df) >= 250 else df['Low'].min()
        distance_from_52wh = ((wk52_high - current_price) / current_price) * 100
        distance_from_52wl = ((current_price - wk52_low) / wk52_low) * 100

        # Verdict
        if score >= 8:
            verdict = "A+ SETUP"
            verdict_color = "gold"
        elif score >= 6:
            verdict = "A SETUP"
            verdict_color = "green"
        elif score >= 4:
            verdict = "WATCH"
            verdict_color = "orange"
        else:
            verdict = "SKIP"
            verdict_color = "red"

        return {
            "symbol": info["symbol"],
            "name": info["name"],
            "sector": info["sector"],
            "price": round(current_price, 2),
            "change_pct": round(change_pct, 2),
            "score": score,
            "verdict": verdict,
            "verdict_color": verdict_color,
            "direction": direction.upper(),
            "trend_daily": detect_trend(df, 5)[0],
            "trend_weekly": w_trend,
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "risk_pct": round(risk_pct, 2),
            "rr": rr,
            "signals": signals,
            "reasons": reasons,
            "wk52_high": round(wk52_high, 2),
            "wk52_low": round(wk52_low, 2),
            "dist_52wh_pct": round(distance_from_52wh, 1),
            "dist_52wl_pct": round(distance_from_52wl, 1),
        }
    except Exception as e:
        return None


def scan_batch(symbols_info_list, progress_callback=None):
    """Scan a batch of stocks (used by web app)."""
    results = []
    total = len(symbols_info_list)
    for i, (sym, info) in enumerate(symbols_info_list):
        r = scan_stock(sym, info)
        if r:
            results.append(r)
        if progress_callback:
            progress_callback(i+1, total, info["symbol"])
    return sorted(results, key=lambda x: x["score"], reverse=True)


if __name__ == "__main__":
    # Test
    sym_map = get_symbol_map()
    symbols = list(sym_map.items())[:5]
    print("Testing scanner on 5 stocks...")
    for sym, info in symbols:
        r = scan_stock(sym, info)
        if r:
            print(f"{r['symbol']}: Score={r['score']}/10, Verdict={r['verdict']}, Direction={r['direction']}")

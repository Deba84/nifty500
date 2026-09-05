"""Small walk-forward evaluator for scanner research.

Usage:
    python backtest.py --symbol RELIANCE.NS --period 5y

This is a research tool, not a live trading component.  It only calls the
deterministic scanner on data available up to each evaluation date and reports
outcomes net of a configurable slippage estimate.
"""
from __future__ import annotations

import argparse
from collections import Counter
from typing import Any, Dict, List

import pandas as pd

from scanner_engine import MIN_HISTORY_BARS, analyze_stock_from_df, batch_download_all


def evaluate_future(
    result: Dict[str, Any],
    future: pd.DataFrame,
    slippage_bps: float = 10.0,
) -> Dict[str, Any]:
    direction = result["direction"]
    entry = float(result["entry_ref"]) * (1 + slippage_bps / 10000 if direction == "LONG" else 1 - slippage_bps / 10000)
    sl = float(result["sl"])
    tp1 = float(result["tp1"])
    for idx, row in future.iterrows():
        high, low = float(row["High"]), float(row["Low"])
        if direction == "LONG":
            hit_sl, hit_tp = low <= sl, high >= tp1
        else:
            hit_sl, hit_tp = high >= sl, low <= tp1
        if hit_sl and hit_tp:
            return {"result": "AMBIGUOUS_SAME_BAR", "date": str(pd.Timestamp(idx).date())}
        if hit_tp:
            return {"result": "TP1", "date": str(pd.Timestamp(idx).date())}
        if hit_sl:
            return {"result": "SL", "date": str(pd.Timestamp(idx).date())}
    return {"result": "NO_EXIT_IN_WINDOW"}


def walk_forward(df: pd.DataFrame, info: Dict[str, Any], step: int = 1, max_bars: int = 1500) -> List[Dict[str, Any]]:
    df = df.sort_index().tail(max_bars).copy()
    start = max(MIN_HISTORY_BARS, 200)
    events = []
    for end in range(start, len(df) - 20, step):
        sample = df.iloc[: end + 1]
        signal_date = pd.Timestamp(sample.index[-1])
        result = analyze_stock_from_df(sample, info, market_session_date=signal_date)
        if not result or result.get("final_status") == "SKIP":
            continue
        outcome = evaluate_future(result, df.iloc[end + 1 : end + 21])
        events.append(
            {
                "date": str(signal_date.date()),
                "symbol": info.get("symbol"),
                "state": result.get("signal_state"),
                "score": result.get("setup_score"),
                **outcome,
            }
        )
    return events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", action="append", required=True, help="Yahoo symbol, repeatable")
    parser.add_argument("--period", default="5y")
    parser.add_argument("--step", type=int, default=5)
    args = parser.parse_args()
    frames = batch_download_all(args.symbol, period=args.period, chunk_size=20)
    all_events = []
    for symbol, df in frames.items():
        info = {"symbol": symbol.removesuffix(".NS"), "name": symbol, "sector": "Unknown"}
        all_events.extend(walk_forward(df, info, step=max(1, args.step)))
    print({"signals": len(all_events), "results": dict(Counter(x["result"] for x in all_events))})
    for event in all_events:
        print(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
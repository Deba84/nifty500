"""Walk-forward signal outcome ledger.

The ledger is intentionally simple JSON so it works locally and in GitHub
Actions without a database.  It evaluates completed daily candles and marks
same-bar SL/TP collisions as ambiguous instead of choosing a convenient
outcome.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import pandas as pd


HORIZONS = (5, 10, 20)
MAX_LEDGER_ROWS = 5000


def _load(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "signals": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("signals"), list):
            return payload
    except (OSError, ValueError):
        pass
    return {"version": 1, "signals": []}


def _bar_outcome(direction: str, row: pd.Series, sl: float, tp1: float) -> str:
    high, low = float(row["High"]), float(row["Low"])
    if direction == "LONG":
        hit_sl, hit_tp = low <= sl, high >= tp1
    else:
        hit_sl, hit_tp = high >= sl, low <= tp1
    if hit_sl and hit_tp:
        return "AMBIGUOUS_SAME_BAR"
    if hit_tp:
        return "TP1"
    if hit_sl:
        return "SL"
    return ""


def _evaluate_signal(record: Dict[str, Any], df: pd.DataFrame) -> None:
    signal_date = pd.Timestamp(record["signal_date"]).normalize()
    future = df.loc[df.index > signal_date].copy()
    if future.empty:
        return
    direction = record["direction"]
    entry = float(record["entry_ref"])
    sl = float(record["sl"])
    tp1 = float(record["tp1"])
    for horizon in HORIZONS:
        key = f"h{horizon}"
        if record.get("outcomes", {}).get(key):
            continue
        window = future.head(horizon)
        if window.empty:
            continue
        event = ""
        event_date = None
        for idx, row in window.iterrows():
            event = _bar_outcome(direction, row, sl, tp1)
            if event:
                event_date = str(pd.Timestamp(idx).date())
                break
        if event:
            record.setdefault("outcomes", {})[key] = {
                "result": event,
                "event_date": event_date,
                "bars_observed": int(len(window)),
            }
        elif len(window) >= horizon:
            last = float(window["Close"].iloc[-1])
            r = (last - entry) / (entry - sl) if direction == "LONG" else (entry - last) / (sl - entry)
            record.setdefault("outcomes", {})[key] = {
                "result": "OPEN_AT_HORIZON",
                "close": round(last, 4),
                "r_multiple": round(r, 4),
                "bars_observed": int(len(window)),
            }


def update_ledger(
    path: Path,
    results: Sequence[Mapping[str, Any]],
    stock_dfs: Mapping[str, pd.DataFrame],
    signal_date: date,
) -> Dict[str, Any]:
    payload = _load(path)
    records: List[Dict[str, Any]] = payload["signals"]
    known = {str(x.get("signal_id")) for x in records}
    created = 0

    for item in results:
        if item.get("final_status") == "SKIP":
            continue
        symbol = str(item.get("symbol"))
        sid = "|".join(
            [
                symbol,
                str(item.get("data_as_of", signal_date)),
                str(item.get("signal_state", "")),
                str(item.get("entry_ref", "")),
            ]
        )
        if sid in known:
            continue
        records.append(
            {
                "signal_id": sid,
                "symbol": symbol,
                "sector": item.get("sector", "Unknown"),
                "signal_date": str(item.get("data_as_of", signal_date)),
                "direction": item.get("direction"),
                "signal_state": item.get("signal_state"),
                "status": item.get("final_status"),
                "score": item.get("final_score"),
                "entry_ref": item.get("entry_ref"),
                "sl": item.get("sl"),
                "tp1": item.get("tp1"),
                "tp2": item.get("tp2"),
                "outcomes": {},
            }
        )
        known.add(sid)
        created += 1

    for record in records:
        df = stock_dfs.get(f"{record.get('symbol')}.NS")
        if df is not None:
            _evaluate_signal(record, df)

    records = records[-MAX_LEDGER_ROWS:]
    payload = {
        "version": 1,
        "updated_at": str(signal_date),
        "signals": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {"ledger_path": str(path), "signals_total": len(records), "signals_created": created}
    for horizon in HORIZONS:
        counts = {}
        for record in records:
            result = (record.get("outcomes", {}).get(f"h{horizon}") or {}).get("result")
            if result:
                counts[result] = counts.get(result, 0) + 1
        summary[f"h{horizon}"] = counts
    return summary
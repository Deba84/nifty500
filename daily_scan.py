"""
Daily Nifty 500 scan v6.2.1
Deterministic SMC + price action + closed-world comparative AI review.
"""
from __future__ import annotations

import html
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from nifty500_list import get_symbol_map
from scanner_engine import (
    STATE_PRIORITY,
    analyze_stock_from_df,
    batch_download_all,
    infer_market_session_date,
    status_from_score,
)

IST = ZoneInfo("Asia/Kolkata")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

MAX_FUND_CANDIDATES = int(os.getenv("MAX_FUND_CANDIDATES", "40"))
MAX_POST_FUND_CANDIDATES = int(os.getenv("MAX_POST_FUND_CANDIDATES", "12"))
MAX_AI_ANALYSIS = int(os.getenv("MAX_AI_ANALYSIS", "8"))
MAX_POST_AI_ANALYSIS = int(os.getenv("MAX_POST_AI_ANALYSIS", "3"))
OVERALL_STAGE_TIMEOUT = int(os.getenv("OVERALL_STAGE_TIMEOUT", "300"))
MAX_ALERTS_PER_CATEGORY = int(os.getenv("MAX_ALERTS_PER_CATEGORY", "5"))
MAX_ARMED_ALERTS = int(os.getenv("MAX_ARMED_ALERTS", "8"))
MAX_POST_ALERTS = int(os.getenv("MAX_POST_ALERTS", "3"))
ARTIFACT_DIR = Path(os.getenv("SCAN_ARTIFACT_DIR", "artifacts"))
SCANNER_VERSION = "v6.2.1"

FUND_POINTS = {2: 7, 3: 11, 4: 15}
PRIME_BLOCKING_FLAGS = {
    "COUNTER_WEEKLY_STRUCTURE",
    "FAST_APPROACH",
    "FUNDAMENTAL_BORDERLINE",
    "REPEATED_NEAR_TESTS",
    "BORDERLINE_RR",
    "STRUCTURE_MIXED",
}


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def _split_telegram(text: str, max_len: int = 4000) -> List[str]:
    """Split on lines so normal HTML tags are not cut in the middle."""
    if len(text) <= max_len:
        return [text]
    chunks: List[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > max_len and current:
            chunks.append(current.rstrip())
            current = ""
        if len(line) > max_len:
            # This should only happen for an unexpected unbroken AI string.
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.extend(line[i : i + max_len] for i in range(0, len(line), max_len))
        else:
            current += line
    if current:
        chunks.append(current.rstrip())
    return chunks


def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram credentials absent; message not sent")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    success = True
    for chunk in _split_telegram(text):
        try:
            response = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
                timeout=(5, 25),
            )
            if response.status_code == 429:
                retry_after = min(10, int(response.json().get("parameters", {}).get("retry_after", 2)))
                time.sleep(retry_after)
                response = requests.post(
                    url,
                    data={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": chunk,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    },
                    timeout=(5, 25),
                )
            if not response.ok:
                success = False
                print(f"⚠️ Telegram {response.status_code}: {response.text[:300]}")
        except Exception as exc:
            success = False
            print(f"⚠️ Telegram exception: {exc}")
        time.sleep(0.15)
    return success


def _fundamental_points(score: int) -> int:
    return FUND_POINTS.get(min(4, max(0, int(score))), 0)


def apply_rule_caps(item: Dict[str, Any]) -> Dict[str, Any]:
    """Apply deterministic quality caps before the AI downgrade layer.

    PRIME_WATCH is reserved for clean setups.  Post-sweep candidates are
    also capped by their actual confirmation quality so a weak reclaim can
    never appear as a prime setup merely because the original pool was good.
    """
    raw_score = int(item.get("setup_score", 0))
    cap = 100
    reasons: List[str] = []
    flags = set(item.get("quality_flags", []) or [])
    blockers = sorted(flags.intersection(PRIME_BLOCKING_FLAGS))
    if blockers:
        cap = min(cap, 84)
        reasons.extend(blockers)

    state = item.get("signal_state", "PRE_SWEEP")
    confirmation_score = int((item.get("confirmation") or {}).get("confirmation_score", 0) or 0)
    if state in {"SWEPT_WAIT_RECLAIM", "RECLAIMED_WAIT_CHOCH"}:
        if confirmation_score < 50:
            cap = min(cap, 74)
            reasons.append("LOW_CONFIRMATION")
        elif confirmation_score < 70:
            cap = min(cap, 84)
            reasons.append("PARTIAL_CONFIRMATION")
    elif state == "CHOCH_WAIT_DISPLACEMENT":
        cap = min(cap, 84)
        reasons.append("DISPLACEMENT_PENDING")

    rule_score = min(raw_score, cap)
    item["raw_setup_score"] = raw_score
    item["rule_cap"] = cap if cap < 100 else None
    item["rule_penalty"] = raw_score - rule_score
    item["rule_reasons"] = list(dict.fromkeys(reasons))
    item["rule_score"] = rule_score
    item["base_status"] = status_from_score(rule_score)
    item["final_score"] = rule_score
    item["final_status"] = item["base_status"]
    item["priority"] = rule_score
    return item


def add_fundamental_safe(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        from fundamental_analyzer import get_fundamental_score

        result = get_fundamental_score(item["symbol"])
        score = int(result.get("score", 0))
        if score < 2:
            return None
        item["fund_score"] = score
        item["fund_points"] = _fundamental_points(score)
        item["fund_data"] = result.get("data") or {}
        item["fund_signals"] = result.get("signals", [])
        item["fund_reasons"] = result.get("reasons", [])
        item["score_breakdown"]["fundamental"] = item["fund_points"]
        item["setup_score"] = min(100, int(item["technical_score"]) + item["fund_points"])
        if score == 2:
            item.setdefault("quality_flags", []).append("FUNDAMENTAL_BORDERLINE")
            item["quality_flags"] = sorted(set(item["quality_flags"]))
        return apply_rule_caps(item)
    except Exception as exc:
        if os.getenv("SCANNER_DEBUG", "false").lower() == "true":
            print(f"⚠️ Fundamental {item.get('symbol')}: {exc}")
        return None


def candidate_sort_key(item: Dict[str, Any], use_final: bool = False) -> Tuple[int, float, float, float]:
    if use_final:
        score = float(item.get("final_score", 0))
    else:
        score = float(item.get("rule_score", item.get("setup_score", item.get("technical_score", 0))))
    confirmation_score = float((item.get("confirmation") or {}).get("confirmation_score", 0))
    return (
        int(STATE_PRIORITY.get(item.get("signal_state", "PRE_SWEEP"), 0)),
        score,
        confirmation_score,
        -float(item.get("distance_pct", 999)),
    )


def _balanced_select(
    candidates: Sequence[Dict[str, Any]],
    total_limit: int,
    post_limit: int,
    use_final: bool = False,
) -> List[Dict[str, Any]]:
    """Reserve most slots for the core pre-sweep mission.

    Post-sweep monitoring is valuable, but it must not crowd pre-sweep
    candidates out of the limited Screener/Groq budgets.
    """
    ordered_pre = sorted(
        (x for x in candidates if x.get("signal_state") == "PRE_SWEEP"),
        key=lambda x: candidate_sort_key(x, use_final=use_final),
        reverse=True,
    )
    ordered_post = sorted(
        (x for x in candidates if x.get("signal_state") != "PRE_SWEEP"),
        key=lambda x: candidate_sort_key(x, use_final=use_final),
        reverse=True,
    )
    take_post = min(max(0, post_limit), len(ordered_post), total_limit)
    take_pre = min(len(ordered_pre), total_limit - take_post)
    selected = ordered_post[:take_post] + ordered_pre[:take_pre]

    # If either category is short, fill unused capacity from the other one.
    if len(selected) < total_limit:
        used = {id(x) for x in selected}
        leftovers = [x for x in list(ordered_post) + list(ordered_pre) if id(x) not in used]
        leftovers.sort(key=lambda x: candidate_sort_key(x, use_final=use_final), reverse=True)
        selected.extend(leftovers[: total_limit - len(selected)])
    return sorted(selected, key=lambda x: candidate_sort_key(x, use_final=use_final), reverse=True)


def calculate_breadth(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    visible = [x for x in results if x.get("final_status") != "SKIP"]
    total = len(visible)
    longs = sum(1 for x in visible if x.get("direction") == "LONG")
    shorts = total - longs
    ratio = longs / total if total else 0.5
    flow = "LONG_HEAVY" if ratio >= 0.65 else "SHORT_HEAVY" if ratio <= 0.35 else "BALANCED"
    sectors = Counter(x.get("sector", "Unknown") for x in visible)
    dominant = sectors.most_common(1)[0] if sectors else ("N/A", 0)
    weekly_aligned = sum(
        1
        for x in visible
        if int(((x.get("score_details") or {}).get("htf_context") or {}).get("weekly", 0)) >= 6
    )
    median_score = float(np.median([x.get("final_score", 0) for x in visible])) if visible else 0.0
    pre_sweep = sum(1 for x in visible if x.get("signal_state") == "PRE_SWEEP")
    trigger_ready = sum(1 for x in visible if x.get("signal_state") == "TRIGGER_READY")
    post_pending = total - pre_sweep - trigger_ready
    return {
        "scope": "scanner candidates; not whole-market direction",
        "setup_flow": flow,
        "long_count": longs,
        "short_count": shorts,
        "total_passed": total,
        "pre_sweep_count": pre_sweep,
        "post_sweep_pending_count": post_pending,
        "trigger_ready_count": trigger_ready,
        "dominant_candidate_sector": dominant[0],
        "dominant_sector_count": dominant[1],
        "weekly_aligned_count": weekly_aligned,
        "median_setup_score": round(median_score, 1),
        "data_as_of": visible[0].get("data_as_of") if visible else None,
    }


def apply_ai_layer(
    results: List[Dict[str, Any]],
    breadth: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from ai_analyzer import apply_review, review_setups

    eligible = [x for x in results if x.get("base_status") != "SKIP"]
    top = _balanced_select(
        eligible,
        total_limit=MAX_AI_ANALYSIS,
        post_limit=MAX_POST_AI_ANALYSIS,
        use_final=False,
    )
    response = review_setups(top, breadth)
    reviews = response.get("reviews", {})
    reviewed_symbols = set()
    for item in results:
        symbol = item["symbol"]
        if symbol in reviews:
            apply_review(item, reviews[symbol])
            reviewed_symbols.add(symbol)
        else:
            item["ai_review"] = {}
            item["ai_penalty"] = 0
            item["final_score"] = item.get("rule_score", item["setup_score"])
            item["final_status"] = status_from_score(item["final_score"])
            item["priority"] = item["final_score"]
    response.setdefault("meta", {})["reviewed_symbols"] = sorted(reviewed_symbols)
    return results, response


def _risk_label(item: Dict[str, Any]) -> str:
    if item.get("final_status") == "PRIME_WATCH":
        return "FULL only after confirmation — max account risk 1.0%"
    if item.get("final_status") == "WATCH":
        return "REDUCED only after confirmation — max account risk 0.5%"
    return "NO ENTRY — reassess after confirmation"


def _entry_line(item: Dict[str, Any]) -> str:
    state = item.get("signal_state")
    status = item.get("final_status")
    if state == "TRIGGER_READY" and status in {"PRIME_WATCH", "WATCH"}:
        return "✅ <b>TRIGGER DETECTED</b> — TradingView-এ CHoCH/retest verify করুন"
    if state == "TRIGGER_READY":
        return "⏳ CHoCH detected, কিন্তু overall quality WAIT — no entry"
    if state == "SWEPT_WAIT_RECLAIM":
        return "🧹 Liquidity swept — reclaim এখনও হয়নি"
    if state == "RECLAIMED_WAIT_CHOCH":
        return "🔁 Level reclaimed — CHoCH-এর জন্য অপেক্ষা"
    if state == "CHOCH_WAIT_DISPLACEMENT":
        return "🧭 CHoCH আছে, displacement দুর্বল — অপেক্ষা"
    if state == "PRE_SWEEP" and item.get("stage") in {"AT LEVEL", "VERY CLOSE"}:
        return "🚨 <b>PRE-SWEEP ARMED</b> — next-session sweep watch; entry নয়"
    if state == "PRE_SWEEP":
        return "👀 <b>EARLY WATCH</b> — liquidity approach চলছে; entry নয়"
    return "⛔ <b>NOT TRIGGERED</b> — sweep + rejection + CHoCH প্রয়োজন"


def format_alert(item: Dict[str, Any], rank: int) -> str:
    direction_emoji = "🟢" if item["direction"] == "LONG" else "🔴"
    change_emoji = "▲" if item["change_pct"] >= 0 else "▼"
    tv_link = f"https://in.tradingview.com/chart/?symbol=NSE:{item['symbol']}"
    screener_link = f"https://www.screener.in/company/{item['symbol']}/"
    liq = item.get("liquidity", {})
    pa = item.get("price_action", {})
    conf = item.get("confirmation", {})
    scores = item.get("score_breakdown", {})
    ai = item.get("ai_review", {})

    text = (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"{item['stage_emoji']} <b>#{rank}. {_esc(item['symbol'])}</b> {direction_emoji} "
        f"<b>{_esc(item['direction'])}</b>\n"
        f"<i>{_esc(item['name'])} • {_esc(item['sector'])}</i>\n\n"
        f"🧠 <b>Setup Quality: {item['final_score']}/100 — {_esc(item['final_status'])}</b>\n"
    )
    if item.get("rule_penalty", 0):
        reasons = ", ".join(item.get("rule_reasons", [])[:3])
        text += (
            f"🛡 Rule cap: {item.get('raw_setup_score', item.get('setup_score', 0))}"
            f"→{item.get('rule_score', item['final_score'])} ({_esc(reasons)})\n"
        )
    if ai:
        action = ai.get("action", "KEEP")
        ai_icon = "⚠️" if action == "CAUTION" else "✅"
        penalty = f" (-{item.get('ai_penalty', 0)})" if item.get("ai_penalty") else ""
        text += f"🤖 AI Review: {ai_icon} <b>{_esc(action)}</b>{penalty}\n"
    text += f"🚦 {_entry_line(item)}\n"

    if item.get("signal_state") != "PRE_SWEEP":
        text += (
            f"\n🎬 <b>Price Action State: {_esc(item['signal_state'])}</b>\n"
            f"🧹 Sweep: {_esc(conf.get('sweep_date', 'N/A'))} | Penetration: "
            f"{_esc(conf.get('sweep_penetration_pct', 'N/A'))}%\n"
            f"↩️ Reclaimed: {'Yes' if conf.get('reclaimed') else 'No'} | "
            f"Displacement: {'Yes' if conf.get('displacement') else 'No'} | "
            f"CHoCH: {'Yes' if conf.get('choch_confirmed') else 'No'}\n"
            f"🎬 Confirmation Quality: <b>{conf.get('confirmation_score', 0)}/100</b>\n"
        )

    text += (
        f"\n💰 <b>Current:</b> ₹{item['price']} ({change_emoji} {abs(item['change_pct'])}%)\n"
        f"🎯 <b>{_esc(item['liq_type'])}:</b> ₹{item['target_level']} "
        f"({_esc(item['target_level_type'])})\n"
        f"📏 Distance: {item['distance_pct']}% | {_esc(item['stage'])}\n\n"
        "🌊 <b>LIQUIDITY EVIDENCE</b>\n"
        f"• Formed: {_esc(liq.get('formation_date', 'N/A'))} | Age: {liq.get('age_bars', 0)} bars\n"
        f"• Touches: {liq.get('touch_count', 1)} | Cluster: {liq.get('cluster_width_pct', 0)}%\n"
        f"• Prior near-tests: {liq.get('prior_near_test_episodes', 0)} | "
        f"Confluence: {liq.get('confluence_count', 0)}\n"
    )
    if item.get("signal_state") == "PRE_SWEEP":
        text += (
            "• ✅ Formation-এর পর full-history check-এ এখনও unswept\n"
            f"• 3D gap closed: {pa.get('gap_closed_3d', 0)}pp | "
            f"Toward closes: {pa.get('directional_closes_3', 0)}/3\n"
        )

    text += (
        "\n📊 <b>SCORE BREAKDOWN</b>\n"
        f"Liquidity {scores.get('liquidity', 0)}/30 | Approach {scores.get('approach', 0)}/20\n"
        f"D/W Context {scores.get('htf_context', 0)}/15 | Trade {scores.get('trade_geometry', 0)}/15\n"
        f"Fundamental {scores.get('fundamental', 0)}/15 | Data {scores.get('data_integrity', 0)}/5\n"
        f"📈 Structure: D {_esc(item['trend_daily'])} | W {_esc(item['trend_weekly'])}\n"
    )

    state = item.get("signal_state")
    if state not in {"PRE_SWEEP", "TRIGGER_READY"}:
        text += (
            "\n🔒 <b>TRADE PLAN LOCKED</b>\n"
            "CHoCH confirm না হওয়া পর্যন্ত entry/SL actionable নয়।\n"
            f"Reference opposing pools: ₹{item['tp1']} ({_esc(item['tp1_type'])}) → "
            f"₹{item['tp2']} ({_esc(item['tp2_type'])})\n"
        )
    else:
        plan_title = "CONFIRMED STRUCTURE PLAN" if item.get("plan_type") == "CONFIRMED_STRUCTURE" else "REFERENCE PLAN — sweep-এর পরে recheck"
        text += (
            f"\n🎯 <b>{plan_title}</b>\n"
            f"Entry/Retest Zone: <b>₹{item['entry_zone'][0]} – ₹{item['entry_zone'][1]}</b>\n"
            f"SL: <b>₹{item['sl']}</b> | Risk distance: {item['risk_pct']}%\n"
            f"TP1: <b>₹{item['tp1']}</b> ({_esc(item['tp1_type'])}, R:R {item['rr1']}:1)\n"
            f"TP2: <b>₹{item['tp2']}</b> ({_esc(item['tp2_type'])}, R:R {item['rr2']}:1)\n"
            f"💼 {_esc(_risk_label(item))}\n"
        )

    fund = item.get("fund_data") or {}
    fund_parts = []
    for key, label, suffix in (
        ("ROE", "ROE", "%"), ("ROCE", "ROCE", "%"),
        ("Stock P/E", "P/E", ""), ("Promoter Holding", "Prom", "%"),
    ):
        if fund.get(key) is not None:
            fund_parts.append(f"{label} {fund[key]}{suffix}")
    text += f"\n💼 <b>Fund [{item.get('fund_score', 0)}/4]:</b> {_esc(' | '.join(fund_parts) or 'limited data')}\n"

    if ai.get("commentary"):
        text += f"\n🤖 <i>{_esc(ai['commentary'])}</i>\n"
    if ai.get("risks"):
        text += "⚠️ " + " • ".join(_esc(x) for x in ai["risks"][:2]) + "\n"
    flags = [x for x in item.get("quality_flags", []) if x not in {"SYNTHETIC_TP2"}]
    if flags:
        text += f"🧭 Flags: {_esc(', '.join(flags[:4]))}\n"

    text += f"\n🔗 <a href='{tv_link}'>Chart</a> | <a href='{screener_link}'>Fundamentals</a>\n"
    return text


def _save_artifact(
    started: datetime,
    stage_times: Dict[str, float],
    stock_count: int,
    raw_candidate_count: int,
    results: Sequence[Dict[str, Any]],
    breadth: Dict[str, Any],
    ai_response: Dict[str, Any],
) -> Path:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_DIR / f"scan_{started.astimezone(IST).strftime('%Y-%m-%d_%H%M%S')}.json"
    payload = {
        "scanner_version": SCANNER_VERSION,
        "started_at": started.isoformat(),
        "completed_at": datetime.now(IST).isoformat(),
        "stage_seconds": stage_times,
        "downloaded_stocks": stock_count,
        "technical_candidates": raw_candidate_count,
        "breadth": breadth,
        "ai_meta": ai_response.get("meta", {}),
        "ai_breadth_commentary_bn": ai_response.get("breadth_commentary_bn", ""),
        "results": list(results),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    return path


def _send_results(
    results: List[Dict[str, Any]],
    breadth: Dict[str, Any],
    ai_response: Dict[str, Any],
    downloaded: int,
    total_symbols: int,
    total_seconds: float,
) -> None:
    visible = [x for x in results if x.get("final_status") != "SKIP"]
    if not visible:
        send_telegram(
            f"⚠️ <b>NO VALID {SCANNER_VERSION} SETUPS TODAY</b>\n\n"
            f"Scanned {downloaded}/{total_symbols} stocks.\n"
            f"Hard rules: genuine untapped + moving approach + Fund ≥2 + TP1 R:R ≥2.\n"
            f"⏱️ Duration: {total_seconds:.0f}s"
        )
        return

    trigger = [x for x in visible if x["signal_state"] == "TRIGGER_READY"]
    confirming = [x for x in visible if x["signal_state"] not in {"PRE_SWEEP", "TRIGGER_READY"}]
    pre = [x for x in visible if x["signal_state"] == "PRE_SWEEP"]
    at_level = [x for x in pre if x["stage"] == "AT LEVEL"]
    very_close = [x for x in pre if x["stage"] == "VERY CLOSE"]
    close = [x for x in pre if x["stage"] == "CLOSE"]
    armed = at_level + very_close
    longs = sum(1 for x in visible if x["direction"] == "LONG")
    shorts = len(visible) - longs
    ai_meta = ai_response.get("meta", {})
    ai_badge = "🤖" if ai_meta.get("available") else ""
    now = datetime.now(IST).strftime("%d %b %Y, %I:%M %p")

    header = (
        f"🌊 <b>NIFTY 500 LIQUIDITY + PRICE ACTION {SCANNER_VERSION} {ai_badge}</b>\n"
        f"📅 {now} IST\n⚡ Scan time: {total_seconds:.0f}s\n\n"
        "📊 <b>Summary</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ TRIGGER READY: <b>{len(trigger)}</b>\n"
        f"🔁 POST-SWEEP CONFIRMING: <b>{len(confirming)}</b>\n"
        f"🎯 AT LEVEL: <b>{len(at_level)}</b>\n"
        f"🔥 VERY CLOSE: <b>{len(very_close)}</b>\n"
        f"⚡ CLOSE: <b>{len(close)}</b>\n"
        f"🟢 LONG: <b>{longs}</b> | 🔴 SHORT: <b>{shorts}</b>\n"
        f"📈 Scanned: {downloaded}/{total_symbols}\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    send_telegram(header)

    breadth_text = (
        "🌐 <b>SCANNER SETUP FLOW</b>\n"
        f"Flow: <b>{_esc(breadth.get('setup_flow', 'N/A'))}</b> "
        f"({breadth.get('long_count', 0)} LONG / {breadth.get('short_count', 0)} SHORT)\n"
        f"Mix: {breadth.get('pre_sweep_count', 0)} pre-sweep | "
        f"{breadth.get('post_sweep_pending_count', 0)} post-sweep pending | "
        f"{breadth.get('trigger_ready_count', 0)} trigger\n"
        f"Candidate concentration: {_esc(breadth.get('dominant_candidate_sector', 'N/A'))} "
        f"({breadth.get('dominant_sector_count', 0)})\n"
        f"Weekly aligned: {breadth.get('weekly_aligned_count', 0)}/{breadth.get('total_passed', 0)}\n"
        "⚠️ <i>এটি deterministic scanner breadth; পুরো Nifty market direction নয়।</i>"
    )
    send_telegram(breadth_text)

    # User priority: see actionable pre-sweep opportunities before old post-sweep monitoring.
    categories = [
        ("✅ <b>TRIGGER READY — CHART VERIFY</b>", trigger, MAX_ALERTS_PER_CATEGORY),
        ("🚨 <b>PRE-SWEEP ARMED ≤0.8% — NEXT-SESSION PRIORITY</b>", armed, MAX_ARMED_ALERTS),
        ("⚡ <b>PRE-SWEEP EARLY WATCH 0.8–1.5%</b>", close, MAX_ALERTS_PER_CATEGORY),
        ("🔁 <b>POST-SWEEP — CONFIRMATION PENDING</b>", confirming, MAX_POST_ALERTS),
    ]
    for title, items, limit in categories:
        if not items:
            continue
        ordered = sorted(items, key=lambda x: candidate_sort_key(x, use_final=True), reverse=True)
        send_telegram(f"\n{title} ({len(items)})")
        for rank, item in enumerate(ordered[:limit], 1):
            send_telegram(format_alert(item, rank))

    model_line = ai_meta.get("model", "deterministic fallback")
    footer = (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>Execution Rules</b>\n"
        "1. PRE-SWEEP alert-এ entry নয়\n"
        "2. Sweep + reclaim/rejection প্রয়োজন\n"
        "3. Daily CHoCH ও R:R TradingView-এ verify\n"
        "4. Account risk সর্বোচ্চ 1%\n"
        "5. Setup Quality win probability নয়\n\n"
        f"🧠 Scanner {SCANNER_VERSION} | AI: {_esc(model_line)}\n"
        f"⏱️ Completed: {total_seconds:.0f}s"
    )
    send_telegram(footer)


def main() -> int:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return 1

    started = datetime.now(IST)
    stage_times: Dict[str, float] = {}
    print(f"🚀 Nifty 500 Scanner {SCANNER_VERSION} starting at {started.isoformat()}")

    symbol_map = get_symbol_map()
    symbols = list(symbol_map.keys())
    print(f"📊 Universe: {len(symbols)} stocks")

    # Stage 1: batch data.
    stage = time.time()
    stock_dfs = batch_download_all(symbols, period="1y", chunk_size=100)
    stage_times["batch_download"] = round(time.time() - stage, 2)
    if not stock_dfs:
        send_telegram("⚠️ <b>SCAN FAILED</b>\nBatch data download returned no stocks.")
        return 2
    market_session = infer_market_session_date(stock_dfs)
    print(f"✅ Common market session: {market_session}")

    # Stage 2: deterministic SMC + price action.
    stage = time.time()
    candidates: List[Dict[str, Any]] = []
    for yf_symbol, info in symbol_map.items():
        df = stock_dfs.get(yf_symbol)
        if df is None:
            continue
        result = analyze_stock_from_df(df, info, market_session_date=market_session)
        if result:
            candidates.append(result)
    raw_candidate_count = len(candidates)
    candidates.sort(key=lambda x: candidate_sort_key(x, use_final=False), reverse=True)
    fund_candidates = _balanced_select(
        candidates,
        total_limit=MAX_FUND_CANDIDATES,
        post_limit=MAX_POST_FUND_CANDIDATES,
        use_final=False,
    )
    stage_times["technical_price_action"] = round(time.time() - stage, 2)
    print(
        f"✅ Technical/PA: {stage_times['technical_price_action']:.1f}s — "
        f"{raw_candidate_count} valid, top {len(fund_candidates)} to fundamentals"
    )

    # Stage 3: mandatory fundamental gate.
    stage = time.time()
    fundamental_results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(add_fundamental_safe, item) for item in fund_candidates]
        try:
            for future in as_completed(futures, timeout=OVERALL_STAGE_TIMEOUT):
                try:
                    result = future.result()
                    if result:
                        fundamental_results.append(result)
                except Exception:
                    continue
        except TimeoutError:
            print(f"⚠️ Fundamental stage exceeded {OVERALL_STAGE_TIMEOUT}s; using partial results")
            for future in futures:
                future.cancel()
    fundamental_results.sort(key=lambda x: candidate_sort_key(x, use_final=False), reverse=True)
    stage_times["fundamental"] = round(time.time() - stage, 2)
    print(f"✅ Fundamental: {stage_times['fundamental']:.1f}s — {len(fundamental_results)} passed")

    # Stage 4: one comparative, downgrade-only AI review.
    stage = time.time()
    preliminary_breadth = calculate_breadth(fundamental_results)
    try:
        fundamental_results, ai_response = apply_ai_layer(fundamental_results, preliminary_breadth)
    except Exception as exc:
        print(f"⚠️ AI layer failed safely: {exc}")
        ai_response = {"reviews": {}, "breadth_commentary_bn": "", "meta": {"available": False, "error": str(exc)[:200]}}
        for item in fundamental_results:
            item["ai_review"] = {}
            item["ai_penalty"] = 0
            item["final_score"] = item.get("rule_score", item["setup_score"])
            item["final_status"] = status_from_score(item["final_score"])
            item["priority"] = item["final_score"]
    stage_times["ai_review"] = round(time.time() - stage, 2)

    fundamental_results.sort(key=lambda x: candidate_sort_key(x, use_final=True), reverse=True)
    breadth = calculate_breadth(fundamental_results)
    total_seconds = (datetime.now(IST) - started).total_seconds()
    print(
        f"✅ AI: {stage_times['ai_review']:.1f}s — "
        f"available={ai_response.get('meta', {}).get('available', False)}"
    )
    print(f"🎉 TOTAL: {total_seconds:.0f}s ({total_seconds / 60:.1f} min)")

    artifact = _save_artifact(
        started,
        stage_times,
        len(stock_dfs),
        raw_candidate_count,
        fundamental_results,
        breadth,
        ai_response,
    )
    print(f"🧾 Audit artifact: {artifact}")
    _send_results(
        fundamental_results,
        breadth,
        ai_response,
        len(stock_dfs),
        len(symbols),
        total_seconds,
    )
    print("✅ Telegram delivery completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

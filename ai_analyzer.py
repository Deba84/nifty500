"""
Groq per-setup resilient reviewer for Nifty 500 Scanner v6.2.1 (AI hotfix).

The LLM is intentionally not the trading decision engine.  Python computes
all SMC, price-action, fundamental and R:R facts.  AI may only explain the
supplied evidence and apply a validated five-point caution penalty.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import requests

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
PRIMARY_MODEL = os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b"
FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL") or "openai/gpt-oss-20b"
AI_TIMEOUT_SECONDS = int(os.getenv("AI_TIMEOUT_SECONDS", "25"))
AI_CAUTION_PENALTY = int(os.getenv("AI_CAUTION_PENALTY", "5"))

REASON_CODES = {
    "COUNTER_WEEKLY_STRUCTURE",
    "FAST_APPROACH",
    "REPEATED_NEAR_TESTS",
    "BORDERLINE_RR",
    "FUNDAMENTAL_BORDERLINE",
    "DATA_COMPLETENESS",
    "STRUCTURE_MIXED",
    "NONE",
}

ALLOWED_EVIDENCE_REFS = {
    "setup_score",
    "rule_score",
    "rule_reasons",
    "signal_state",
    "distance_pct",
    "liquidity.primary_type",
    "liquidity.source_types",
    "liquidity.touch_count",
    "liquidity.cluster_width_pct",
    "liquidity.age_bars",
    "liquidity.prior_near_test_episodes",
    "liquidity.confluence_count",
    "price_action.gap_closed_3d",
    "price_action.gap_closed_5d",
    "price_action.directional_closes_3",
    "trend_daily",
    "trend_weekly",
    "range_position",
    "rr1",
    "rr2",
    "risk_pct",
    "fund_score",
    "confirmation.confirmation_score",
    "confirmation.sweep_penetration_pct",
    "confirmation.reclaimed",
    "confirmation.displacement",
    "confirmation.choch_confirmed",
}

BANNED_PHRASES = {
    "buy now",
    "strong_buy",
    "strong buy",
    "guaranteed",
    "guarantee",
    "নিশ্চিত লাভ",
    "এখনই কিনুন",
    "অবশ্যই লাভ",
}


def _review_item_schema(symbol: str) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "enum": [symbol]},
            "action": {"type": "string", "enum": ["KEEP", "CAUTION"]},
            "reason_codes": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(REASON_CODES)},
            },
            "evidence_refs": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(ALLOWED_EVIDENCE_REFS)},
            },
            "commentary_bn": {"type": "string"},
            "risks_bn": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "symbol", "action", "reason_codes", "evidence_refs",
            "commentary_bn", "risks_bn",
        ],
        "additionalProperties": False,
    }


def _review_schema(symbols: Sequence[str]) -> Dict[str, Any]:
    """Use required symbol keys so strict decoding cannot return a partial batch."""
    unique_symbols = list(dict.fromkeys(str(symbol) for symbol in symbols))
    review_properties = {
        symbol: _review_item_schema(symbol)
        for symbol in unique_symbols
    }
    return {
        "type": "object",
        "properties": {
            "review_version": {"type": "string", "enum": ["v6.2.1"]},
            "reviews": {
                "type": "object",
                "properties": review_properties,
                "required": unique_symbols,
                "additionalProperties": False,
            },
        },
        "required": ["review_version", "reviews"],
        "additionalProperties": False,
    }


def _compact_candidate(stock: Dict[str, Any]) -> Dict[str, Any]:
    liq = stock.get("liquidity", {}) or {}
    pa = stock.get("price_action", {}) or {}
    conf = stock.get("confirmation", {}) or {}
    fund = stock.get("fund_data", {}) or {}
    return {
        "symbol": stock.get("symbol"),
        "sector": stock.get("sector"),
        "data_as_of": stock.get("data_as_of"),
        "direction": stock.get("direction"),
        "signal_state": stock.get("signal_state"),
        "setup_score": stock.get("setup_score"),
        "rule_score": stock.get("rule_score", stock.get("setup_score")),
        "rule_reasons": stock.get("rule_reasons", []),
        "base_status": stock.get("base_status"),
        "distance_pct": stock.get("distance_pct"),
        "liquidity": {
            "primary_type": liq.get("primary_type"),
            "source_types": liq.get("source_types", []),
            "touch_count": liq.get("touch_count"),
            "cluster_width_pct": liq.get("cluster_width_pct"),
            "age_bars": liq.get("age_bars"),
            "prior_near_test_episodes": liq.get("prior_near_test_episodes"),
            "confluence_count": liq.get("confluence_count"),
            "swept_since_formation": liq.get("swept_since_formation"),
        },
        "price_action": {
            "gap_closed_3d": pa.get("gap_closed_3d"),
            "gap_closed_5d": pa.get("gap_closed_5d"),
            "directional_closes_3": pa.get("directional_closes_3"),
            "moving_toward": pa.get("moving_toward"),
            "flags": pa.get("flags", []),
        },
        "trend_daily": stock.get("trend_daily"),
        "trend_weekly": stock.get("trend_weekly"),
        "range_position": stock.get("range_position"),
        "structure": {
            "daily": stock.get("trend_daily"),
            "weekly": stock.get("trend_weekly"),
            "range_position": stock.get("range_position"),
        },
        "trade": {
            "plan_type": stock.get("plan_type"),
            "risk_pct": stock.get("risk_pct"),
            "rr1": stock.get("rr1"),
            "rr2": stock.get("rr2"),
        },
        "fund_score": stock.get("fund_score"),
        "fundamental": {
            "score": stock.get("fund_score"),
            "roe": fund.get("ROE"),
            "roce": fund.get("ROCE"),
            "pe": fund.get("Stock P/E"),
            "promoter": fund.get("Promoter Holding"),
        },
        "confirmation": {
            "confirmation_score": conf.get("confirmation_score"),
            "sweep_penetration_pct": conf.get("sweep_penetration_pct"),
            "reclaimed": conf.get("reclaimed"),
            "displacement": conf.get("displacement"),
            "choch_confirmed": conf.get("choch_confirmed"),
        },
        "quality_flags": stock.get("quality_flags", []),
    }


def _closed_world_prompt(stocks: Sequence[Dict[str, Any]], breadth: Dict[str, Any]) -> str:
    payload = {
        "snapshot_scope": "Nifty 500 scanner candidates only",
        "breadth": breadth,
        "candidates": [_compact_candidate(s) for s in stocks],
    }
    symbols = [str(stock.get("symbol")) for stock in stocks]
    output_shape = {
        "review_version": "v6.2.1",
        "reviews": {
            symbol: {
                "symbol": symbol,
                "action": "KEEP",
                "reason_codes": ["NONE"],
                "evidence_refs": ["setup_score", "signal_state"],
                "commentary_bn": "সংক্ষিপ্ত evidence-based বাংলা review",
                "risks_bn": ["Confirmation ছাড়া entry নয়"],
            }
            for symbol in symbols
        },
    }
    return f"""Review the supplied Indian swing-trading candidates using ONLY the JSON snapshot below.

NON-NEGOTIABLE RULES:
1. These are SMC/liquidity setups. Every PRE_SWEEP candidate is a watch item, never an entry.
2. Never say BUY NOW, STRONG_BUY, guaranteed reversal, or guaranteed profit.
3. Do not invent news, FII/DII activity, institutional interest, macro facts, or a current sector outlook.
4. Do not alter setup_score, direction, entry, SL, TP, R:R, or status.
5. You may return CAUTION only when one of the supplied objective facts supports an allowed reason code.
6. Use concise natural Bengali for commentary and risks. Missing data is unknown.
7. Compare all candidates under the same rubric. Return exactly one review for every supplied symbol, with no duplicates.
8. Use 1-2 reason codes, 2-4 evidence refs, one concise commentary (max 260 chars), and 1-2 risks.
9. Return candidate reviews only. Do not produce market, sector, or breadth narrative.

ALLOWED REASON CODES:
{', '.join(sorted(REASON_CODES))}

ALLOWED EVIDENCE REFS:
{', '.join(sorted(ALLOWED_EVIDENCE_REFS))}

SNAPSHOT JSON:
{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}

OUTPUT JSON SHAPE (replace example text with factual Bengali):
{json.dumps(output_shape, ensure_ascii=False, separators=(',', ':'))}

Return only this JSON object, with every supplied symbol exactly once."""


def _api_call(model: str, prompt: str, symbols: Sequence[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    schema = _review_schema(symbols)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a closed-world risk reviewer for a deterministic SMC scanner. "
                    "Use only supplied facts and return strict schema-compliant JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_completion_tokens": 800,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "nifty500_v621_single_review",
                "strict": True,
                "schema": schema,
            },
        },
    }
    try:
        response = requests.post(
            GROQ_URL,
            headers=headers,
            json=payload,
            timeout=(5, AI_TIMEOUT_SECONDS),
        )
        if not response.ok:
            return None, f"HTTP {response.status_code}: {response.text[:240]}"
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content), None
    except Exception as exc:
        return None, str(exc)[:240]


def _api_call_json_object(model: str, prompt: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Compatibility fallback using Groq's simpler JSON Object Mode."""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Review exactly one supplied SMC setup. Use only supplied facts. "
                    "Return one valid JSON object in the requested shape."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_completion_tokens": 800,
        "response_format": {"type": "json_object"},
    }
    try:
        response = requests.post(
            GROQ_URL,
            headers=headers,
            json=payload,
            timeout=(5, AI_TIMEOUT_SECONDS),
        )
        if not response.ok:
            return None, f"HTTP {response.status_code}: {response.text[:240]}"
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content), None
    except Exception as exc:
        return None, str(exc)[:240]


def _contains_banned_claim(text: str) -> bool:
    lowered = (text or "").casefold()
    return any(phrase.casefold() in lowered for phrase in BANNED_PHRASES)


def _state_text_mismatch(stock: Dict[str, Any], text: str) -> bool:
    lowered = (text or "").casefold()
    state = stock.get("signal_state", "PRE_SWEEP")
    if state == "PRE_SWEEP":
        impossible_terms = {
            "level reclaimed", "reclaimed", "পুনরুদ্ধার", "choch confirmed",
            "trigger ready", "sweep সম্পন্ন",
        }
    else:
        impossible_terms = {"pre-sweep", "pre sweep", "প্রি-সুইপ", "unswept setup"}
    return any(term.casefold() in lowered for term in impossible_terms)


def _supported_reason(stock: Dict[str, Any], code: str) -> bool:
    flags = set(stock.get("quality_flags", []) or [])
    if code == "NONE":
        return True
    if code in {"COUNTER_WEEKLY_STRUCTURE", "FAST_APPROACH", "REPEATED_NEAR_TESTS", "STRUCTURE_MIXED"}:
        return code in flags
    if code == "BORDERLINE_RR":
        return float(stock.get("rr1", 999) or 999) < 2.5
    if code == "FUNDAMENTAL_BORDERLINE":
        return int(stock.get("fund_score", 0) or 0) == 2
    if code == "DATA_COMPLETENESS":
        return int((stock.get("score_breakdown", {}) or {}).get("data_integrity", 0)) < 5
    return False


def validate_review_payload(
    payload: Any,
    stocks: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], str, List[str]]:
    """Semantic validation in addition to Groq's syntactic JSON schema."""
    errors: List[str] = []
    if not isinstance(payload, dict):
        return {}, "", ["payload is not an object"]
    expected = {str(s.get("symbol")): s for s in stocks}
    reviews = payload.get("reviews")
    if isinstance(reviews, dict):
        review_items = []
        for key, value in reviews.items():
            if not isinstance(value, dict):
                errors.append(f"non-object review: {key}")
                continue
            item = dict(value)
            if item.get("symbol") != key:
                errors.append(f"symbol key mismatch: {key}")
                continue
            review_items.append(item)
    elif isinstance(reviews, list):
        # Backward-compatible parser for archived v6.2 test fixtures.
        review_items = reviews
    else:
        return {}, "", ["reviews is neither an object nor a list"]

    validated: Dict[str, Dict[str, Any]] = {}
    for item in review_items:
        if not isinstance(item, dict):
            errors.append("non-object review")
            continue
        symbol = str(item.get("symbol", ""))
        if symbol not in expected or symbol in validated:
            errors.append(f"unknown/duplicate symbol: {symbol}")
            continue
        action = item.get("action")
        if action not in {"KEEP", "CAUTION"}:
            errors.append(f"bad action: {symbol}")
            continue
        codes = [c for c in item.get("reason_codes", []) if c in REASON_CODES]
        supported = [c for c in codes if _supported_reason(expected[symbol], c)]
        non_none = [c for c in supported if c != "NONE"]
        # AI cannot downgrade without an objectively supported caution.
        if action == "CAUTION" and not non_none:
            action = "KEEP"
            supported = ["NONE"]
            errors.append(f"unsupported caution neutralized: {symbol}")
        if action == "KEEP" and not supported:
            supported = ["NONE"]

        refs = [x for x in item.get("evidence_refs", []) if x in ALLOWED_EVIDENCE_REFS]
        refs = list(dict.fromkeys(refs))[:4]
        commentary = str(item.get("commentary_bn", ""))[:260]
        risks = [str(x)[:180] for x in item.get("risks_bn", []) if str(x).strip()][:2]
        combined = " ".join([commentary] + risks)
        if len(refs) < 2 or _contains_banned_claim(combined) or _state_text_mismatch(expected[symbol], combined):
            commentary = ""
            risks = []
            # Keep an objectively supported caution, but never display unsafe text.
            if action == "CAUTION" and non_none:
                supported = non_none[:2]
            else:
                action = "KEEP"
                supported = ["NONE"]
            errors.append(f"unsafe/unsupported text suppressed: {symbol}")

        validated[symbol] = {
            "action": action,
            "reason_codes": supported[:2],
            "evidence_refs": refs,
            "commentary": commentary,
            "risks": risks,
        }

    missing = sorted(set(expected) - set(validated))
    if missing:
        errors.append(f"missing reviews: {','.join(missing)}")
    return validated, "", errors


def _review_one_setup(
    stock: Dict[str, Any],
    breadth: Dict[str, Any],
) -> Tuple[str, Optional[Dict[str, Any]], Dict[str, Any]]:
    """Review one stock independently so one bad response cannot drop all top-8 reviews."""
    symbol = str(stock.get("symbol"))
    prompt = _closed_world_prompt([stock], breadth)
    attempts: List[Dict[str, str]] = []

    # First choice: small, one-symbol strict schema on the high-quality model.
    raw, error = _api_call(PRIMARY_MODEL, prompt, [symbol])
    attempts.append({"model": PRIMARY_MODEL, "mode": "strict", "error": error or ""})
    if raw is not None:
        reviews, _, validation_errors = validate_review_payload(raw, [stock])
        if symbol in reviews:
            return symbol, reviews[symbol], {
                "model": PRIMARY_MODEL,
                "fallback_used": False,
                "validation_errors": validation_errors,
                "attempts": attempts,
            }
        attempts[-1]["error"] = "; ".join(validation_errors[:3]) or "missing symbol review"

    # Compatibility fallback: simple JSON mode on the cheaper/faster model.
    raw, error = _api_call_json_object(FALLBACK_MODEL, prompt)
    attempts.append({"model": FALLBACK_MODEL, "mode": "json_object", "error": error or ""})
    if raw is not None:
        reviews, _, validation_errors = validate_review_payload(raw, [stock])
        if symbol in reviews:
            return symbol, reviews[symbol], {
                "model": FALLBACK_MODEL,
                "fallback_used": True,
                "validation_errors": validation_errors,
                "attempts": attempts,
            }
        attempts[-1]["error"] = "; ".join(validation_errors[:3]) or "missing symbol review"

    return symbol, None, {
        "model": "deterministic",
        "fallback_used": True,
        "validation_errors": [],
        "attempts": attempts,
    }


def review_setups(
    top_stocks: Sequence[Dict[str, Any]],
    breadth: Dict[str, Any],
) -> Dict[str, Any]:
    """Review up to eight setups independently with three parallel workers."""
    stocks = list(top_stocks[:8])
    if not stocks:
        return {"reviews": {}, "breadth_commentary_bn": "", "meta": {"available": False, "error": "no candidates"}}
    if not GROQ_API_KEY:
        return {"reviews": {}, "breadth_commentary_bn": "", "meta": {"available": False, "error": "GROQ_API_KEY not set"}}

    started = time.time()
    reviews: Dict[str, Dict[str, Any]] = {}
    per_symbol_meta: Dict[str, Dict[str, Any]] = {}
    workers = min(3, len(stocks))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_review_one_setup, stock, breadth): stock for stock in stocks}
        for future in as_completed(futures):
            stock = futures[future]
            symbol = str(stock.get("symbol"))
            try:
                returned_symbol, review, meta = future.result()
                per_symbol_meta[returned_symbol] = meta
                if review:
                    reviews[returned_symbol] = review
            except Exception as exc:
                per_symbol_meta[symbol] = {
                    "model": "deterministic",
                    "fallback_used": True,
                    "attempts": [{"model": "worker", "mode": "exception", "error": str(exc)[:240]}],
                }

    requested_symbols = [str(stock.get("symbol")) for stock in stocks]
    missing = sorted(set(requested_symbols) - set(reviews))
    used_models = sorted({meta.get("model") for symbol, meta in per_symbol_meta.items() if symbol in reviews})
    model_label = used_models[0] if len(used_models) == 1 else "mixed" if used_models else "deterministic"
    return {
        "reviews": reviews,
        "breadth_commentary_bn": "",
        "meta": {
            "available": bool(reviews),
            "model": model_label,
            "requested_count": len(stocks),
            "reviewed_count": len(reviews),
            "missing_symbols": missing,
            "fallback_used": any(meta.get("fallback_used") for meta in per_symbol_meta.values()),
            "latency_seconds": round(time.time() - started, 2),
            "per_symbol": per_symbol_meta,
            "error": "all AI attempts failed" if not reviews else "",
        },
    }


def apply_review(stock: Dict[str, Any], review: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply downgrade-only penalty and deterministic final status."""
    from scanner_engine import status_from_score

    item = stock
    base = int(item.get("rule_score", item.get("setup_score", 0)))
    action = (review or {}).get("action", "KEEP")
    penalty = AI_CAUTION_PENALTY if action == "CAUTION" else 0
    item["ai_review"] = review or {}
    item["ai_penalty"] = penalty
    item["final_score"] = max(0, base - penalty)
    item["final_status"] = status_from_score(item["final_score"])
    item["priority"] = item["final_score"]
    return item


# Backwards-compatible single-setup wrapper.
def analyze_setup(stock_data: Dict[str, Any]) -> Dict[str, Any]:
    result = review_setups([stock_data], {"setup_flow": "SINGLE_SETUP", "note": "not market breadth"})
    review = result.get("reviews", {}).get(stock_data.get("symbol"), {})
    return {
        "verdict": review.get("action", "N/A"),
        "confidence": stock_data.get("setup_score", 0),
        "commentary": review.get("commentary", ""),
        "risks": review.get("risks", []),
        "position_sizing": "RULE_BASED",
        "meta": result.get("meta", {}),
    }


def get_market_overview(top_stocks: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Deprecated compatibility helper: deliberately avoids invented market bias."""
    if not top_stocks:
        return None
    longs = sum(1 for x in top_stocks if x.get("direction") == "LONG")
    total = len(top_stocks)
    ratio = longs / total if total else 0.5
    flow = "LONG_HEAVY" if ratio >= 0.65 else "SHORT_HEAVY" if ratio <= 0.35 else "BALANCED"
    return {
        "overall_market": "NOT_INFERRED",
        "dominant_theme": f"Scanner setup flow: {flow}",
        "best_pick_reasoning": "Highest deterministic setup score; TradingView confirmation required.",
        "market_wisdom": "Sweep, rejection ও CHoCH ছাড়া entry নয়।",
    }

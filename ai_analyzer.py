"""
AI Analyzer using Groq — Ultra-fast LLM analysis
Provides:
1. Setup verdict (BUY/WAIT/SKIP with confidence)
2. Analyst commentary (Bengali/English)
3. Risk assessment
4. Sector context
"""
import os
import json
import requests
import time

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"  # Fast & powerful


def analyze_setup(stock_data):
    """
    Ask Groq to analyze a setup and provide:
    - Verdict (BUY/WAIT/SKIP)
    - Confidence (0-10)
    - Commentary in Bengali
    - Risk factors
    """
    if not GROQ_API_KEY:
        return {
            "verdict": "N/A",
            "confidence": 0,
            "commentary": "AI analysis unavailable (GROQ_API_KEY not set)",
            "risks": [],
            "position_sizing": "Standard",
        }

    # Prepare context
    fund = stock_data.get('fund_data', {}) or {}
    prompt = f"""You are an expert Indian stock market analyst specializing in Smart Money Concepts (SMC) and liquidity-based swing trading.

Analyze this setup and respond in JSON format only.

STOCK: {stock_data['symbol']} ({stock_data['name']})
Sector: {stock_data['sector']}
Current Price: ₹{stock_data['price']}
Today's Change: {stock_data.get('change_pct', 0)}%

LIQUIDITY SETUP:
- Direction: {stock_data['direction']}
- Stage: {stock_data['stage']}
- Distance from liquidity: {stock_data['distance_pct']}% away
- Target level: ₹{stock_data['target_level']} ({stock_data['target_level_type']})
- Setup type: Price approaching {stock_data['liq_type']} — wait for sweep + CHoCH

TRADE PLAN:
- Entry Zone: ₹{stock_data['entry_zone'][0]}-{stock_data['entry_zone'][1]} (after sweep)
- SL: ₹{stock_data['sl']}
- TP1: ₹{stock_data['tp1']} (R:R {stock_data['rr1']}:1)
- TP2: ₹{stock_data['tp2']} (R:R {stock_data['rr2']}:1)
- Risk: {stock_data['risk_pct']}%

TREND:
- Daily: {stock_data['trend_daily']}
- Weekly: {stock_data['trend_weekly']}

FUNDAMENTAL:
- Fund Score: {stock_data.get('fund_score', 0)}/4
- ROE: {fund.get('ROE', 'N/A')}%
- ROCE: {fund.get('ROCE', 'N/A')}%
- P/E: {fund.get('Stock P/E', 'N/A')}
- Promoter: {fund.get('Promoter Holding', 'N/A')}%

52-Week Range: ₹{stock_data['wk52_low']}-{stock_data['wk52_high']}

Provide analysis in this JSON format (Bengali commentary, English keys):
{{
  "verdict": "STRONG_BUY / BUY / WAIT / SKIP",
  "confidence": 0-10,
  "commentary_bn": "১-২ লাইন Bangla analysis (setup quality, why worth watching)",
  "sector_view": "Bullish / Neutral / Bearish for this sector currently",
  "risks": ["risk1", "risk2"],
  "position_sizing": "FULL / REDUCED / MINIMAL",
  "extra_note": "Any important observation (Bengali)"
}}

Be concise, factual, and honest. Consider Indian market context. Response must be valid JSON only."""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a professional Indian stock market analyst. Respond only in valid JSON format."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }

    try:
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=15)
        if r.ok:
            data = r.json()
            content = data['choices'][0]['message']['content']
            result = json.loads(content)
            return {
                "verdict": result.get("verdict", "WAIT"),
                "confidence": int(result.get("confidence", 5)),
                "commentary": result.get("commentary_bn", ""),
                "sector_view": result.get("sector_view", "Neutral"),
                "risks": result.get("risks", []),
                "position_sizing": result.get("position_sizing", "REDUCED"),
                "extra_note": result.get("extra_note", ""),
            }
        else:
            print(f"⚠️ Groq API error: {r.status_code} - {r.text[:200]}")
            return {"verdict": "N/A", "confidence": 0, "commentary": "AI call failed", "risks": [], "position_sizing": "REDUCED"}
    except Exception as e:
        print(f"⚠️ Groq exception: {e}")
        return {"verdict": "N/A", "confidence": 0, "commentary": f"Error: {str(e)[:100]}", "risks": [], "position_sizing": "REDUCED"}


def get_market_overview(top_stocks):
    """
    Get overall market analysis from top setups.
    Called once per scan.
    """
    if not GROQ_API_KEY or not top_stocks:
        return None

    stocks_summary = "\n".join([
        f"- {s['symbol']} ({s['sector']}): {s['direction']} setup, Fund {s.get('fund_score',0)}/4, "
        f"Priority {s.get('priority',0)}"
        for s in top_stocks[:10]
    ])

    prompt = f"""Analyze today's Indian market swing trading opportunities:

TOP 10 SETUPS TODAY:
{stocks_summary}

Provide brief market overview in JSON:
{{
  "overall_market": "BULLISH / NEUTRAL / BEARISH",
  "dominant_theme": "e.g., Pharma rotation, Banking weakness, etc. (Bengali)",
  "best_pick_reasoning": "Which of these looks best and WHY (Bengali, 2 lines)",
  "market_wisdom": "1 line Bengali trading advice for today"
}}

Response in valid JSON only."""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a professional Indian stock market strategist. Respond only in valid JSON."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.4,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }

    try:
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=15)
        if r.ok:
            data = r.json()
            content = data['choices'][0]['message']['content']
            return json.loads(content)
    except Exception as e:
        print(f"⚠️ Market overview error: {e}")
    return None

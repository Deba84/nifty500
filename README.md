# 🌊 Nifty 500 Liquidity + Price Action Scanner v6.2

Automated Nifty 500 swing-trading scanner for **1D + 1W Smart Money Concepts (SMC)**. It uses raw OHLC structure and liquidity behaviour—no traditional indicators.

## What v6.2 does

### Pre-sweep watch engine

- Finds BSL/SSL from Equal Highs/Lows, major swings, PWH/PWL, PMH/PML and 52-week extremes
- Keeps formation date, touches, cluster width and confluence metadata
- Verifies every candle since formation before calling a level “untapped”
- Requires price to be genuinely moving toward the level
- Maximum distance: 1.5%

### Post-sweep price-action engine

```text
Liquidity sweep
   → reclaim/rejection
   → reversal displacement
   → daily CHoCH
   → structural R:R recheck
   → TRIGGER_READY (TradingView verification still required)
```

### Hybrid quality system

- Deterministic Setup Quality score: 0–100
- Mandatory fundamental gate: minimum 2/4
- Minimum conservative TP1 R:R: 2:1
- Groq AI is a closed-world comparative reviewer
- AI can only apply a validated caution/downgrade; it cannot upgrade a setup or change trade levels
- AI failure never stops the deterministic scan

## Safe status labels

| Status | Meaning |
|---|---|
| `PRIME_WATCH` | High-quality watch; entry still requires confirmation |
| `WATCH` | Valid watch with reduced post-confirmation risk |
| `WAIT` | Not ready |
| `SKIP` | Rejected |
| `TRIGGER_READY` | Daily PA sequence detected; manually verify chart/retest |

`Setup Quality` is a ranking score, **not a win probability**.

## Pipeline

1. Batch download 500 stocks in chunks of 100
2. Deterministic SMC + price-action analysis
3. Balanced shortlist: pre-sweep core + limited post-sweep monitoring
4. Fundamental analysis for maximum 40 candidates
5. One comparative Groq review for maximum 8 candidates
6. Telegram alerts
7. JSON audit artifact uploaded by GitHub Actions

## GitHub Secrets

Required:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
GROQ_API_KEY
```

Optional GitHub Repository Variables:

```text
GROQ_MODEL=openai/gpt-oss-120b
GROQ_FALLBACK_MODEL=openai/gpt-oss-20b
```

If variables are absent, the code uses those defaults. The retired `llama-3.3-70b-versatile` model is not used.

## Schedule

```yaml
cron: '45 11 * * 1-5'
```

11:45 UTC = 5:15 PM IST, Monday–Friday.

## Local verification

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python daily_scan.py
```

The full scan requires Telegram credentials. Groq is optional; without it, deterministic alerts still work.

## Main configuration

| Environment variable | Default |
|---|---:|
| `MIN_RR` | `2.0` |
| `MAX_LIQ_DISTANCE_PCT` | `1.5` |
| `MAX_FUND_CANDIDATES` | `40` |
| `MAX_POST_FUND_CANDIDATES` | `12` |
| `MAX_AI_ANALYSIS` | `8` |
| `MAX_POST_AI_ANALYSIS` | `3` |
| `AI_CAUTION_PENALTY` | `5` |
| `CONFIRMATION_LOOKBACK` | `6` bars |

## Risk rules

- PRE_SWEEP alert is never an entry signal
- Wait for sweep + reclaim/rejection + CHoCH
- Recheck entry, actual sweep-wick SL and R:R on TradingView
- Maximum account risk: 1%
- `PRIME_WATCH`: up to 1% only after confirmation
- `WATCH`: up to 0.5% only after confirmation
- If uncertain, skip

## Audit trail

Each production run writes:

```text
artifacts/scan_YYYY-MM-DD_HHMMSS.json
```

GitHub Actions uploads it for 30 days. It includes stage timings, component scores, validated AI review and final rankings—but no secrets.

## Files

```text
scanner_engine.py       deterministic liquidity + PA state machine
daily_scan.py           orchestration, fundamentals, Telegram, artifacts
ai_analyzer.py           strict closed-world Groq reviewer
fundamental_analyzer.py Screener.in parser and 0–4 gate
nifty500_list.py         Nifty 500 universe
 tests/                  deterministic regression tests
```

## Disclaimer

Educational scanner only. Market data and automated structure detection can be incomplete or wrong. Always verify the TradingView chart. Historical or backtested performance does not guarantee future results.

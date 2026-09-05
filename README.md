# 🌊 Nifty 500 Liquidity + Price Action Scanner v6.3.0

Automated Nifty 500 swing-trading scanner for **1D + 1W Smart Money Concepts (SMC)**. It uses raw OHLC structure and liquidity behaviour—no traditional indicators.

## v6.2.1 quality improvements

- Strict AI schema requires one review for every submitted top-8 symbol
- Deterministic breadth replaces free-form AI breadth commentary
- Fresh age-0/1/2 standalone 52W extremes are filtered out
- Major caution flags block `PRIME_WATCH`
- Weak post-sweep confirmation is capped at `WAIT` or `WATCH`
- Pending post-sweep trade plans remain locked until CHoCH
- GitHub Actions use Node 24-compatible action versions
- Pre-sweep `AT LEVEL`/`VERY CLOSE` setups are sent first as `PRE-SWEEP ARMED`; post-sweep pending alerts are de-emphasized

## v6.3.0 accuracy improvements

- Deterministic market breadth and sector breadth context
- Average traded value and relative-volume liquidity checks
- ATR volatility and gap-risk flags
- Counter-market and counter-sector penalties
- Penalty-only execution-quality layer; weak technical setups cannot be upgraded
- Outcome ledger for 5, 10 and 20-bar forward evaluation
- Walk-forward evaluator for selected symbols
- Same-candle stop/target collisions are recorded as `AMBIGUOUS_SAME_BAR`

## What v6.3.0 does

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

### Deterministic execution-quality layer

The quality layer is a penalty-only ranking guard. It uses:

- 20-day average traded value and relative volume
- ATR-based volatility and gap-risk flags
- Cross-sectional Nifty breadth and sector breadth
- Invalid-data and low-price checks

Defaults can be changed with environment variables:

| Environment variable | Default |
|---|---:|
| `MIN_AVG_DAILY_TURNOVER` | `10000000` |
| `MIN_RELATIVE_VOLUME` | `0.50` |
| `MAX_GAP_PCT` | `4.0` |
| `MAX_ATR_PCT` | `8.0` |
| `COUNTER_REGIME_PENALTY` | `5` |
| `COUNTER_SECTOR_PENALTY` | `3` |

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
8. Signal outcome ledger update

## GitHub Secrets

Required for Telegram alerts and the full scan:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Optional:

```text
GROQ_API_KEY
```

Without `GROQ_API_KEY`, the deterministic scanner still runs without the AI review layer.

The production workflow keeps outcome tracking enabled and treats a ledger
write failure as a failed scan rather than silently reporting success.

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
| `MIN_AVG_DAILY_TURNOVER` | `10000000` |
| `MIN_RELATIVE_VOLUME` | `0.50` |
| `MAX_GAP_PCT` | `4.0` |
| `MAX_ATR_PCT` | `8.0` |
| `COUNTER_REGIME_PENALTY` | `5` |
| `COUNTER_SECTOR_PENALTY` | `3` |
| `STRICT_OUTCOME_TRACKING` | `true` |

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

## Outcome tracking and research

Each run updates `data/signal_outcomes.json`. The ledger evaluates completed daily candles at 5, 10 and 20 bars and records TP1, SL, open-at-horizon or `AMBIGUOUS_SAME_BAR` when both stop and target occur inside one candle.

Use the ledger and walk-forward evaluator to inspect behaviour before changing score thresholds:

```bash
python backtest.py --symbol RELIANCE.NS --symbol HDFCBANK.NS --period 5y
```

Do not treat the resulting statistics as a guarantee of future performance.

## Files

```text
scanner_engine.py        deterministic liquidity + PA state machine
daily_scan.py            orchestration, fundamentals, Telegram, artifacts
ai_analyzer.py           strict closed-world Groq reviewer
quality_enhancements.py  market/sector breadth and execution-quality penalties
outcome_tracker.py       signal ledger and forward-outcome evaluation
backtest.py              selected-symbol walk-forward evaluator
fundamental_analyzer.py  Screener.in parser and 0–4 gate
nifty500_list.py         Nifty 500 universe
data/signal_outcomes.json persisted signal outcome ledger
tests/                   deterministic regression tests
```

## Disclaimer

Educational scanner only. Market data and automated structure detection can be incomplete or wrong. Always verify the TradingView chart. Historical or backtested performance does not guarantee future results.
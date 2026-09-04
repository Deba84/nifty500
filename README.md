# 🌊 Nifty 500 Liquidity + Price Action Scanner v6.3.0

Automated Nifty 500 swing-trading scanner for 1D + 1W Smart Money Concepts
(SMC). It uses raw OHLC structure, liquidity behaviour, market breadth and
execution-quality checks. It does not place trades.

## What it does

1. Downloads the Nifty 500 universe in batches.
2. Finds liquidity from equal highs/lows, major swings, prior
   week/month levels and 52-week extremes.
3. Checks pre-sweep approach and post-sweep
   `sweep → reclaim/rejection → displacement → CHoCH`.
4. Applies daily/weekly structure, market breadth, sector breadth, volume,
   turnover, volatility and gap-risk checks.
5. Applies a fundamental gate for selected candidates.
6. Uses Groq only as a closed-world, downgrade-only reviewer.
7. Sends Telegram alerts and writes a JSON audit artifact.

`PRIME_WATCH`, `WATCH` and `TRIGGER_READY` are not buy signals. Confirm the
chart, retest, stop-loss and risk/reward manually before any decision.

## Quality layer

The deterministic execution-quality layer adds:

- 20-day average traded value and relative volume
- ATR-based volatility and gap-risk flags
- cross-sectional Nifty breadth and sector breadth
- counter-market and counter-sector penalties
- invalid data and low-price checks

It is penalty-only: these proxies cannot inflate a technically weak setup.
Defaults can be changed with environment variables:

| Variable | Default |
|---|---:|
| `MIN_AVG_DAILY_TURNOVER` | `10000000` |
| `MIN_RELATIVE_VOLUME` | `0.50` |
| `MAX_GAP_PCT` | `4.0` |
| `MAX_ATR_PCT` | `8.0` |
| `COUNTER_REGIME_PENALTY` | `5` |
| `COUNTER_SECTOR_PENALTY` | `3` |

## Secrets

Required for Telegram alerts and the full scan:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Optional:

```text
GROQ_API_KEY
```

Without `GROQ_API_KEY`, the deterministic scanner still runs without the AI
review layer.

## Outcome tracking

Each run updates `data/signal_outcomes.json`. The ledger evaluates completed
daily candles at 5, 10 and 20 bars and records TP1, SL, open-at-horizon or
`AMBIGUOUS_SAME_BAR` when both stop and target occur inside one candle.

Do not change score thresholds based on one run. Use the ledger and the
walk-forward research tool:

```bash
python backtest.py --symbol RELIANCE.NS --symbol HDFCBANK.NS --period 5y
```

## Local verification

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python daily_scan.py
```

The full scan requires Telegram credentials. Groq is optional.

## Schedule

```yaml
cron: '45 11 * * 1-5'
```

11:45 UTC = 5:15 PM IST, Monday–Friday.

## Risk rules

- PRE_SWEEP is never an entry signal.
- Wait for sweep + reclaim/rejection + daily CHoCH.
- Recheck entry, actual sweep-wick SL and R:R on TradingView.
- Maximum account risk: 1%.
- Setup Quality is a ranking score, not a win probability.

## Disclaimer

Educational scanner only. Market data and automated structure detection can be
incomplete or wrong. Always verify the chart. Historical or backtested
performance does not guarantee future results.
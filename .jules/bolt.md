# Bolt's Journal - Critical Learnings

## 2026-03-09 - Avoid Pandas `.iloc` in Tight Loops
**Learning:** Calling `df['High'].iloc[i]` or `df['Low'].iloc[i]` inside a loop across hundreds of bars for every liquidity level creates significant overhead due to Pandas indexing mechanisms.
**Action:** Always pre-extract the series to a 1D NumPy array using `df['High'].to_numpy(dtype=float)` before looping when indexing repeatedly by integer position.

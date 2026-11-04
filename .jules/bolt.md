## 2025-05-18 - Avoid Pandas `.iloc` indexing in hot loops

**Learning:** In `scanner_engine._count_near_test_episodes`, indexing a `pd.Series` via `.iloc[i]` repeatedly inside a loop over historical bar ranges incurs significant pandas Series/Index overhead, accounting for ~43% of total stock analysis time across 500 stocks. Converting the high/low price Series to a NumPy array (`to_numpy(dtype=float)`) once per invocation and operating directly on slice distances eliminated pandas indexing overhead and sped up total stock analysis runtime by ~39% (from 38.0s down to 23.2s for 500 stocks).

**Action:** Whenever iterating across DataFrame/Series bar indices in loops (such as historical approach/touch checks or sweep scanning), always extract underlying price vectors via `to_numpy()` first instead of using `.iloc[i]` repeatedly.

## 2026-03-01 - Avoid Pandas `.iloc` indexing in tight loops over historical price series

**Learning:** Accessing `df["High"].iloc[i]` or `df["Low"].iloc[i]` inside loops across multiple liquidity levels creates significant pandas overhead. Converting OHLC series to 1D NumPy float arrays (`df["High"].to_numpy(dtype=float)`) before indexing in loops like `_count_near_test_episodes` speeds up full stock analysis by ~35-40%.

**Action:** When performing element-wise iteration or indexed loops over DataFrame columns in scanner engines, extract `to_numpy(dtype=float)` once outside the loop.

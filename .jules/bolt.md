## 2026-03-01 - Avoid Pandas `.iloc[i]` Overhead in High-Frequency Loops

**Learning:** In Pandas DataFrame / Series operations, repeated indexing using `.iloc[i]` inside loops incurs heavy Pandas Series overhead (manager access, index finalizing, dtype validation). In `_count_near_test_episodes`, converting `df["High"]` or `df["Low"]` to a 1D NumPy float array (`to_numpy(dtype=float)`) outside the per-bar loop sped up the call by ~22.5x and the overall `build_liquidity_levels` execution by ~2.05x.

**Action:** Whenever iterating over row indices of a DataFrame column, convert the target Series to a NumPy array (`.to_numpy(dtype=float)`) prior to the loop.

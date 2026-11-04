## 2025-05-18 - Vectorizing Pandas `.iloc` Loops & Replacing `np.median` on Small Lists

**Learning:**
1. Accessing Pandas `.iloc[i]` row-by-row inside Python loops incurs severe indexing overhead (~77x slower than NumPy array operations). Converting series slices to NumPy arrays via `.to_numpy(dtype=float)` before indexing or applying boolean masks eliminates Pandas Series construction overhead.
2. Calling `np.median()` or `np.nanmax()` on small Python lists/windows inside frequent loops creates NumPy array object allocation overhead (~33x slower than pure-Python math or NumPy sliding window views).

**Action:**
1. Always convert Pandas Series to NumPy arrays before looping or slicing in hot paths (`_count_near_test_episodes`).
2. Use `np.lib.stride_tricks.sliding_window_view` for rolling window max/min operations on arrays (`find_swings`).
3. Use fast pure-Python arithmetic for medians/statistics on small Python lists (`_cluster_pivots`, `_new_level`).

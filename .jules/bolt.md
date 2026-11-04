## 2025-05-18 - Vectorize pandas `.iloc` in tight loops
**Learning:** In pandas DataFrames, row-by-row `.iloc` lookups inside tight loops (like checking historical near-test episodes across multiple liquidity levels) incur massive overhead due to Pandas indexing and Series creation.
**Action:** Extract slice values as raw NumPy arrays using `.to_numpy(dtype=float)` and perform vectorized distance and masking operations using NumPy (`np.arange`, `np.maximum`, `np.diff`). This yields a ~76x speedup for function execution and ~32% speedup for full universe analysis.

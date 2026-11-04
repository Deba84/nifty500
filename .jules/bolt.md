## 2026-03-31 - Pandas `.iloc` Indexing Bottleneck in Stock Analysis Loops

**Learning:** Indexing Pandas DataFrames with `.iloc[i]` inside tight loops over historical stock prices (e.g., inside liquidity touch and historical near-test episode evaluations) introduces severe Python function call overhead (~43% of total execution time across 500 stocks). Pre-extracting `.to_numpy()` arrays once and using NumPy indexing or direct slice comparisons eliminates DataFrame overhead and yields a 2.86x execution speedup.

**Action:** Whenever iterating over price bar positions, extract `to_numpy()` arrays for High/Low/Close columns prior to entering loops or pass pre-extracted arrays into helper functions.

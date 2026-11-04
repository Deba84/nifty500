## 2025-05-20 - NumPy Extraction for Pandas Series in Loops
**Learning:** Calling `df['High'].iloc[i]` repeatedly inside loops incurs substantial overhead from Pandas Indexing and Series creation. Converting `df['High']` / `df['Low']` to NumPy 1D arrays (`to_numpy(dtype=float)`) prior to tight loops yields a ~27x speedup in isolated helper calls and ~30% total scan time reduction.
**Action:** When iterating over bar indices of a DataFrame in performance-critical paths, always convert the necessary columns into NumPy arrays beforehand.

# Type stub for the compiled Cython module: it mirrors the pure-Python reference
# (rp2040py._execute_batch) exactly, so the types live there (single source of truth) and are re-exported here.
# Lets mypy/IDEs see the native backend's API instead of the ignore_missing_imports fallback.

from rp2040py._execute_batch import execute_batch as execute_batch

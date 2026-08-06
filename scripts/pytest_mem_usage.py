import tracemalloc

tracemalloc.start()

import pytest

pytest.main()

current, peak = tracemalloc.get_traced_memory()
print(f"Current RAM usage: {current / 1024 / 1024:.2f} MB")
print(f"Peak RAM usage: {peak / 1024 / 1024:.2f} MB")

tracemalloc.stop()

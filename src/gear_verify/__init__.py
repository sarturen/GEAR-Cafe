"""GEAR verification mode: real DSL cases against a simulated bench.

Nothing in the Framework, the DSL or the plugins changes. Each plugin's hardware
boundary is replaced by a simulated counterpart, so the same Test Case runs
unchanged and its timing, preconditions and cross-resource expectations can be
checked without a physical bench.
"""

from .bench import SimBench
from .inject import install
from .runner import build_bench, exit_code, run_case
from .seal import sealed

__all__ = [
    "SimBench",
    "build_bench",
    "exit_code",
    "install",
    "run_case",
    "sealed",
]

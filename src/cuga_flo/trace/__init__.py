"""
Unified execution trace — the common event envelope CUGA FLO and its workflow
engines write to one run-scoped JSONL file, read back by `cuga_flo.evaluation`.

Stdlib only (`dataclasses` + `json`). Deliberately dependency-free: the standalone
SimPN Simulation Runtime Service (`cuga_flo/adapters/simpn/runtime/trace.py`) writes
the SAME schema independently, with zero shared code, because it cannot import this
package (see that module's docstring) — the two sides agree on field names, not code.
"""

from cuga_flo.trace.event import TraceEvent
from cuga_flo.trace.writer import TraceWriter, get_writer, trace_path_from_env

__all__ = ["TraceEvent", "TraceWriter", "get_writer", "trace_path_from_env"]

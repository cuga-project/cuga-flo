"""TraceWriter — appends one JSON line per TraceEvent to a run-scoped file.

ponytail: plain POSIX append of small lines, shared with whatever workflow-engine
process is running (e.g. the SimPN Simulation Runtime Service) writing to the same
path at this single-case-at-a-time research volume. Upgrade path: one file per
run_id, or a real queue, once a scenario-runner fires many instances concurrently.
"""

from __future__ import annotations

import json
import os

from cuga_flo.trace.event import TraceEvent

_ENV_VAR = "CUGA_FLO_TRACE_PATH"


def trace_path_from_env() -> str | None:
    """The run-scoped trace file path, if the operator set one.

    Unset by default — trace writing is opt-in, so day-to-day `cuga-flo start` runs
    outside an experiment don't accumulate trace files. Set CUGA_FLO_TRACE_PATH before
    `cuga-flo start <app>` to capture one, then run `cuga-flo evaluate <path>`.
    """
    return os.environ.get(_ENV_VAR)


class TraceWriter:
    def __init__(self, path: str):
        self._path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def emit(self, event: TraceEvent) -> None:
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict()) + "\n")


_singleton: dict[str, TraceWriter] = {}


def get_writer(path: str | None = None) -> TraceWriter | None:
    """Returns the TraceWriter for `path` (or CUGA_FLO_TRACE_PATH), or None if neither
    is set — callers should treat None as "tracing disabled", not an error."""
    path = path or trace_path_from_env()
    if not path:
        return None
    if path not in _singleton:
        _singleton[path] = TraceWriter(path)
    return _singleton[path]

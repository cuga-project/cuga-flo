"""
Unified-trace event emission for the Simulation Runtime Service.

Stdlib only (``json`` + ``time``) — see the package docstring for why this module
doesn't import ``cuga_flo.trace``: that module lives in the main CUGA FLO process,
which this service is not. Both sides write the SAME JSON-line schema (see
``src/cuga_flo/trace/event.py`` for the authoritative field list) to the same
run-scoped file, so the evaluator doesn't care which process wrote which line — only
that the field names agree. That is the entire contract between the two; there is no
shared code, the same way CugaFlo.java and the Python MCP bridge agree on a JSON shape
with zero shared code.
"""

from __future__ import annotations

import json
import time
from typing import Any, TextIO


class TraceSink:
    """Wraps one open file handle; `emit()` appends one JSON line per call.

    ponytail: plain POSIX append of small lines is safe across the two processes
    (this service and CUGA FLO's own) writing to the same path at this single-case-
    at-a-time research volume. Upgrade path: one file per run_id, or a real queue, if
    a later scenario-runner fires many instances concurrently.
    """

    def __init__(self, path: str):
        self._fh: TextIO = open(path, "a", encoding="utf-8")

    def write(self, line: str) -> None:
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def emit(
    sink: TraceSink | None,
    *,
    run_id: str,
    case_id: str,
    source: str,
    event_type: str,
    bpmn_element_id: str,
    payload: dict[str, Any],
) -> None:
    """No-op when `sink` is None, so engine.py's unit tests don't need a real file."""
    if sink is None:
        return
    event = {
        "experiment_id": None,
        "run_id": run_id,
        "application_id": None,
        "scenario_id": None,
        "configuration": None,
        "repetition": None,
        "seed": None,
        "case_id": case_id,
        "timestamp": time.time(),
        "source": source,
        "event_type": event_type,
        "bpmn_element_id": bpmn_element_id,
        "process_state": {},
        "payload": payload,
    }
    sink.write(json.dumps(event))

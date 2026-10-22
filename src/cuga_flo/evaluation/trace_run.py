"""TraceRun — a parsed view over one run's unified-trace JSONL file.

Structural events (CASE_START/ACTIVITY_COMPLETE/GATEWAY_RESOLVED/HOOK_EVALUATED/
CASE_COMPLETE/CASE_HALTED, source="simpn") drive execution_path/cycle-time/
structural metrics — they're engine-agnostic BPS-side events in bpmn_element_id
order. AGENT_* events (source="cuga", emitted by flow_agent.py) drive agent/tool
counts. Both halves of one run land in the same file (see cuga_flo.trace's docstring)
but are read independently here — the evaluator doesn't assume engine-specific
pairing beyond matching run_id.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

_STRUCTURAL_TYPES = {"ACTIVITY_COMPLETE", "GATEWAY_RESOLVED", "HOOK_EVALUATED", "CASE_COMPLETE"}


@dataclass
class TraceRun:
    run_id: str
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: str, run_id: str | None = None) -> TraceRun:
        """Load every event for `run_id` from `path` (or, if `run_id` is None and the
        file holds exactly one run, that run)."""
        events: list[dict[str, Any]] = []
        all_run_ids: set[str] = set()
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                all_run_ids.add(event["run_id"])
                if run_id is None or event["run_id"] == run_id:
                    events.append(event)

        if run_id is None:
            if len(all_run_ids) > 1:
                raise ValueError(
                    f"{path} holds {len(all_run_ids)} runs ({sorted(all_run_ids)}); pass run_id="
                )
            run_id = next(iter(all_run_ids), "")
            events = [e for e in events if e["run_id"] == run_id]

        events.sort(key=lambda e: e["timestamp"])
        return cls(run_id=run_id, events=events)

    def structural_events(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["event_type"] in _STRUCTURAL_TYPES]

    def execution_path(self) -> list[str]:
        return [e["bpmn_element_id"] for e in self.structural_events()]

    def hook_firings(self) -> list[dict[str, Any]]:
        """One entry per hook evaluation, deduplicated by hook id — the simpn-side
        HOOK_EVALUATED event is authoritative; flow_agent.py's AGENT_HOOK_EVALUATED is
        a secondary record of the same decision and is skipped here to avoid
        double-counting the same intervention."""
        return [e for e in self.events if e["event_type"] == "HOOK_EVALUATED"]

    def gateway_resolutions(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["event_type"] == "GATEWAY_RESOLVED"]

    def is_complete(self) -> bool:
        return any(e["event_type"] == "CASE_COMPLETE" and e["source"] == "simpn" for e in self.events)

    def is_halted(self) -> bool:
        return any(e["event_type"] == "CASE_HALTED" for e in self.events)

    def halt_reason(self) -> str | None:
        for e in self.events:
            if e["event_type"] == "CASE_HALTED":
                return e["payload"].get("halt_reason")
        return None

    def cycle_time_seconds(self) -> float | None:
        starts = [e["timestamp"] for e in self.events if e["event_type"] == "CASE_START"]
        ends = [
            e["timestamp"] for e in self.events if e["event_type"] in ("CASE_COMPLETE", "CASE_HALTED")
        ]
        if not starts or not ends:
            return None
        return max(ends) - min(starts)

    def agent_task_count(self) -> int:
        return sum(1 for e in self.events if e["event_type"] == "AGENT_TASK_COMPLETED")

    def agent_gateway_count(self) -> int:
        return sum(1 for e in self.events if e["event_type"] == "AGENT_GATEWAY_ROUTED")

    def agent_hook_count(self) -> int:
        return sum(1 for e in self.events if e["event_type"] == "AGENT_HOOK_EVALUATED")

    def fallback_count(self) -> int:
        """Hook-target resolution fallbacks — see engine.py's _resolve_hook_target.
        A non-zero count means a policy requested an action this engine's (documented,
        bounded) topology couldn't realize as asked."""
        return sum(1 for e in self.events if e["event_type"] == "HOOK_TARGET_FALLBACK")

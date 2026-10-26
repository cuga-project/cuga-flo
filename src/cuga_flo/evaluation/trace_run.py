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

    def initial_variables(self) -> dict[str, Any]:
        """The process input — CASE_START's payload, captured post any `__start__`
        perturbation (see engine.py's run_instance)."""
        for e in self.events:
            if e["event_type"] == "CASE_START":
                return dict(e["payload"].get("process_variables") or {})
        return {}

    def final_variables(self) -> dict[str, Any]:
        """The execution result — CASE_COMPLETE's payload for a normal run,
        CASE_HALTED's for a halted one (e.g. a hook's `terminate`)."""
        for e in self.events:
            if e["event_type"] in ("CASE_COMPLETE", "CASE_HALTED"):
                return dict(e["payload"].get("process_variables") or {})
        return {}

    def cycle_time_seconds(self) -> float | None:
        """Real, measured wall-clock time to run this case — `timestamp` is
        `time.time()` at each event (see trace.py), not SimPN's simulated clock.
        Near-zero for a non-agentic config like G0: SimPN's DecProblem.step()
        advances its internal notion of time logically rather than actually
        sleeping out a task's configured duration. See
        simulated_cycle_time_seconds for the simulated figure instead."""
        starts = [e["timestamp"] for e in self.events if e["event_type"] == "CASE_START"]
        ends = [
            e["timestamp"] for e in self.events if e["event_type"] in ("CASE_COMPLETE", "CASE_HALTED")
        ]
        if not starts or not ends:
            return None
        return max(ends) - min(starts)

    def simulated_cycle_time_seconds(self) -> float | None:
        """The BPS-simulated process duration — the sum of every `ACTIVITY_COMPLETE`
        event's own `duration` (engine.py's make_task_behavior, in simulated
        seconds per simpn_parameters.yaml, not wall-clock). Gateways and hooks are
        instantaneous in this model (no `duration` in their payload), and the
        execution path here is always serial (no true parallel-gateway join — see
        engine.py's module docstring), so a plain sum is correct. None, not 0.0,
        when the run never completed a single activity — the two are different
        facts."""
        durations = [
            e["payload"]["duration"]
            for e in self.events
            if e["event_type"] == "ACTIVITY_COMPLETE" and "duration" in e["payload"]
        ]
        return sum(durations) if durations else None

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

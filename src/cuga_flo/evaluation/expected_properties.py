"""
The `expected_properties` oracle — design doc §6.3, standalone (no `initial_state` /
`perturbations`; those need the scenario DSL this pass doesn't build — see the plan's
Scope section). A YAML file of named predicates, checked against one TraceRun.

Predicate names are a bounded, named vocabulary — not an expression language — the
same discipline already used for HookAction and gateway `flows:`. `_PREDICATES`
covers the handful of domain-independent ones (`case_resolved`); anything else is
checked against the execution path directly, so `must: [Activity_0oydey5]` means
"element Activity_0oydey5 appears in the execution path" with no registration needed.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

from cuga_flo.evaluation.trace_run import TraceRun


class InterventionExpectation(BaseModel):
    required_after: list[str] = Field(default_factory=list)


class ExpectedProperties(BaseModel):
    eventually: list[str] = Field(default_factory=list)
    must: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)
    acceptable_routes: dict[str, float] = Field(default_factory=dict)
    intervention: InterventionExpectation = Field(default_factory=InterventionExpectation)


def _case_resolved(run: TraceRun) -> bool:
    return run.is_complete() or run.is_halted()


_PREDICATES: dict[str, Callable[[TraceRun], bool]] = {
    "case_resolved": _case_resolved,
}


def check_predicate(name: str, run: TraceRun) -> bool:
    """True if `name` holds for `run`. Falls back to "this element id appears in the
    execution path" for any name not in the named registry above."""
    if name in _PREDICATES:
        return _PREDICATES[name](run)
    return name in run.execution_path()


def chosen_flow_id(run: TraceRun) -> str | None:
    """The flow id of this run's (last) gateway resolution, if any."""
    resolutions = run.gateway_resolutions()
    if not resolutions:
        return None
    return resolutions[-1]["payload"].get("chosen_flow")


def hook_intervened(run: TraceRun, hook_id: str) -> bool | None:
    """True/False if hook `hook_id` fired during this run, None if it never fired."""
    for firing in run.hook_firings():
        if firing["bpmn_element_id"] == hook_id:
            return firing["payload"].get("action") != "continue"
    return None


def intervention_counts(run: TraceRun, expected: InterventionExpectation) -> dict[str, int]:
    """TP/FP/FN/TN per design doc §13.7, over every hook that actually fired."""
    required = set(expected.required_after)
    fired = {f["bpmn_element_id"] for f in run.hook_firings()}

    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for hook_id in fired:
        intervened = hook_intervened(run, hook_id)
        required_here = hook_id in required
        if required_here and intervened:
            counts["tp"] += 1
        elif required_here and not intervened:
            counts["fn"] += 1
        elif not required_here and intervened:
            counts["fp"] += 1
        else:
            counts["tn"] += 1
    return counts


def precision_recall_f1(counts: dict[str, int]) -> tuple[float | None, float | None, float | None]:
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall) > 0
        else None
    )
    return precision, recall, f1


def goal_achieved(run: TraceRun, expected: ExpectedProperties) -> bool:
    if any(check_predicate(p, run) for p in expected.must_not):
        return False
    if not all(check_predicate(p, run) for p in expected.must):
        return False
    return all(check_predicate(p, run) for p in expected.eventually)


def decision_utility(run: TraceRun, expected: ExpectedProperties) -> float | None:
    if not expected.acceptable_routes:
        return None
    flow_id = chosen_flow_id(run)
    if flow_id is None:
        return None
    return expected.acceptable_routes.get(flow_id)

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


def chosen_flow_id(run: TraceRun, acceptable_routes: dict[str, float]) -> str | None:
    """
    The flow id of the gateway resolution `acceptable_routes` is actually about.

    A process typically has more than one gateway — e.g. an agentic decision gateway
    plus a plain downstream merge gateway (loan_approval_simpn has exactly this
    shape). Taking "the last gateway resolution in the trace" would pick whichever
    one happens to run last, not the one the oracle cares about — found live, not
    hypothetically, running this very fixture against a real trace. Instead, return
    the last resolution whose chosen flow is actually a key in `acceptable_routes`.
    """
    for resolution in reversed(run.gateway_resolutions()):
        flow_id = resolution["payload"].get("chosen_flow")
        if flow_id in acceptable_routes:
            return flow_id
    return None


def hook_intervened(run: TraceRun, hook_id: str) -> bool | None:
    """True/False if hook `hook_id` fired during this run, None if it never fired."""
    for firing in run.hook_firings():
        if firing["bpmn_element_id"] == hook_id:
            return firing["payload"].get("action") != "continue"
    return None


def intervention_counts(run: TraceRun, expected: InterventionExpectation) -> dict[str, int]:
    """
    TP/FP/FN/TN per design doc §13.7, over `required | fired` — not just `fired`.

    Found live: a hook that was required but never fired *because this G-level's
    config has no hooks at all* (G0/G1/G2 all have zero hooks registered) used to
    contribute nothing — the loop only ever considered hooks that actually fired,
    so "a required intervention never even had the chance to happen" silently
    scored as `None` instead of a false negative. Iterating `required | fired`
    means a required-but-absent hook still counts.
    """
    required = set(expected.required_after)
    fired = {f["bpmn_element_id"] for f in run.hook_firings()}

    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for hook_id in required | fired:
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
    """
    `None` only in the fully vacuous case (nothing required, nothing happened —
    `tp == fp == fn == 0`, no intervention story to evaluate). Otherwise zero
    positive predictions scores 0.0, not `None` — found live: a required
    intervention that never fired at all (G0's config has no hooks — see
    `intervention_counts`) has `tp = fp = 0`, which made precision `None` by the
    0/0-is-undefined convention, and that `None` precision propagated into F1 even
    though recall was unambiguously 0.0 — reporting "no information" about a run
    that in fact completely missed a required intervention.
    """
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    if tp == 0 and fp == 0 and fn == 0:
        return None, None, None
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def goal_achieved(run: TraceRun, expected: ExpectedProperties) -> bool:
    """
    Found live, not hypothetically: a run can satisfy every `must`/`must_not`/
    `eventually` predicate (the process genuinely completed, the credit check ran)
    while still taking a route `acceptable_routes` doesn't recognize at all — e.g.
    G0 rejecting a well-qualified applicant because it has no real scoring
    mechanism. None of those three predicate lists says anything about which
    *route* was taken, so without this check `goal_achieved` would report success
    on exactly the failure the scenario was designed to catch. When
    `acceptable_routes` is declared, the chosen route must be in it (utility > 0)
    for the goal to count as achieved — not just that the process ended cleanly.
    """
    if any(check_predicate(p, run) for p in expected.must_not):
        return False
    if not all(check_predicate(p, run) for p in expected.must):
        return False
    if not all(check_predicate(p, run) for p in expected.eventually):
        return False
    if expected.acceptable_routes:
        utility = decision_utility(run, expected)
        if utility is None or utility <= 0:
            return False
    return True


def decision_utility(run: TraceRun, expected: ExpectedProperties) -> float | None:
    if not expected.acceptable_routes:
        return None
    flow_id = chosen_flow_id(run, expected.acceptable_routes)
    if flow_id is None:
        return None
    return expected.acceptable_routes.get(flow_id)

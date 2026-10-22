"""
The deterministic evaluator — design doc §11. Computes every metric category that
doesn't require a scenario oracle (§11.1 trace-native, §11.2 trace+policy) from the
trace alone, plus the oracle-gated ones (§11.3) when an `expected_properties` file is
given. Anything that needs the (still-deferred) scenario/perturbation layer — chiefly
Perturbation Recovery Rate, and anything comparing multiple runs rather than judging
one — is reported `not_yet_computable` with a reason, never a silent 0/omission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cuga_flo.evaluation.expected_properties import (
    ExpectedProperties,
    decision_utility,
    goal_achieved,
    intervention_counts,
    precision_recall_f1,
)
from cuga_flo.evaluation.trace_run import TraceRun


@dataclass
class Scorecard:
    run_id: str
    # §11.1 trace-native
    process_completed: bool
    process_halted: bool
    halt_reason: str | None
    cycle_time_seconds: float | None
    activity_count: int
    agent_task_invocations: int
    agent_gateway_invocations: int
    agent_hook_invocations: int
    hook_target_fallbacks: int
    # §11.2 trace+policy — see _policy_compliance's docstring for what this does and
    # does not check.
    policy_compliant: bool | None
    # §11.3 trace+scenario-assertion — None/"not_yet_computable" without an oracle.
    goal_achieved: bool | None = None
    decision_utility: float | None = None
    intervention_precision: float | None = None
    intervention_recall: float | None = None
    intervention_f1: float | None = None
    not_yet_computable: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _policy_compliance(run: TraceRun) -> bool | None:
    """
    Whether every hook-target-resolution fallback (engine.py's _resolve_hook_target
    falling back to `continue` because the requested action couldn't be realized
    against this topology) ever occurred. A fallback means the policy asked for
    something the engine's bounded topology silently downgraded — worth flagging as
    non-compliant with what the policy actually asked for, distinct from a genuine
    policy violation. Returns None if no hooks fired at all (nothing to assess).
    """
    if not run.hook_firings():
        return None
    return run.fallback_count() == 0


def evaluate(trace_path: str, expected: ExpectedProperties | None = None, run_id: str | None = None) -> Scorecard:
    run = TraceRun.load(trace_path, run_id=run_id)

    not_yet: dict[str, str] = {
        "perturbation_recovered": "requires perturbation injection (not yet built — see plan Scope)",
    }

    card = Scorecard(
        run_id=run.run_id,
        process_completed=run.is_complete(),
        process_halted=run.is_halted(),
        halt_reason=run.halt_reason(),
        cycle_time_seconds=run.cycle_time_seconds(),
        activity_count=len(run.execution_path()),
        agent_task_invocations=run.agent_task_count(),
        agent_gateway_invocations=run.agent_gateway_count(),
        agent_hook_invocations=run.agent_hook_count(),
        hook_target_fallbacks=run.fallback_count(),
        policy_compliant=_policy_compliance(run),
        not_yet_computable=not_yet,
    )

    if expected is None:
        card.not_yet_computable.update(
            {
                "goal_achieved": "no --expected file given",
                "decision_utility": "no --expected file given",
                "intervention_precision": "no --expected file given",
                "intervention_recall": "no --expected file given",
                "intervention_f1": "no --expected file given",
            }
        )
        return card

    card.goal_achieved = goal_achieved(run, expected)
    card.decision_utility = decision_utility(run, expected)
    counts = intervention_counts(run, expected.intervention)
    card.intervention_precision, card.intervention_recall, card.intervention_f1 = precision_recall_f1(counts)
    return card

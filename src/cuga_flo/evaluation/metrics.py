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
    simulated_cycle_time_seconds: float | None
    total_agent_duration_seconds: float | None
    total_duration_seconds: float | None
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
    # Cost — needs a `rates` dict (the app's own simpn_parameters.yaml content),
    # not an `expected_properties` oracle; see `evaluate()`'s `rates` parameter.
    simulated_cost: float | None = None
    agentic_cost: float | None = None
    total_cost: float | None = None
    not_yet_computable: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _total_duration_seconds(run: TraceRun) -> float | None:
    """simulated_cycle_time_seconds + total_agent_duration_seconds — mirrors
    total_cost's simulated_cost + agentic_cost. simulated_cycle_time_seconds
    alone is the *modeled* BPS duration, deliberately the same regardless of
    which config executes a task/gateway (it's a property of the activity,
    not of who performs it), so it can't by itself show what autonomy costs
    in time — total_agent_duration_seconds is the real wall-clock overhead a
    config's agentic calls add *on top of* that modeled baseline. A config
    with no agentic calls at all (e.g. G0) contributes a confirmed `0.0`
    here, same reasoning as _agentic_cost. None only if there's no completed
    activity to sum at all (simulated_cycle_time_seconds itself is None)."""
    simulated = run.simulated_cycle_time_seconds()
    if simulated is None:
        return None
    return simulated + (run.total_agent_duration_seconds() or 0.0)


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


def _simulated_cost(run: TraceRun, rates: dict) -> float | None:
    """(duration / 3600) * hourly_rate, summed across every ACTIVITY_COMPLETE
    (section "tasks") and GATEWAY_RESOLVED (section "gateways") event that has
    both a traced `duration` and a configured `hourly_rate` for that specific
    element id. None, not 0.0, if not a single element in the run is priced —
    a partial price list still prices whatever it can, consistent with how
    `decision_utility`/`intervention_f1` already handle partial applicability."""
    total = 0.0
    priced_any = False
    for e in run.events:
        if e["event_type"] == "ACTIVITY_COMPLETE":
            section = "tasks"
        elif e["event_type"] == "GATEWAY_RESOLVED":
            section = "gateways"
        else:
            continue
        duration = e["payload"].get("duration")
        if duration is None:
            continue
        rate = (rates.get(section, {}).get(e["bpmn_element_id"], {}) or {}).get("hourly_rate")
        if rate is None:
            continue
        total += (duration / 3600) * rate
        priced_any = True
    return total if priced_any else None


def _agentic_cost(run: TraceRun, rates: dict) -> float | None:
    """num_of_tokens * model_token_cost — one flat rate for the whole run (one
    MODEL_NAME configured per process via .env), not per-task. None only if the
    rate itself is unconfigured. A run with zero agentic calls (e.g. G0) has a
    *known* token count of zero, not a missing one — that's a confirmed $0,
    not a fabricated one, so it's distinguished from "rate unknown" here so
    total_cost stays computable for G0 from simulated_cost alone."""
    model_token_cost = rates.get("model_token_cost")
    if model_token_cost is None:
        return None
    tokens = run.total_agent_tokens()
    return (tokens or 0) * model_token_cost


def evaluate(
    trace_path: str,
    expected: ExpectedProperties | None = None,
    run_id: str | None = None,
    rates: dict | None = None,
) -> Scorecard:
    """`rates` is the app's own `simpn_parameters.yaml` content (not a separate
    schema) — `evaluate()` reads `tasks.<id>.hourly_rate`/`gateways.<id>.
    hourly_rate`/`model_token_cost` out of the same dict the SimPN service
    itself loads. `cuga-flo evaluate --simpn-parameters PATH` loads this file
    for a manual single-trace evaluation; `cuga_flo.scenarios.runner` passes it
    automatically since it already knows the app."""
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
        simulated_cycle_time_seconds=run.simulated_cycle_time_seconds(),
        total_agent_duration_seconds=run.total_agent_duration_seconds(),
        total_duration_seconds=_total_duration_seconds(run),
        activity_count=len(run.execution_path()),
        agent_task_invocations=run.agent_task_count(),
        agent_gateway_invocations=run.agent_gateway_count(),
        agent_hook_invocations=run.agent_hook_count(),
        hook_target_fallbacks=run.fallback_count(),
        policy_compliant=_policy_compliance(run),
        not_yet_computable=not_yet,
    )

    if rates is None:
        card.not_yet_computable.update(
            {
                "simulated_cost": "no simpn_parameters.yaml rates supplied",
                "agentic_cost": "no simpn_parameters.yaml rates supplied",
                "total_cost": "no simpn_parameters.yaml rates supplied",
            }
        )
    else:
        card.simulated_cost = _simulated_cost(run, rates)
        card.agentic_cost = _agentic_cost(run, rates)
        if card.simulated_cost is None:
            card.not_yet_computable["simulated_cost"] = "no hourly_rate configured for any task/gateway in this run"
        if card.agentic_cost is None:
            card.not_yet_computable["agentic_cost"] = "no model_token_cost configured"
        if card.simulated_cost is not None and card.agentic_cost is not None:
            card.total_cost = card.simulated_cost + card.agentic_cost
        else:
            card.not_yet_computable["total_cost"] = "simulated_cost and/or agentic_cost not computable — see those reasons"

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

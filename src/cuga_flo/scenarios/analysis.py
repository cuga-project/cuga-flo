"""
Comparative analysis across a G0-G3 matrix — design doc §16, scoped to descriptive
aggregation and the incremental/ablation interpretation (§16.4), not formal
mixed-effects regression (§16.1-§16.3).

ponytail: real mixed-effects modeling needs (a) enough repetitions per cell for a
GLMM to be statistically meaningful — the design doc itself says "where sample size
permits," and a benchmark run with N=1-3 repetitions per config isn't that — and
(b) a new stats dependency (statsmodels or similar) this project doesn't carry.
Fitting a model on data too thin to support one would manufacture false confidence,
not real statistical insight. What this module gives instead: the per-config
aggregates (Goal Achievement Rate, mean decision utility, mean intervention F1,
mean cycle time) and the G0->G1->G2->G3 deltas a researcher needs to read a
comparison correctly, plus a CSV export — one row per (scenario, config,
repetition) — that is exactly §16's own comparison table and the right input for
fitting a real model externally once there's enough data to justify one.

Upgrade path: add statsmodels and fit a GLMM over the exported CSV once a scenario
suite with real repetition counts exists. The `AutonomyLevel x PerturbationSeverity`
interaction (§16.3) needs a severity dimension the scenario schema doesn't carry
yet either — scenarios here are qualitative, not severity-graded.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from cuga_flo.evaluation.metrics import Scorecard
from cuga_flo.evaluation.trace_run import TraceRun
from cuga_flo.scenarios.runner import trace_path_for

_CONFIG_ORDER = ["G0", "G1", "G2", "G3"]
_ABLATION_PAIRS = [("G0", "G1"), ("G1", "G2"), ("G2", "G3"), ("G0", "G3")]
# not_yet_computable is metadata about the scorecard, not a metric value — excluded
# from the per-run CSV columns.
_SCORECARD_FIELDS = [f.name for f in fields(Scorecard) if f.name != "not_yet_computable"]
_TASK_ELEMENT_TYPES = {"task", "serviceTask", "userTask", "scriptTask", "manualTask"}
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _activity_columns(app_name: str) -> dict[str, str]:
    """element_id -> CSV column name, one per BPMN task element (not gateways,
    events, or hooks) — the fixed universe of "was this activity activated"
    columns, same across every config/scenario for this app since the BPMN model
    itself doesn't change across G0-G3 (only tasks:/gateways:/hooks: do).
    Sanitized from the task's display name for readability; disambiguated by
    appending the element id if two tasks happen to sanitize to the same name."""
    from cuga_flo.engine.bpmn_parser import BPMNParser
    from cuga_flo.engine.flow_config import FlowConfig

    config_path = _REPO_ROOT / "applications" / app_name / "config" / f"{app_name}_config.yaml"
    cfg = FlowConfig.from_yaml(str(config_path))
    bpmn = BPMNParser().parse_file(cfg.get_bpmn_file())

    def _slug(name: str, element_id: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", (name or element_id).strip().lower()).strip("_")
        return slug or element_id.lower()

    columns: dict[str, str] = {}
    for element_id, element in bpmn.elements.items():
        if element.element_type not in _TASK_ELEMENT_TYPES:
            continue
        column = f"activity_{_slug(element.name, element_id)}"
        if column in columns.values():
            column = f"activity_{_slug(element.name, element_id)}__{element_id}"
        columns[element_id] = column
    return columns


def write_run_table(
    results: dict[str, dict[str, list[Scorecard]]], csv_path: str, trace_dir: str, app_name: str
) -> None:
    """One row per (scenario, config, repetition) — design doc §16's own comparison
    table, plus that run's process input and execution result (TraceRun.
    initial_variables/final_variables, re-read from the same trace file
    run_repetition already wrote) flattened into input_<key>/output_<key> columns,
    plus one activity_<name> boolean column per BPMN task element recording
    whether that specific run's execution path reached it at all. `results` is
    keyed scenario_id -> config -> per-repetition scorecards, so a single call
    covers one scenario (results == {scenario.scenario_id: matrix}) or every
    scenario in a folder (run_matrix_many's return value, used as-is) — the CSV
    schema is identical either way, disambiguated by its scenario_id column.
    Column sets can differ across rows (e.g. a rejected-at-intake case never
    reaches the fields a scored one has); missing ones are blank rather than
    shrinking the header to the intersection."""
    activity_columns = _activity_columns(app_name)
    rows: list[dict[str, Any]] = []
    input_keys: set[str] = set()
    output_keys: set[str] = set()
    for scenario_id, config_results in results.items():
        for config, cards in config_results.items():
            for i, card in enumerate(cards):
                run = TraceRun.load(trace_path_for(trace_dir, scenario_id, config, i))
                inputs = run.initial_variables()
                outputs = run.final_variables()
                input_keys.update(inputs)
                output_keys.update(outputs)
                activated = set(run.execution_path())
                row: dict[str, Any] = {"scenario_id": scenario_id, "config": config, "repetition": i}
                row.update({f: getattr(card, f) for f in _SCORECARD_FIELDS})
                row.update({f"input_{k}": v for k, v in inputs.items()})
                row.update({f"output_{k}": v for k, v in outputs.items()})
                row.update({col: (eid in activated) for eid, col in activity_columns.items()})
                rows.append(row)

    fieldnames = (
        ["scenario_id", "config", "repetition"]
        + _SCORECARD_FIELDS
        + [f"input_{k}" for k in sorted(input_keys)]
        + [f"output_{k}" for k in sorted(output_keys)]
        + sorted(activity_columns.values())
    )
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(rows)


def _rate(values: list[bool | None]) -> float | None:
    known = [v for v in values if v is not None]
    return sum(1 for v in known if v) / len(known) if known else None


def _mean(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


@dataclass
class ConfigAggregate:
    config: str
    n: int
    goal_achievement_rate: float | None
    mean_decision_utility: float | None
    mean_intervention_f1: float | None
    mean_cycle_time_seconds: float | None


def aggregate(results: dict[str, list[Scorecard]]) -> list[ConfigAggregate]:
    """Per-config descriptive aggregates, in G0..G3 order (any configs run out of
    that order, or a subset of levels, are still handled — see format_ablation)."""
    aggregates = []
    for config in _CONFIG_ORDER:
        cards = results.get(config)
        if not cards:
            continue
        aggregates.append(
            ConfigAggregate(
                config=config,
                n=len(cards),
                goal_achievement_rate=_rate([c.goal_achieved for c in cards]),
                mean_decision_utility=_mean([c.decision_utility for c in cards]),
                mean_intervention_f1=_mean([c.intervention_f1 for c in cards]),
                mean_cycle_time_seconds=_mean([c.cycle_time_seconds for c in cards]),
            )
        )
    return aggregates


def format_aggregates(aggregates: list[ConfigAggregate]) -> str:
    lines = [f"{'config':<6} {'n':<4} {'GAR':<6} {'mean_util':<10} {'mean_interv_f1':<15} {'mean_cycle_s':<12}"]
    for a in aggregates:
        lines.append(
            f"{a.config:<6} {a.n:<4} {_fmt(a.goal_achievement_rate):<6} {_fmt(a.mean_decision_utility):<10} "
            f"{_fmt(a.mean_intervention_f1):<15} {_fmt(a.mean_cycle_time_seconds):<12}"
        )
    return "\n".join(lines)


def format_ablation(aggregates: list[ConfigAggregate]) -> str:
    """ΔGAR for each G-level step — design doc §16.4's incremental/ablation
    interpretation. Only pairs where both sides actually ran are reported."""
    by_config = {a.config: a for a in aggregates}
    lines = ["Ablation (ΔGAR — Goal Achievement Rate):"]
    any_pair = False
    for a, b in _ABLATION_PAIRS:
        if a in by_config and b in by_config:
            ga, gb = by_config[a].goal_achievement_rate, by_config[b].goal_achievement_rate
            if ga is not None and gb is not None:
                lines.append(f"  {a} -> {b}: {gb - ga:+.2f}")
                any_pair = True
    if not any_pair:
        lines.append("  (need at least two of G0-G3 with a known goal_achievement_rate)")
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "-"

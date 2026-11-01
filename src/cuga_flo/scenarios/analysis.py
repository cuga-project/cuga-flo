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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cuga_flo.evaluation.metrics import Scorecard
from cuga_flo.evaluation.trace_run import TraceRun
from cuga_flo.scenarios.runner import trace_path_for
from cuga_flo.scenarios.schema import Scenario

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


def _pct(value: float | None) -> str:
    return f"{round(value * 100)}%" if value is not None else "N/A"


# Every column/term write_report's tables use, defined once so the report is
# self-contained — a reader shouldn't have to find this source file to know what
# "interv_f1" or "ΔGAR" means.
_GLOSSARY: dict[str, str] = {
    "config": "The autonomy configuration (G0-G3) this row's run used.",
    "rep": "The repetition index within this (scenario, config) cell, 0-based.",
    "goal (goal_achieved)": (
        "Whether this run satisfied every expected property in the scenario's "
        "oracle (`must`/`must_not`/`eventually`/`acceptable_routes`/required "
        "intervention). `-` if the scenario has no oracle."
    ),
    "util (decision_utility)": (
        "The `acceptable_routes` utility (0.0-1.0) of the route a decision "
        "gateway actually chose. `-` if the scenario has no `acceptable_routes` "
        "oracle or no gateway resolution to score."
    ),
    "interv_f1 (intervention_f1)": (
        "F1 score of hook interventions against the scenario's "
        "`intervention.required_after` oracle — did the hooks required to "
        "intervene actually do so, and did any intervene that weren't "
        "required. `-` if there's neither a required nor a fired hook to score."
    ),
    "cycle_s (cycle_time_seconds)": (
        "Real, measured wall-clock seconds to run this case — near-zero for a "
        "non-agentic config, since the simulator advances its own clock "
        "logically rather than sleeping out a task's configured duration. "
        "Distinct from the process's *simulated* duration "
        "(`simulated_cycle_time_seconds`, available via `--csv`/`cuga-flo "
        "evaluate` but not shown in this report)."
    ),
    "n": "Number of repetitions aggregated into this config's row.",
    "GAR (Goal Achievement Rate)": (
        "Fraction of this config's repetitions where `goal_achieved` was `True`."
    ),
    "mean_util": "Mean `decision_utility` across this config's repetitions.",
    "mean_interv_f1": "Mean `intervention_f1` across this config's repetitions.",
    "mean_cycle_s": "Mean wall-clock `cycle_time_seconds` across this config's repetitions.",
    "ΔGAR (ablation)": (
        "The change in Goal Achievement Rate between two autonomy configs "
        "(e.g. `G1 -> G2`), isolating that specific autonomy layer's "
        "incremental contribution."
    ),
    "Scores Matrix cell": (
        "The same Goal Achievement Rate as GAR above, shown as a rounded "
        "percentage, one per (scenario, config) pair."
    ),
}


def write_report(
    app_name: str,
    scenarios: list[Scenario],
    configs: list[str],
    repetitions: int,
    multi_results: dict[str, dict[str, list[Scorecard]]],
    report_path: str,
) -> None:
    """A markdown summary of one run-scenario invocation (single scenario or
    folder mode): which scenarios/configs/repetitions were run, each scenario's
    comparison/aggregate/ablation (same numbers runner.format_comparison/
    format_aggregates/format_ablation print, as markdown tables here), a
    Scenario x Configuration Goal Achievement Rate matrix — the same shape as
    the executive deck's "Scenario Type" x G0-G3 scorecard
    (CUGA_FLO_Prosimos_Executive_Integration_extended.pptx, slide 23), except
    with real scenario ids and real measured results instead of an illustrative
    hypothetical pattern — and a closing "Footnotes" glossary (_GLOSSARY)
    explaining every column/term the tables above use, so the report is
    readable standalone without finding this source file."""
    lines: list[str] = [
        f"# Scenario Run Report — {app_name}",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}Z",
        "",
        "## Configuration",
        "",
        f"- **Scenarios run:** {', '.join(s.scenario_id for s in scenarios)}",
        f"- **Autonomy configs tested:** {', '.join(configs)}",
        f"- **Repetitions per (scenario, config):** {repetitions}",
        "",
        "## Scenarios",
        "",
        "| ID | Description |",
        "|---|---|",
    ]
    for s in scenarios:
        # Collapse the YAML block scalar's internal newlines into one line for
        # the table cell.
        desc = " ".join(s.description.split())
        lines.append(f"| {s.scenario_id} | {desc} |")

    lines += ["", "## Results"]
    for s in scenarios:
        results = multi_results[s.scenario_id]
        aggs = aggregate(results)
        lines += [
            "",
            f"### {s.scenario_id}",
            "",
            "**Per-run comparison**",
            "",
            "| config | rep | goal | util | interv_f1 | cycle_s |",
            "|---|---|---|---|---|---|",
        ]
        for config, cards in results.items():
            for i, card in enumerate(cards):
                cycle = f"{card.cycle_time_seconds:.1f}" if card.cycle_time_seconds is not None else "-"
                lines.append(
                    f"| {config} | {i} | {card.goal_achieved} | {card.decision_utility} | "
                    f"{card.intervention_f1} | {cycle} |"
                )

        lines += [
            "",
            "**Per-config aggregates**",
            "",
            "| config | n | GAR | mean_util | mean_interv_f1 | mean_cycle_s |",
            "|---|---|---|---|---|---|",
        ]
        for a in aggs:
            lines.append(
                f"| {a.config} | {a.n} | {_fmt(a.goal_achievement_rate)} | "
                f"{_fmt(a.mean_decision_utility)} | {_fmt(a.mean_intervention_f1)} | "
                f"{_fmt(a.mean_cycle_time_seconds)} |"
            )

        lines += ["", "**Ablation (ΔGAR — Goal Achievement Rate)**", ""]
        by_config = {a.config: a for a in aggs}
        any_pair = False
        for a_name, b_name in _ABLATION_PAIRS:
            if a_name in by_config and b_name in by_config:
                ga, gb = by_config[a_name].goal_achievement_rate, by_config[b_name].goal_achievement_rate
                if ga is not None and gb is not None:
                    lines.append(f"- {a_name} -> {b_name}: {gb - ga:+.2f}")
                    any_pair = True
        if not any_pair:
            lines.append("- (need at least two of G0-G3 with a known goal_achievement_rate)")

    lines += [
        "",
        "## Scores Matrix — Goal Achievement Rate by Scenario × Configuration",
        "",
        "Same shape as the executive deck's \"Scenario Type\" scorecard "
        "(`CUGA_FLO_Prosimos_Executive_Integration_extended.pptx`, slide 23) — "
        "real scenario ids and real measured results here, not an illustrative "
        "hypothetical.",
        "",
        "| Scenario | " + " | ".join(configs) + " |",
        "|---|" + "---|" * len(configs),
    ]
    for s in scenarios:
        aggs_by_config = {a.config: a for a in aggregate(multi_results[s.scenario_id])}
        row = [s.scenario_id]
        for config in configs:
            agg = aggs_by_config.get(config)
            row.append(_pct(agg.goal_achievement_rate) if agg else "N/A")
        lines.append("| " + " | ".join(row) + " |")

    lines += ["", "## Footnotes — What Each Measurement Means", ""]
    for term, definition in _GLOSSARY.items():
        lines.append(f"- **{term}** — {definition}")
    lines.append("")

    Path(report_path).write_text("\n".join(lines), encoding="utf-8")

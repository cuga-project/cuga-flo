"""`cuga-flo run-scenario` and `cuga-flo author-scenario` entry points."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import yaml

from cuga_flo.scenarios.analysis import aggregate, format_ablation, format_aggregates, write_run_table
from cuga_flo.scenarios.author import author_scenario
from cuga_flo.scenarios.runner import discover_scenarios, format_comparison, load_scenario, run_matrix_many


def run(
    app_name: str,
    scenario_path: str,
    configs: list[str],
    repetitions: int,
    trace_dir: str,
    csv_path: Optional[str] = None,
) -> int:
    """`scenario_path` is either one scenario YAML (the original, still-supported
    form) or a directory of them — run-scenario's folder mode, every `*.yaml`
    under it run as one combined matrix (each config's SimPN service built and
    started once, reused across every scenario — see run_matrix_many). Either
    way the per-scenario comparison/aggregate/ablation tables print once per
    scenario, unchanged; folder mode's only difference is running more than one
    and writing them to a single combined --csv (the scenario_id column already
    disambiguates rows — combining the *ablation* across different scenarios
    wouldn't be meaningful, so that part deliberately stays per-scenario)."""
    if Path(scenario_path).is_dir():
        scenario_paths = discover_scenarios(scenario_path)
        if not scenario_paths:
            print(f"no *.yaml scenario files found under {scenario_path}", file=sys.stderr)
            return 1
    else:
        scenario_paths = [scenario_path]

    multi_results = run_matrix_many(app_name, scenario_paths, configs, repetitions, trace_dir)
    for path in scenario_paths:
        scenario = load_scenario(path)
        results = multi_results[scenario.scenario_id]
        print()
        print(format_comparison(scenario, results))
        print()
        print(format_aggregates(aggregate(results)))
        print()
        print(format_ablation(aggregate(results)))
    if csv_path:
        write_run_table(multi_results, csv_path, trace_dir, app_name)
        print(f"\nPer-run table written to {csv_path}")
    return 0


def author(app_name: str, scenario_id: str, idea: str, out_path: Optional[str]) -> int:
    scenario, warnings = author_scenario(app_name, scenario_id, idea)
    rendered = yaml.safe_dump(scenario.model_dump(), sort_keys=False)
    print(rendered)
    if warnings:
        print("Consistency warnings (review before freezing this scenario):", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
    if out_path:
        Path(out_path).write_text(rendered, encoding="utf-8")
        print(f"\nWritten to {out_path} — review before treating as frozen.", file=sys.stderr)
    return 1 if warnings else 0

"""`cuga-flo run-scenario` and `cuga-flo author-scenario` entry points."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import yaml

from cuga_flo.scenarios.analysis import aggregate, format_ablation, format_aggregates, write_run_table
from cuga_flo.scenarios.author import author_scenario
from cuga_flo.scenarios.runner import format_comparison, load_scenario, run_matrix


def run(
    app_name: str,
    scenario_path: str,
    configs: list[str],
    repetitions: int,
    trace_dir: str,
    csv_path: Optional[str] = None,
) -> int:
    results = run_matrix(app_name, scenario_path, configs, repetitions, trace_dir)
    scenario = load_scenario(scenario_path)
    print(format_comparison(scenario, results))
    print()
    print(format_aggregates(aggregate(results)))
    print()
    print(format_ablation(aggregate(results)))
    if csv_path:
        write_run_table(scenario.scenario_id, results, csv_path, trace_dir)
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

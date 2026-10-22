"""`cuga-flo run-scenario` — runs a frozen scenario across one or more G-level
configs and prints the comparison scorecard."""

from __future__ import annotations

from cuga_flo.scenarios.runner import format_comparison, load_scenario, run_matrix


def run(app_name: str, scenario_path: str, configs: list[str], repetitions: int, trace_dir: str) -> int:
    results = run_matrix(app_name, scenario_path, configs, repetitions, trace_dir)
    scenario = load_scenario(scenario_path)
    print(format_comparison(scenario, results))
    return 0

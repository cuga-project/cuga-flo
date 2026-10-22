"""
The scenario runner — design doc §8: `R = (Application, Scenario, Configuration,
Repetition, Seed)`.

Manages the SimPN Simulation Runtime Service's build/start/stop lifecycle itself
(build_simpn_app.sh, then its generated run.sh) rather than assuming it's already
running — unattended N-repetitions-x-4-configurations is the whole point of a
scenario runner, so the manual three-terminal workflow `CLAUDE.md` documents for a
single interactive run doesn't fit here.

ponytail: one G-level's service is built and started, all of that config's
repetitions run against it, then it's stopped before moving to the next G-level —
sequential, one service at a time, reusing one port. No attempt at parallel
G-level execution; the design doc's own matrix (`S3 x G0 x N`, `S3 x G1 x N`, ...)
doesn't require it, and parallelizing would mean juggling four ports/venvs instead
of one. Upgrade path: run each G-level's service on its own port if wall-clock time
across a full matrix becomes the bottleneck.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from cuga_flo.evaluation.metrics import Scorecard, evaluate
from cuga_flo.scenarios.schema import Scenario

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PORT = 8082


def load_scenario(path: str) -> Scenario:
    with open(path, encoding="utf-8") as fh:
        return Scenario.model_validate(yaml.safe_load(fh))


def build_run_inputs(scenario: Scenario) -> dict[str, Any]:
    """
    The `process_variables` dict passed to `FlowAgent.invoke()` — initial business
    variables, the thin semantic world flattened into `semantic.<key>` process
    variables, and `cugaPerturbations` (the engine-side half's input — see
    `cuga_flo.adapters.simpn.runtime.perturbations`).
    """
    inputs: dict[str, Any] = dict(scenario.initial_state.process_variables)
    for key, value in scenario.initial_state.semantic.items():
        inputs[f"semantic.{key}"] = value

    perturbations: list[dict[str, Any]] = []
    if scenario.initial_state.bps:
        perturbations.append({"when": "__start__", "bps_change": dict(scenario.initial_state.bps)})
    for p in scenario.perturbations:
        perturbations.append(
            {"when": p.when, "bps_change": dict(p.bps_change), "semantic_change": dict(p.semantic_change)}
        )
    inputs["cugaPerturbations"] = json.dumps(perturbations)
    return inputs


@dataclass
class ServiceHandle:
    out_dir: Path
    port: int
    process: subprocess.Popen | None = None

    def build(self, app_name: str, config_path: Path) -> None:
        script = _REPO_ROOT / "scripts" / "build_simpn_app.sh"
        result = subprocess.run(
            [
                "bash", str(script), app_name,
                "--config", str(config_path),
                "--out", str(self.out_dir),
                "--port", str(self.port),
                "--clean",
            ],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"build_simpn_app.sh failed for {config_path}:\n{result.stderr[-4000:]}")

    def start(self, timeout: float = 60.0) -> None:
        self.process = subprocess.Popen(
            [str(self.out_dir / "run.sh")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=1.0)
                if resp.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        self.stop()
        raise RuntimeError(f"SimPN service on port {self.port} did not become healthy within {timeout}s")

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None


def run_repetition(config_path: str, scenario: Scenario, trace_path: str) -> Scorecard:
    """
    Runs one repetition — in its own subprocess, see `_worker.py`'s docstring for
    why — and evaluates its trace.

    `trace_path` is dedicated to this one repetition — simpler than correlating
    multiple runs' events in one shared file by run_id. Both the CUGA FLO process
    and the SimPN service append to this same path (see cuga_flo.trace's module
    docstring), so a stale file from a previous run at the same path is removed
    first — otherwise a rerun would silently accumulate two runs' events into one
    file and TraceRun.load() would refuse to guess which one to evaluate.
    """
    Path(trace_path).unlink(missing_ok=True)
    env = dict(os.environ)
    env["CUGA_FLO_TRACE_PATH"] = trace_path
    result = subprocess.run(
        [sys.executable, "-m", "cuga_flo.scenarios._worker", config_path, json.dumps(build_run_inputs(scenario))],
        env=env,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"scenario repetition failed (config={config_path}):\n{result.stderr[-4000:]}")
    return evaluate(trace_path, expected=scenario.expected_properties)


def run_matrix(
    app_name: str,
    scenario_path: str,
    configs: list[str],
    repetitions: int,
    trace_dir: str,
) -> dict[str, list[Scorecard]]:
    scenario = load_scenario(scenario_path)
    app_dir = _REPO_ROOT / "applications" / app_name
    os.makedirs(trace_dir, exist_ok=True)

    results: dict[str, list[Scorecard]] = {}
    for config in configs:
        config_path = app_dir / "config" / f"{config}_config.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"no {config_path} — expected one config file per G-level")

        handle = ServiceHandle(out_dir=_REPO_ROOT / "build" / "simpn" / f"{app_name}_{config}", port=_DEFAULT_PORT)
        handle.build(app_name, config_path)
        handle.start()
        try:
            scorecards = []
            for rep in range(repetitions):
                trace_path = str(Path(trace_dir) / f"{scenario.scenario_id}__{config}__rep{rep}.jsonl")
                scorecards.append(run_repetition(str(config_path), scenario, trace_path))
            results[config] = scorecards
        finally:
            handle.stop()

    return results


def format_comparison(scenario: Scenario, results: dict[str, list[Scorecard]]) -> str:
    lines = [f"Scenario {scenario.scenario_id}: {scenario.description.strip()}", ""]
    header = f"{'config':<6} {'rep':<4} {'goal':<6} {'util':<6} {'interv_f1':<10} {'cycle_s':<10}"
    lines.append(header)
    lines.append("-" * len(header))
    for config, cards in results.items():
        for i, card in enumerate(cards):
            cycle = f"{card.cycle_time_seconds:.1f}" if card.cycle_time_seconds is not None else "-"
            lines.append(
                f"{config:<6} {i:<4} {str(card.goal_achieved):<6} {str(card.decision_utility):<6} "
                f"{str(card.intervention_f1):<10} {cycle:<10}"
            )
    return "\n".join(lines)

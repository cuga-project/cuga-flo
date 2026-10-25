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

import yaml

from cuga_flo.evaluation.metrics import Scorecard, evaluate
from cuga_flo.scenarios.schema import Scenario

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PORT = 8082


def trace_path_for(trace_dir: str, scenario_id: str, config: str, rep: int) -> str:
    """One dedicated trace file per (scenario, config, repetition) — see
    run_repetition's docstring for why. Shared with analysis.write_run_table, which
    re-reads these same files to pull each run's input/output process variables
    into the CSV."""
    return str(Path(trace_dir) / f"{scenario_id}__{config}__rep{rep}.jsonl")


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
        """Found live: this used to poll GET /health on `self.port` to detect
        readiness. That's fooled by a *race*, not just a clean failure: if
        something else (e.g. a service started manually in another terminal, per
        CLAUDE.md's three-step workflow) already held the port, the health check
        could succeed against THAT other listener before our own (slower —
        cold venv, BPMN parse, DecProblem build) subprocess even reached its own
        bind() call, which then fails moments later with nobody watching anymore.
        `self.process.poll()` alone doesn't close this gap either, since it's
        checked *before* the (already-misleading) health request, not after.

        Fix: wait for OUR OWN subprocess's own startup line
        (`server.serve`'s "[simpn-runtime] serving process ...", redirected into
        `service.log`) instead of hitting the network port at all. That line can
        only be written by a process that itself bound successfully, so there is
        no other listener to be fooled by."""
        # A file, not subprocess.PIPE: a long matrix can make the service log a lot
        # over its lifetime, and nothing here ever drains a PIPE after start()
        # returns — an unread pipe filling up would eventually block the service's
        # own stdout writes and hang the run.
        log_path = self.out_dir / "service.log"
        log_file = open(log_path, "w", encoding="utf-8")
        self.process = subprocess.Popen(
            [str(self.out_dir / "run.sh")],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        log_file.close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            output = log_path.read_text(encoding="utf-8", errors="replace")
            if "[simpn-runtime] serving process" in output:
                return
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"SimPN service subprocess on port {self.port} exited early (code "
                    f"{self.process.returncode}) — port likely already held by another "
                    f"process (e.g. one started manually in another terminal):\n{output[-2000:]}"
                )
            time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"SimPN service on port {self.port} did not start within {timeout}s")

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
                trace_path = trace_path_for(trace_dir, scenario.scenario_id, config, rep)
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

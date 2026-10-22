"""`cuga-flo` command-line entry point."""

from __future__ import annotations

from typing import Optional

import typer

app = typer.Typer(help="CUGA FLO — process harness for policy-aware agent workflows.", no_args_is_help=True)


@app.command()
def start(
    app_name: Optional[str] = typer.Argument(
        None,
        help="Application under applications/ (e.g. loan_approval, loan_approval_kogito). "
        "Defaults to the sole app if there is only one.",
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind (0.0.0.0 for external)."),
    sandbox: bool = typer.Option(False, "--sandbox", help="Enable remote sandbox mode."),
) -> None:
    """Start a FlowAgent application with the CugaSupervisor and the Carbon UI."""
    # ponytail: tolerate the retired `cuga-flo start flow_agent_inline <app>` form
    if app_name == "flow_agent_inline":
        app_name = None
    from cuga_flo.cli.start import run

    run(app_name, host=host, sandbox=sandbox)


@app.command()
def evaluate(
    trace_path: str = typer.Argument(..., help="Unified-trace .jsonl file (set CUGA_FLO_TRACE_PATH before `cuga-flo start` to produce one)."),
    expected: Optional[str] = typer.Option(
        None, "--expected", help="expected_properties YAML — unlocks goal_achieved/decision_utility/intervention_f1."
    ),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Required if the file holds more than one run."),
) -> None:
    """Print the deterministic-evaluator scorecard for one run's unified trace."""
    from cuga_flo.evaluation.cli import run as run_evaluate

    raise typer.Exit(run_evaluate(trace_path, expected, run_id))


@app.command("run-scenario")
def run_scenario(
    app_name: str = typer.Argument(..., help="Application under applications/ (e.g. loan_approval_simpn)."),
    scenario_path: str = typer.Argument(..., help="Frozen scenario YAML, e.g. applications/<app>/scenarios/S01-nominal.yaml."),
    configs: str = typer.Option("G0,G1,G2,G3", "--configs", help="Comma-separated G-levels to run, each needing applications/<app>/config/<level>_config.yaml."),
    repetitions: int = typer.Option(1, "--repetitions", help="Repetitions per config."),
    trace_dir: str = typer.Option("traces", "--trace-dir", help="Directory for per-repetition unified-trace files."),
) -> None:
    """
    Run a frozen scenario across one or more autonomy configurations (design doc §8).

    Builds and starts each config's SimPN Simulation Runtime Service in turn (one at
    a time, stopped before the next), runs the requested repetitions through the
    real FlowAgent, and prints a goal/decision-utility/intervention-F1/cycle-time
    comparison table across configs.
    """
    from cuga_flo.scenarios.cli import run as run_scenario_matrix

    raise typer.Exit(run_scenario_matrix(app_name, scenario_path, configs.split(","), repetitions, trace_dir))


@app.command("patch-host")
def patch_host(
    check: bool = typer.Option(False, "--check", help="Exit non-zero if patches are not applied."),
    revert: bool = typer.Option(False, "--revert", help="Undo the patches."),
) -> None:
    """Stamp (or check / revert) the vendored cuga-agent seams on the installed package."""
    from cuga_flo import _hostpatch

    argv = []
    if check:
        argv.append("--check")
    if revert:
        argv.append("--revert")
    _hostpatch.main(argv)


@app.command()
def doctor() -> None:
    """Report whether cuga-agent is importable and the host patches are in place."""
    import importlib.util

    from cuga_flo import _hostpatch

    if importlib.util.find_spec("cuga") is None:
        typer.echo("cuga-agent: NOT importable — `pip install -e .` should pull it in.", err=True)
        raise typer.Exit(1)
    typer.echo("cuga-agent: importable.")
    raise typer.Exit(0 if _hostpatch.check(verbose=True) else 1)


if __name__ == "__main__":
    app()

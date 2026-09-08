"""`cuga-flo` command-line entry point."""

from __future__ import annotations

from typing import Optional

import typer

app = typer.Typer(help="CUGA FLO — process harness for policy-aware agent workflows.", no_args_is_help=True)


@app.command()
def start(
    service: str = typer.Argument(..., help="Only 'flow_agent_inline' is supported."),
    process_name: Optional[str] = typer.Argument(
        None, help="Application under applications/ (e.g. loan_approval, loan_approval_kogito)."
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind (0.0.0.0 for external)."),
    sandbox: bool = typer.Option(False, "--sandbox", help="Enable remote sandbox mode."),
) -> None:
    """Start an inline FlowAgent application with the CugaSupervisor and the Carbon UI."""
    if service != "flow_agent_inline":
        typer.echo(f"Unknown service '{service}'. Only 'flow_agent_inline' is supported.", err=True)
        raise typer.Exit(1)
    from cuga_flo.cli.start import run

    run(process_name, host=host, sandbox=sandbox)


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

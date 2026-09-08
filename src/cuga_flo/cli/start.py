"""`cuga-flo start flow_agent_inline <app>` — lifted from cuga-agent's
`cuga start flow_agent_inline` block, repointed at `applications/`.

The startup sequence is unchanged: enable the CugaSupervisor, publish a FlowAgent
config into the config store, then start the registry + demo (Carbon UI) via
cuga-agent's AppManager. The supervisor YAML in the app's `config/` compiles the
FlowAgent from its `*_config.yaml` + BPMN.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Optional

import typer
from loguru import logger

APPLICATIONS_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "applications"


def run(process_name: Optional[str], host: str = "127.0.0.1", sandbox: bool = False) -> None:
    # Fail fast if the vendored cuga-agent seams are not stamped onto the install.
    from cuga_flo import _hostpatch

    if not _hostpatch.check(verbose=True):
        raise typer.Exit(1)

    # Imported here so `cuga-flo --help` / `patch-host` don't pull in the whole agent stack.
    from cuga.config import settings
    from cuga.cli.main import (
        _make_app_manager,
        console,
        kill_processes_by_port,
        stop_direct_processes,
        wait_for_direct_processes,
    )
    from cuga.backend.server.managed_mcp import ensure_managed_mcp_file_exists, get_managed_mcp_path
    from cuga.backend.server.config_store import reset_config_db, save_config, save_draft
    from rich.panel import Panel
    from rich.table import Table

    os.environ["DYNACONF_SUPERVISOR__ENABLED"] = "true"

    available = sorted(
        d.name for d in APPLICATIONS_ROOT.iterdir() if d.is_dir() and (d / "config").is_dir()
    )
    if not process_name:
        if len(available) == 1:
            chosen = available[0]
            logger.info(f"Defaulting to the only available application: '{chosen}'")
        else:
            logger.error("Specify an application: cuga-flo start flow_agent_inline <app>")
            logger.error(f"Available: {', '.join(available) if available else '(none found)'}")
            raise typer.Exit(1)
    elif process_name not in available:
        logger.error(f"Application '{process_name}' not found under {APPLICATIONS_ROOT}")
        logger.error(f"Available: {', '.join(available) if available else '(none found)'}")
        raise typer.Exit(1)
    else:
        chosen = process_name

    config_dir = APPLICATIONS_ROOT / chosen / "config"
    supervisor_candidates = sorted(config_dir.glob("supervisor*.yaml"))
    if not supervisor_candidates:
        logger.error(f"No supervisor*.yaml found in {config_dir}")
        raise typer.Exit(1)
    supervisor_config_path = str(supervisor_candidates[0])

    os.environ["DYNACONF_SUPERVISOR__CONFIG_PATH"] = supervisor_config_path
    settings.reload()
    logger.info(f"FlowAgent (inline) supervisor config: {supervisor_config_path}")

    os.environ["CUGA_MANAGER_MODE"] = "true"
    os.environ["DYNACONF_POLICY__FILESYSTEM_SYNC"] = "false"
    os.environ["MCP_SERVERS_FILE"] = "none"
    os.environ["CUGA_AGENT_NAME"] = "FlowAgent"
    os.environ["CUGA_AGENT_DESCRIPTION"] = "BPMN-based workflow orchestration agent (inline)"

    ensure_managed_mcp_file_exists(get_managed_mcp_path())
    reset_config_db()

    llm_api_key_ref = ""
    try:
        from cuga.backend.secrets.seed import resolve_llm_api_key_ref

        llm_api_key_ref = resolve_llm_api_key_ref()
    except Exception:
        pass

    llm_cfg: Any = {"model": os.environ.get("MODEL_NAME", "")}
    if llm_api_key_ref:
        llm_cfg["api_key"] = llm_api_key_ref

    flow_agent_config: Any = {
        "agent": {
            "name": "FlowAgent",
            "description": "BPMN-based workflow orchestration agent (inline)",
        },
        "tools": [],
        "llm": llm_cfg,
        "homescreen": {
            "isOn": True,
            "greeting": f"Welcome to the {chosen.replace('_', ' ').title()} workflow. How can I help you?",
            "starters": ["What can you do for me?"],
        },
        "feature_flags": {"builtin_tools": ["knowledge", "evaluate_condition"]},
    }

    async def _save() -> None:
        await save_draft(flow_agent_config, "cuga-default")
        await save_config(flow_agent_config, "cuga-default")

    asyncio.run(_save())
    logger.info("FlowAgent (inline) config saved (model: %s)", llm_cfg.get("model") or "(default)")

    app_mgr = _make_app_manager()
    kill_processes_by_port([app_mgr.registry_port, settings.server_ports.demo])

    os.environ["CUGA_HOST"] = host
    if sandbox:
        os.environ["DYNACONF_FEATURES__LOCAL_SANDBOX"] = "false"

    registry_process = app_mgr.start_registry(host)
    if registry_process is None or registry_process.poll() is not None:
        logger.error("Registry service failed to start. Exiting.")
        stop_direct_processes()
        raise typer.Exit(1)

    demo_process = app_mgr.start_demo(host, sandbox=sandbox)
    if demo_process is None or demo_process.poll() is not None:
        logger.error("Demo service failed to start. Exiting.")
        stop_direct_processes()
        raise typer.Exit(1)

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Service", style="bold white")
    table.add_column("URL", style="cyan")
    table.add_row("Registry:", f"http://localhost:{app_mgr.registry_port}")
    table.add_row("Demo:", f"http://localhost:{settings.server_ports.demo}")
    console.print()
    console.print(
        Panel(
            table,
            title=f"[bold yellow]CUGA FLO — {chosen} — running. Press Ctrl+C to stop[/bold yellow]",
            border_style="cyan",
            padding=(1, 2),
            expand=False,
        )
    )
    wait_for_direct_processes()

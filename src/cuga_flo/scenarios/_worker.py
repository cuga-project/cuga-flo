"""
One repetition's subprocess entry point: `python -m cuga_flo.scenarios._worker
<config_path> <run_inputs_json>`.

Each repetition runs in its own process rather than looping `asyncio.run()` calls
inside the scenario runner's process. Found necessary, not assumed: running two
`FlowAgent`s back-to-back in one process collides on the MCP callback port —
`MCPFlowBridge._ensure_http_server` guards per-bridge, not per-port, a pre-existing
gap docs/README-KOGITO.md's "Known gaps" already flags for "two FlowAgents in one
process." A scenario runner's repetitions are the first thing in this codebase to
actually trigger it in practice. A fresh process per repetition also means no other
module-level state (ActivityTracker, caches, ...) can leak between repetitions
either — the right isolation for a benchmark runner regardless of the port issue.
"""

from __future__ import annotations

import asyncio
import json
import sys


async def _go(config_path: str, run_inputs: dict) -> None:
    from cuga_flo.engine.flow_config import FlowConfig

    flow_agent = FlowConfig.from_yaml(config_path).to_flow_agent()
    await flow_agent.invoke(input_data=run_inputs)


def main(argv: list[str]) -> int:
    config_path, run_inputs_json = argv
    asyncio.run(_go(config_path, json.loads(run_inputs_json)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

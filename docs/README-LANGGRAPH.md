# CUGA FLO — Integration with LangGraph

The first of three `WorkflowEngine` backends, alongside Flowable and Apache KIE (Kogito) — and
the only one that runs in-process, with no external service. LangGraph owns the compiled
process graph and its execution; CUGA FLO contributes LLM reasoning at each control point
(task, gateway, hook) through the same `MCPFlowBridge` used by the other two engines.

Select it in an app's YAML — or omit `workflow_engine` entirely, since it's the default:

```yaml
workflow_engine:
  type: langgraph
```

---

## How it works

`LangGraphWorkflowEngine` (`langgraph_engine.py`) compiles a `BPMNProcess` into a LangGraph
`StateGraph` once per run:

1. Connects to the bridge over an in-process `FastMCPTransport` — no HTTP/SSE hop, since the
   engine and the FlowAgent share a process. (Swappable for a remote transport without
   changing FlowAgent or engine logic — see [MCP Bridge](../README.md#mcp-bridge).)
2. Fetches the parsed `BPMNProcess` via the bridge's `get_bpmn_process` tool and
   engine-consumable config (task instructions, hook policies, flow conditions, action
   permissions) via `get_flow_annotations`.
3. Builds a `_ControlOverlay` — a private dataclass mapping each annotated element to a
   handler closure: `task_handlers[task_id]`, `gateway_handlers[gateway_id]`, and a single
   `hook_evaluator`. Every handler calls back into the FlowAgent over MCP
   (`execute_task` / `route_gateway` / `evaluate_hook`) rather than executing logic itself —
   `_ControlOverlay` is purely internal LangGraph wiring, not part of the FlowAgent/WorkflowEngine
   contract.
4. `_build_graph` walks the `BPMNProcess` elements into LangGraph nodes, and
   `_add_edges_with_hooks` wires the sequence flows into edges, inserting a hook node on any
   edge that carries one — this is how hooks are materialised for this engine.
5. `_run_graph` runs the compiled graph, streaming a `ControlPointFlowKnowledge` into each node
   and collecting the resulting `FlowState`.

## Structural hook actions: live graph recompile

`REMOVE_NODE` and `ADD_NODE` are the two hook actions LangGraph cannot satisfy by routing
alone — they change the topology mid-run, so the graph has to be rebuilt:

- The triggering hook routes to `END` instead of its normal target.
- `_apply_graph_modification` mutates a deep copy of the `BPMNProcess` and the
  `_ControlOverlay`:
  - **`remove_node`** drops the node's elements/flows and reconnects every predecessor to
    every successor with synthetic `bypass__<from>__to__<to>` flows.
  - **`add_node`** inserts a new `BPMNElement`, redirects every flow that pointed at the node
    it's being inserted before, and registers a dynamic MCP-backed task handler for it.
- `_build_graph` / `_add_edges_with_hooks` run again on the modified model, and execution
  resumes directly at the correct entry point via a conditional `START` edge — `new_node_id`
  for `ADD_NODE`, or the successor of the removed node for `REMOVE_NODE` — never replaying
  nodes that already executed.

Every other hook action (`CONTINUE`, `SKIP_NODE`, `SKIP_TO`, `SWAP_NODES`, `TERMINATE`) is
satisfied by routing within the existing compiled graph — no recompile needed.

---

## Further reading

- [`diagrams/cuga-flo-lifecycle.md`](diagrams/cuga-flo-lifecycle.md) — full startup and
  per-control-point sequence diagram for this engine.
- [`diagrams/cuga-flo-workflow-engines.md`](diagrams/cuga-flo-workflow-engines.md) — how all
  three engines relate to the same MCP bridge.

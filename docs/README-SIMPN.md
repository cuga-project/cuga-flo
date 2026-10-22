# CUGA FLO — Integration with SimPN (standalone Simulation Runtime Service)

A fourth `workflow_engine:` backend, alongside LangGraph (in-process), Flowable, and
Kogito. Unlike Flowable/Kogito, SimPN is not a third-party engine CUGA FLO proxies
to — the service is built **from this repo's own code**
(`src/cuga_flo/adapters/simpn/`) on top of the
[`simpn`](https://github.com/bpogroup/simpn) Petri-net simulation library, and runs as
its own standalone process with its own isolated venv.

Select it in an app's YAML:

```yaml
workflow_engine:
  type: simpn
  url: http://localhost:8082
  process_id: BPMNdiagram       # the BPMN process id, as parsed by BPMNParser
  callback_host: localhost
  callback_port: 8090
```

**The Simulation Runtime Service must already be running.** Nothing here builds or
starts it — same division of responsibility as Flowable/Kogito. See
[App lifecycle](#app-lifecycle) below, or `CLAUDE.md`'s "Running a SimPN app" section
for the three-command version.

---

## Why a standalone service, and why its own venv

The design doc this integration implements originally proposed Prosimos as the BPS
(business-process-simulation) backend. Two things changed before any code was
written:

1. **Prosimos was ruled out architecturally.** Its `execute_full_process` is a single
   tight synchronous loop with no pause/resume point and no behavior-injection API —
   intercepting it for agentic control points would mean monkey-patching internals.
   `simpn.simulator.SimProblem`/`DecProblem` expose `step()` (fires exactly one
   enabled transition) and `add_event`/`add_decision` with a plain-Python-callable
   `behavior` — the intended, public extension point, exactly where a control-point
   callback into CUGA FLO's `FlowAgent` belongs.
2. **`simpn` itself turned out to be a heavy dependency** — its `pyproject.toml`
   declares hard (non-optional) dependencies on PyQt6, pygame, matplotlib and
   python-igraph (its visualization stack), confirmed by actually installing it
   before writing any integration code. None of that belongs in CUGA FLO's own
   process. The Simulation Runtime Service therefore gets its **own venv**, built by
   `scripts/build_simpn_app.sh`, installing only `simpn` + `pyyaml`
   (`src/cuga_flo/adapters/simpn/runtime/requirements.txt`) — never CUGA FLO's own
   (also heavy) dependencies. The service's code still lives inside the `cuga_flo`
   package on disk; the generated `run.sh` points that isolated venv's interpreter at
   it via `PYTHONPATH`, not a real `pip install` of `cuga_flo` — verified safe because
   `runtime/`'s modules and their parent `__init__.py` files import nothing beyond
   `simpn`/`pyyaml`/stdlib.

---

## Components

### `SimPNProxy` — `src/cuga_flo/adapters/simpn/proxy.py`

Sync `httpx` client, same shape as `KogitoProxy`: `ping()`, `start_process(process_id,
variables) -> dict`. `start_process` blocks until the simulated instance completes —
same semantics as Kogito's `POST /{processId}` for an automated process. Config from
`SIMPN_BASE_URL` / `SIMPN_TIMEOUT`, or the app's `workflow_engine.url`.

### The Simulation Runtime Service — `src/cuga_flo/adapters/simpn/runtime/`

- **`engine.py`** — builds one `simpn.simulator.DecProblem` per simulated instance
  from `process_model.json` (a dumped `BPMNProcess`) + `engine_config.json` (a dumped
  `FlowConfig.to_engine_config()`) + `simpn_parameters.yaml` (BPS durations). One
  `SimVar` place per BPMN element; a task/gateway/hook's event consumes its place's
  token and, via its `behavior`, calls the matching MCP tool
  (`execute_task`/`route_gateway`/`evaluate_hook`) when the element is agentic, or
  resolves deterministically (condition eval, fixed duration) when it isn't.
- **`mcp_client.py`** — a stdlib-only (`urllib.request`) synchronous JSON-RPC client
  for the `tools/call` endpoint CUGA FLO's `MCPFlowBridge` exposes. FastMCP's
  stateless HTTP app always answers as SSE (`data:` line), confirmed empirically
  against a real server before writing this; the response's
  `result.structuredContent` is used directly rather than re-parsing `content[0].text`
  as JSON.
- **`server.py`** — the HTTP API, `http.server.ThreadingHTTPServer` (stdlib; no new
  web-framework dependency for two routes — see [HTTP API](#http-api)).
- **`trace.py`** — unified-trace event emission, independent of (but schema-compatible
  with) `cuga_flo.trace` on the main-process side; see [Unified trace](#unified-trace).
- **`__main__.py`** — `python -m cuga_flo.adapters.simpn.runtime <build-dir> [--port N]`.

### `MCPFlowBridge.register_simpn_engine`

Registers `run_process`, starts the shared lazy uvicorn MCP listener, and injects
`cugaProcessKey` + `cugaMcpUrl` + `cugaTracePath` as start variables — the last one
only when `CUGA_FLO_TRACE_PATH` is set. SimPN is held in `_simpn_proxy`, not `_proxy`
— the latter gates the Flowable-only `_realize_hook_action` REST path; like Kogito,
hook-action realization happens entirely inside the service's own token routing.

### HTTP API

Two routes:

```
GET  /health
POST /processes/{process_id}/instances   body: {"variables": {...}}
```

Both ends of this integration are ours, so there's no third-party REST surface to
mirror — unlike `KogitoProxy`/`FlowableProxy`, which match a generated API they don't
control.

---

## G0–G3: the autonomy ladder, mapped onto SimPN

| Level | Mechanism |
|---|---|
| G0 (native) | plain `add_event`; duration-only `behavior`, no MCP call |
| G1 (task autonomy) | a `mode: task_agent` task's `behavior` blocks on `execute_task` |
| G2 (decision autonomy) | a `mode: decision_agent` gateway becomes an `add_decision` — SimPN's native "decision event" concept, which pauses the simulated clock while it resolves, a close structural match |
| G3 (oversight autonomy) | every hooked edge's `behavior` blocks on `evaluate_hook`; see below |

### The full `HookAction` vocabulary, one mechanism

No graph mutation. A hook's event pre-declares **every** admissible outgoing arc (the
normal next element, every other element's place, and a dedicated `__terminated__`
sink) and its `behavior` returns a token on exactly the one arc the resolved action
names, `None` on every other — the same `behavior → list[token | None]` contract
`simpn`'s own gateways and tasks already use, applied generically
(`engine._resolve_hook_target`):

| Action | Resolution |
|---|---|
| `continue` | the normal next element |
| `skip_to` | the named `skip_to_node`, if it's a known element |
| `terminate` | the `__terminated__` sink; `state["is_halted"]` set |
| `skip_node` | the normal next element's own successor (requires it to have exactly one) |
| `remove_node` | same as `skip_node`, only when the named node *is* the normal next one |
| `add_node` | the named node, if it's already modeled (pre-declared, not invented at runtime) |
| `swap_nodes` | whichever of the named pair isn't the normal next element |

Whenever a requested target can't be resolved against this (deliberately simple,
linear) topology, the engine falls back to `continue` and emits a
`HOOK_TARGET_FALLBACK` trace event rather than failing the run — the deterministic
evaluator's `policy_compliant` field reflects whether that ever happened.

---

## Unified trace

Set `CUGA_FLO_TRACE_PATH` before starting CUGA FLO to capture one JSONL file per
session; both the main CUGA FLO process (`cuga_flo.trace`, `source: "cuga"` events —
`AGENT_TASK_COMPLETED`, `AGENT_GATEWAY_ROUTED`, `AGENT_HOOK_EVALUATED`) and the
Simulation Runtime Service (`cuga_flo/adapters/simpn/runtime/trace.py`, `source:
"simpn"` events — `CASE_START`, `ACTIVITY_COMPLETE`, `GATEWAY_RESOLVED`,
`HOOK_EVALUATED`, `CASE_COMPLETE`/`CASE_HALTED`) append to it. The two sides share no
code — they agree on field names, the same way `CugaFlo.java` and the Python MCP
bridge agree on a JSON shape with zero shared code — but both halves of one run land
in the same file, correlated by `run_id`/`case_id`.

```bash
export CUGA_FLO_TRACE_PATH=/tmp/run1.jsonl
cuga-flo start loan_approval_simpn
# ... run a case through the UI ...
cuga-flo evaluate /tmp/run1.jsonl --expected applications/loan_approval_simpn/config/expected_properties/normal.yaml
```

## Deterministic evaluator

`cuga-flo evaluate <trace.jsonl> [--expected <properties.yaml>]` prints a JSON
scorecard. Trace-native and policy/governance metrics (cycle time, activity/agent/
hook counts, `policy_compliant`) are always computed; `goal_achieved`,
`decision_utility`, and `intervention_{precision,recall,f1}` need an
`expected_properties` file (design doc §6.3 — `eventually`/`must`/`must_not`/
`acceptable_routes`/`intervention.required_after`, predicates named either from a
small built-in registry or, by fallback, "this element id appears in the execution
path"). `perturbation_recovered` is always `not_yet_computable`: it needs the
scenario/perturbation layer this integration doesn't build — see the plan's Scope
section for what's deliberately out: the scenario DSL's `initial_state`/
`perturbations`, the semantic-world layer, the scenario-runner CLI, and LLM-assisted
scenario authoring.

---

## App lifecycle

An app is authored entirely under `applications/<app-name>/`:

```
<app-name>/
├── config/
│   ├── <app-name>_config.yaml          workflow_engine.type: simpn
│   ├── BPMNdiagram.bpmn                the same clean model CUGA FLO itself parses
│   ├── simpn_parameters.yaml           BPS durations — this engine's own file
│   └── expected_properties/*.yaml      oracle fixtures, one per case worth asserting on
└── policies/                           engine-independent markdown
```

Then generate and build the service:

```bash
scripts/build_simpn_app.sh <app-name>      # --port N, --out DIR, --clean, --no-build
build/simpn/<app-name>/run.sh              # generated
source .venv/bin/activate && cuga-flo start <app-name>
```

`loan_approval_simpn` is the worked reference — byte-identical `tasks:`/`gateways:`/
`hooks:` and policies to `loan_approval_kogito`, proving the engine type is a drop-in
peer.

---

## Known gaps

- **One `DecProblem` per simulated instance, not shared across concurrently-running
  ones.** This sidesteps modeling resource contention *between* cases (queueing,
  shared capacity) — the real BPS behavior a Prosimos-style engine brings. Upgrade
  path: one shared `DecProblem` per process definition, keyed by case id in the
  resource-queue layer, once multi-case throughput experiments are in scope.
- **Gateways are XOR-choice only.** A true AND-split/AND-join `parallelGateway` has no
  synchronizing join semantics here (each element has exactly one place, so a join
  fires once per arriving token rather than waiting for all branches) — not exercised
  by `loan_approval_simpn`'s merge gateway, which is itself an XOR-join and works
  correctly as-is.
- **`skip_node`/`remove_node` need a single, unambiguous successor.** Both fall back to
  `continue` (traced via `HOOK_TARGET_FALLBACK`) when the normal next element has more
  than one outgoing flow — true of any gateway. `loan_approval_kogito`'s own hooks
  never hit this (they use `continue`/`skip_to` only).
- **`add_node` cannot invent a new element at runtime** — its target must already be
  modeled in the BPMN (a "pre-declared" node, per the design doc's own bounded
  vocabulary framing), not a fresh synthetic task minted on the fly.

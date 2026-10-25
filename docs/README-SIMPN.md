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
- **`formulas.py`** — `load_registry(path)`, a generic loader (stdlib
  `importlib`) for one specific app's own `config/formulas.py` — see the
  "`formula:`" section below for why this is per-app rather than a shared
  registry living here.
- **`__main__.py`** — `python -m cuga_flo.adapters.simpn.runtime <build-dir> [--port N]`;
  also loads `<build-dir>/formulas.py` (if present) via the above.

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
| G0 (native) | plain `add_event`; duration-only `behavior`, no MCP call — optionally a `formula:` lookup (below), still no MCP call |
| G1 (task autonomy) | a `mode: task_agent` task's `behavior` blocks on `execute_task` |
| G2 (decision autonomy) | a `mode: decision_agent` gateway becomes an `add_decision` — SimPN's native "decision event" concept, which pauses the simulated clock while it resolves, a close structural match |
| G3 (oversight autonomy) | every hooked edge's `behavior` blocks on `evaluate_hook`; see below |

### `formula:` — a native task's own built-in computation, no callback at all

Distinct from `tool:` (`TaskConfig.tool`, LangGraph-only today): `tool:` names an
**externally-injected** Python callable — the caller constructing `FlowAgent`
supplies `tools={name: fn}`, and no engine's `FlowConfig.to_flow_agent()` dispatch
actually threads a `tools=` dict through, so there is no pure-YAML path to it for
*any* engine. `formula:` is the opposite: a deterministic computation looked up
by name, with the function never supplied by a caller — but it comes from **the
app's own `formulas.py`**, not from the generic adapter code. For SimPN,
`make_task_behavior`'s native (non-agentic) branch looks up
`process.native_formulas.get(eid)` in `process.formula_registry`, a dict built
at startup by `cuga_flo.adapters.simpn.runtime.formulas.load_registry`, which
dynamically loads `build/<app-name>/formulas.py` — the app's
`config/formulas.py`, staged there by `build_simpn_app.sh` the same way
`simpn_parameters.yaml` is. Deliberately per-app rather than one shared registry
inside `runtime/`: a formula is business logic belonging to one demo app, and a
shared registry would mean every app's formula gets added to code the generic
adapter owns — the same reason the BPMN and policies already live under
`applications/<app>/`, not under the adapter. An app with no native formulas
just has no `config/formulas.py`. An unregistered name doesn't crash the run; it
emits a `FORMULA_NOT_FOUND` trace event and falls through unchanged, the same
fallback-and-trace discipline `_resolve_hook_target` already uses.

This exists because a native task doing *nothing* overstates how badly a real
legacy system performs — the design doc frames G0 as following "conventional
BPMN/BPS behavior," not as a no-op. `loan_approval_simpn`'s
`config/formulas.py` provides `credit_score_formula` (a crude,
keyword-and-arithmetic approximation of `policies/task-credit_check.md`'s
scoring rubric), and `G0_config.yaml` references it via
`formula: "credit_score_formula"`, so G0 can compute a real, if rigid, credit
score instead of leaving `credit_score` at its 0.0 default for every case.
Found live, fixed in the same pass: the first version's keyword check for a bad
payment history matched the substring `"default"` inside the phrase
`"no defaults"`, scoring a genuinely good history as bad — fixed by checking
positive phrases first, with a regression test
(`tests/test_loan_approval_simpn_formulas.py::test_does_not_misread_a_negated_keyword`).

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
path"). `goal_achieved` also fails when `acceptable_routes` is declared and the
actual chosen route isn't in it — found running a real G0 case: a process can
satisfy every `must`/`must_not`/`eventually` predicate while still taking a route
nobody said was acceptable (G0 rejecting a well-qualified applicant for lack of a
real scoring mechanism), and without this check `goal_achieved` would report success
on exactly that failure. `perturbation_recovered` is always `not_yet_computable`: it
needs multiple runs (perturbed vs. nominal) to compare, not just one to judge.

---

## Scenarios and the scenario runner

`cuga_flo.scenarios` builds on the evaluator above to run one frozen scenario across
the G0-G3 autonomy ladder — design doc §5-§8, minus the LLM-assisted authoring and
statistical-analysis layers (still deferred).

### Scenario format — `cuga_flo.scenarios.schema.Scenario`

```yaml
scenario_id: S02
initial_state:
  process_variables: {applicant_id: "4321", loan_amount: 1000, ...}  # CUGA-FLO extension — see below
  semantic: {evidence.contradictory_information: false}               # design doc §4.2/§6.2
  bps: {activities.Activity_0oydey5.duration_multiplier: 1}           # design doc §6.1
perturbations:
  - when: "Activity_0oydey5.enter"                                    # "<element_id>.<enter|complete>"
    bps_change: {activities.Activity_0oydey5.duration_multiplier: 5}
  - when: "Gateway_09ad5fc.complete"
    semantic_change: {evidence.contradictory_information: true}
expected_properties: { ... }   # cuga_flo.evaluation.expected_properties.ExpectedProperties, reused verbatim
```

`initial_state.process_variables` is a CUGA-FLO-specific extension beyond the design
doc's bare `semantic:`-only example: a frozen, reproducible scenario needs to say
*which case* is being run (applicant id, loan amount, ...) without relying on
free-text `_user_message` extraction, which would reintroduce the non-determinism
frozen scenarios exist to remove.

### The "thin semantic world" is just process variables

`initial_state.semantic` and every `semantic_change` land as `semantic.<dotted.key>`
entries in `process_variables` — ordinary variables any task/gateway/hook policy can
already read via `${semantic.evidence.contradictory_information}`-style templating
or plain prose reference, nothing new the engine has to know about. This is why
`engine.py` needed zero semantic-world-specific code.

### Perturbation injection — what `bps_change` can and can't do

Only `activities.<element_id>.duration_multiplier` has a real effect — see
engine.py's `check_point`/`perturbations.py`. Anything else under `bps_change` (e.g.
design doc §6.1's `resources.X.capacity`) is recorded as `unsupported_bps_keys` in a
`PERTURBATION_APPLIED` trace event rather than silently applied or dropped — true
resource-contention modeling needs the shared-`DecProblem` upgrade the "Known gaps"
section below already flags. A synthetic `when: "__start__"` entry (built from
`initial_state.bps`) applies once, unconditionally, before the first token is
seeded; real `perturbations:` entries apply at the matching element's `enter`
(before duration/MCP-call work) or `complete` (after) point, each at most once per
case.

### G0-G3 configs

The runner expects one config file per level, named `G0_config.yaml` ..
`G3_config.yaml` under the app's `config/`, sharing the same BPMN model and
policies — only `tasks:`/`gateways:`/`hooks:` differ. `loan_approval_simpn`'s four
variants are the worked example: G0 strips the task to `mode: native` with
`formula: "credit_score_formula"` (see "`formula:`" above — a real, if crude,
deterministic score, not the 0.0 default a bare native task would otherwise leave
unchanged) and the gateway to a plain condition, G1 adds the Task Agent back, G2
adds the Decision Agent, G3 (content-identical to the app's default
`loan_approval_simpn_config.yaml`) adds the two oversight hooks.

### Running it

```bash
source .venv/bin/activate
cuga-flo run-scenario loan_approval_simpn applications/loan_approval_simpn/scenarios/S02-regulatory-restriction.yaml \
    --configs G0,G1,G2,G3 --repetitions 3 --trace-dir traces/
```

Builds and starts each `G<n>_config.yaml`'s service in turn (`scripts/
build_simpn_app.sh --config ...`), one at a time on the same port, stopped before
the next — unattended N-repetitions-x-4-configs is the point, so this does not
follow the manual three-terminal workflow `CLAUDE.md` documents for a single
interactive run. Prints a `goal`/`util`/`interv_f1`/`cycle_s` comparison table across
configs. `loan_approval_simpn/scenarios/S01-nominal.yaml` and `S02-regulatory-
restriction.yaml` are designed to actually differentiate G-levels (see each file's
own comment for the expected story); `S03-slow-credit-check.yaml` exercises the
perturbation mechanism itself rather than autonomy differentiation.

### LLM-assisted scenario authoring

`cuga-flo author-scenario <app> <scenario_id> --idea "<natural-language idea>" [--out PATH]`
(design doc §5) converts a researcher's plain-English scenario idea into a
structured, schema-validated draft — offline, once, never at benchmark runtime (the
same reasoning that keeps the evaluator itself deterministic). `cuga_flo.scenarios.
author.load_app_context` gives the LLM this app's real element/flow/hook ids and
declared variables (reusing `BPMNParser`/`FlowConfig`, so it never drifts from what
the engine actually sees) plus which variables are a Task Agent's *output* —
`check_consistency` then flags any element/flow/hook id the LLM invented that isn't
real for this app. Nothing is treated as frozen until you review and save the
printed/written draft yourself — the design doc's own "optional researcher review"
step before a scenario is frozen.

Found live, not hypothetically: the first version of this prompt, given only
variable names and defaults, had the LLM invent a FICO-scale `credit_score: 780.0`
for an app whose own task policy defines that variable as 0.0-1.0 — a schema- and
consistency-valid but semantically wrong initial value, because `credit_score` is
normally computed by the Task Agent, not supplied upfront, and nothing had told the
LLM that. Telling it which variables are Task Agent outputs (and to leave them out
of `initial_state` rather than inventing a value) fixed it in the next run.

### Comparative analysis — descriptive, not a fitted statistical model

`cuga_flo.scenarios.analysis` (design doc §16) adds, on top of `run-scenario`'s
comparison table: per-config aggregates (Goal Achievement Rate, mean decision
utility, mean intervention F1, mean cycle time) and the G0→G1→G2→G3 ΔGAR ablation
(§16.4) — always printed. `--csv PATH` writes one row per (config, repetition),
exactly §16's own comparison table, ready to load into pandas/R/statsmodels.

What this deliberately does **not** do is fit a real mixed-effects model (§16.1-
§16.3) or test the `AutonomyLevel × PerturbationSeverity` interaction (§16.3) — see
`analysis.py`'s module docstring: a demo's N=1-3 repetitions per cell isn't enough
to support a GLMM (the design doc itself says "where sample size permits"), fitting
one anyway would manufacture false confidence rather than real statistical
insight, and the severity dimension that interaction needs isn't part of the
scenario schema yet either. The CSV export is the intended handoff point for that
analysis once there's a scenario suite with real repetition counts to justify it.

### Still deferred

Both of design doc §5 and §16 are now implemented in the scoped form described
above. What's left unbuilt: a fitted mixed-effects/GLMM model, a severity dimension
on the scenario schema, and anything that would need those (the `AutonomyLevel ×
PerturbationSeverity` interaction specifically).

---

## App lifecycle

An app is authored entirely under `applications/<app-name>/`:

```
<app-name>/
├── config/
│   ├── <app-name>_config.yaml          workflow_engine.type: simpn — the default, single-run config
│   ├── G0_config.yaml .. G3_config.yaml  autonomy-ladder variants — only the scenario runner uses these
│   ├── BPMNdiagram.bpmn                the same clean model CUGA FLO itself parses
│   ├── simpn_parameters.yaml           BPS durations — this engine's own file
│   ├── formulas.py                     optional — native formula: lookups, this app's own
│   └── expected_properties/*.yaml      oracle fixtures, one per case worth asserting on
├── scenarios/*.yaml                    frozen scenarios — see "Scenarios and the scenario runner" below
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
- **`bps_change`/`initial_state.bps` supports exactly one key shape** —
  `activities.<id>.duration_multiplier` — for the same one-`DecProblem`-per-instance
  reason as the first gap above. Anything else is recorded, not applied; see
  "Perturbation injection" above.
- **One trace file per scenario repetition, not one shared file correlated by
  `run_id`.** `cuga_flo.scenarios.runner.run_repetition` removes any stale file at
  its `trace_path` before each run — simpler than making `TraceRun.load()` pick one
  run out of several sharing a file, at the cost of one trace file per
  `(scenario, config, repetition)` rather than one per scenario run.

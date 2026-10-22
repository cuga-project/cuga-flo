> **Status: implemented and verified** on branch `cuga-flo-sim` (commits `c98fcfd`,
> `77b45c2`). Supersedes `CUGA_FLO_simulation_experimental_setup_v2.md` and
> `CUGA_FLO_Prosimos_Executive_Integration.md` for the engine choice (SimPN, not
> Prosimos) and the infrastructure milestone (adapter + G0-G3 + trace + evaluator);
> those two docs' scenario/semantic-world/statistical-analysis layers remain the
> accurate description of what's still deferred. Verified with two live runs against
> a real LLM (`loan_approval_simpn`, normal applicant and applicant `"4321"`) — see
> `docs/README-SIMPN.md` for the as-built integration guide and known gaps.

# CUGA FLO × SimPN — Simulation Runtime Service (Infrastructure Milestone)

## Context

`.claude/plans/CUGA_FLO_simulation_experimental_setup_v2.md` (and its executive companion `CUGA_FLO_Prosimos_Executive_Integration.md`) lay out a large experimental program: run the same BPMN application under four CUGA FLO autonomy levels (G0 legacy → G1 task autonomy → G2 +decision autonomy → G3 +Flow-Agent oversight), against a BPS (business-process-simulation) backend, with injected scenario perturbations, a unified execution trace, and a deterministic evaluator producing metrics like Goal Achievement Rate and Perturbation Recovery Rate.

Both docs proposed Prosimos as the BPS backend, integrated as a peer to the existing Flowable/Kogito workflow adapters. Two things changed that plan:

1. **Engine choice.** The user wants **SimPN** (bpogroup/simpn, MIT-licensed) instead of Prosimos, for licensing reasons.
2. **Integration shape.** The new engine must be built as a **standalone Simulation Runtime Service** — its own long-running process with an API, architecturally equivalent to the Flowable (Tomcat/Flowable container) and Kogito (Quarkus/Kogito service) integrations — rather than an in-process embedded library (the way LangGraph is wired in today).
3. **Sequencing.** The user wants this treated as an **infrastructure-first** build. The adapter, the standalone service, and the config/dispatch wiring come first — but so do the pieces needed to actually observe and judge what the simulation did: the full G3 `HookAction` vocabulary, the unified execution trace, and the deterministic evaluator (including the `expected_properties` oracle). What's deferred is narrower: the scenario DSL's `initial_state`/`perturbations` sections, the semantic-world layer, perturbation injection, the scenario-runner CLI/comparative matrix, and LLM-assisted scenario authoring/statistical analysis — i.e. the machinery for *manufacturing* experimental conditions, as opposed to *running* and *judging* a process under this engine, which this pass fully builds.

### Why this is feasible (technical grounding)

I read the real source of both candidate engines before committing to a design:

- **Prosimos was ruled out architecturally, independent of licensing.** Its `simulation_engine.py::execute_full_process` is a single tight synchronous Python loop with no pause/resume point and no behavior-injection API — intercepting it for agentic control points would require monkey-patching internals, a high-risk approach.
- **SimPN is a materially better fit.** `simpn.simulator.SimProblem`/`DecProblem` expose:
  - `step()` — fires exactly **one** enabled Petri-net transition/event and returns the binding that fired. This is a native single-step primitive (Prosimos has nothing equivalent).
  - `add_event(inflow, outflow, behavior, ...)` / `add_decision(...)` — `behavior` is a **plain Python callable** invoked synchronously inside `fire()`. This is the public, intended extension point — exactly where a control-point callback into CUGA FLO's FlowAgent belongs, no subclassing or monkey-patching required.
  - `DecProblem` already models the concept of **decision events** that pause the simulation clock while they're resolved — a very close structural match to CUGA FLO's gateway/decision-agent concept (G2).
  - License: MIT (`Copyright (c) 2023 Remco Dijkman, Eindhoven University of Technology`).
  - Caveat: SimPN's own `bpmn_parser.py` is limited (requires per-task `processing_time` properties and lane/role assignments baked into the BPMN XML, and its own TODO says intermediate events/event-based gateways aren't supported). **Plan:** don't use SimPN's BPMN importer at all — keep CUGA FLO's own `bpmn_parser.BPMNProcess` as the single source of truth (consistent with "the BPMN model stays fixed across G0–G3"), and have the new service build the `DecProblem` programmatically from that model plus a small SimPN-specific simulation-parameters file (durations/resources/arrival — the BPS data Prosimos's JSON would have carried).

This means the riskiest assumption in the original doc — "can the BPS engine be paused and resumed at arbitrary control points" — is now a solved problem via `step()` + `behavior` callbacks, not an open research question.

> **As-built note:** a second risk surfaced only once `simpn` was actually installed, not from reading its source: its `pyproject.toml` declares hard (non-optional) dependencies on PyQt6, pygame, matplotlib and python-igraph. The Simulation Runtime Service therefore got its own isolated venv (built by `scripts/build_simpn_app.sh`), never CUGA FLO's main one — see `docs/README-SIMPN.md`.

---

## Architecture

```
                         CUGA FLO (Python process)
                                |
                       MCPFlowBridge (FastMCP, same
                       execute_task/route_gateway/
                       evaluate_hook/complete_process
                       tools Flowable/Kogito already use)
                                |
                         SimPNProxy (httpx client)
                                |  HTTP (start instance, poll)
                                v
              Simulation Runtime Service  (separate process)
              ─────────────────────────────────────────────
              - owns a simpn.simulator.DecProblem per process definition
              - built once from BPMNProcess (CUGA FLO's parser) +
                a simpn-parameters.yaml (durations/resources/arrival)
              - per instance: loops DecProblem.step()
              - when the fired event is a CUGA-FLO-controlled element
                (task_agent task / decision_agent gateway / hook edge),
                its `behavior` callback blocks and calls back into
                CUGA FLO's MCP bridge over HTTP (same JSON-RPC
                tools/call contract CugaFlo.java already uses, but
                Python→Python — no SSE/HTTP-2 workaround needed)
              - returns the terminal state once the instance's
                DecProblem has no more enabled bindings
```

This mirrors Kogito's shape exactly: an out-of-process service that blocks a single "start process" HTTP call until the (simulated) instance completes, calling back into CUGA FLO mid-flight at annotated control points. The only structural difference is that **we own both ends** of this integration (service + proxy), since SimPN is a library we embed ourselves rather than a third-party engine we proxy to.

---

## Scope of this pass

**In scope:**
- The Simulation Runtime Service (SimPN-backed, standalone process, own HTTP API).
- `SimPNProxy` on the CUGA FLO side, wired through the existing `workflow_engine.type` dispatch.
- G0 (native pass-through), G1 (task-agent control points), G2 (decision-agent gateways via SimPN's native `DecProblem`).
- **G3 — the full `HookAction` vocabulary** (`continue`, `skip_node`, `skip_to`, `terminate`, `swap_nodes`, `remove_node`, `add_node`), realized in the Simulation Runtime Service without graph mutation (see design below).
- **The unified execution trace** — a typed JSONL event envelope, written by both the CUGA FLO process and the Simulation Runtime Service, correlated by run/case id.
- **The deterministic evaluator**, including the **`expected_properties` oracle** — a standalone, per-run predicate file (`eventually` / `must` / `must_not` / `acceptable_routes` / `intervention.required_after`), checked against the trace to produce `goal_achieved`, `decision_utility`, and `intervention_{precision,recall,f1}`, on top of the trace-native and policy/governance metrics. This needs no scenario DSL, no perturbation injection, and no semantic-world layer — it's just a predicate file attached to a run, same spirit as the design doc's §6.3 but without §6.1/§6.2's BPS/semantic perturbation machinery.
- One demo application proving the whole path end-to-end, including at least one real hook intervention, an `expected_properties` fixture for each of its cases, and one evaluator run over the resulting trace.
- A build/run lifecycle script mirroring `scripts/build_kogito_app.sh`.

**Explicitly deferred (documented, not built now):**
- The full scenario YAML schema's `initial_state` and `perturbations` sections, and the semantic-world layer — i.e. the machinery that *manufactures* a perturbed run, as opposed to *judging* one, which the oracle above already does.
- Perturbation injection (BPS-parameter and semantic-world changes mid-run). One consequence: **Perturbation Recovery Rate stays `not_yet_computable`** even with the oracle in place — PRR is defined over perturbed-vs-nominal runs, and nothing manufactures a perturbed run yet. Everything else the oracle can judge (goal achievement, decision utility, intervention quality) is computable as soon as a run has an `expected_properties` fixture, perturbed or not.
- The scenario-runner CLI and the G0–G3 comparative experiment matrix (N repetitions × scenarios × configs).
- LLM-assisted scenario authoring and the statistical/mixed-effects analysis layer.

These remain the eventual destination but require the scenario/semantic-world layer this pass doesn't build; they're natural next milestones once this infrastructure is in place.

---

## Implementation

### 1. Config schema and dispatch (small, mechanical changes)

- `src/cuga_flo/engine/app_yaml_schema.py:8` — add `"simpn"` to `WorkflowEngineType`.
- `src/cuga_flo/engine/flow_config.py` — in `FlowConfig.to_flow_agent()` (`flow_config.py:571-629`), add an `elif engine_type == "simpn":` branch alongside the existing `flowable`/`kogito` branches, building a `SimPNProxy` and calling a new `bridge.register_simpn_engine(...)`.
- `src/cuga_flo/mcp/bridge.py` — add `register_simpn_engine(proxy, process_id, callback_port, callback_host)`, modeled directly on `register_kogito_engine` (`bridge.py:215-267`): lazily start the MCP HTTP server, inject `cugaProcessKey`/`cugaMcpUrl` into the start payload, run the (blocking) `proxy.start_process(...)` in an executor thread, return `{}` immediately.

### 2. `SimPNProxy` (CUGA FLO side client)

New file `src/cuga_flo/adapters/simpn/proxy.py`, same shape as `adapters/kogito/proxy.py`: an `httpx`-based REST client with `start_process(process_id, variables) -> dict` (blocks until the simulated instance completes, mirroring Kogito's `start_process`), plus `get_process_instance`/`get_variables` for inspection. No `deploy()` — like Kogito, the process definition is fixed at service-build time from the app's BPMN + sim-parameters file.

### 3. Simulation Runtime Service (new standalone process)

New package `src/cuga_flo/adapters/simpn/runtime/`:

- `engine.py` — builds one `simpn.simulator.DecProblem` per process definition from CUGA FLO's `BPMNProcess` (reuse `src/cuga_flo/engine/bpmn_parser.py` — do not use SimPN's own BPMN importer, per the feasibility note above) plus a new `simpn-parameters.yaml` (per-task duration distribution, resource pools/capacity, arrival — the BPS data that used to live in Prosimos's JSON). Maps:
  - plain tasks → `add_event` with a duration-sampling `behavior`;
  - `mode: task_agent` tasks → `add_event` whose `behavior` blocks on an MCP call (`execute_task`) and feeds the structured result back as output tokens;
  - gateways in `mode: decision_agent` → `add_decision` (SimPN's native decision-event type), whose `behavior` blocks on `route_gateway`;
  - native/condition gateways → plain SimPN choice behavior, matching the `flows:` probabilities/conditions already in the YAML.
- `mcp_client.py` — a small synchronous HTTP JSON-RPC client calling the CUGA FLO MCP bridge's `/mcp` endpoint (`tools/call`), i.e. the Python-to-Python equivalent of `CugaFlo.java`'s `call()` (`adapters/kogito/runtime/CugaFlo.java:187-229`) — much simpler here since there's no SSE/HTTP-2 workaround needed between two Python `httpx` clients.
- `server.py` — the HTTP service itself. **Use the stdlib (`http.server.ThreadingHTTPServer`) rather than adding a new web-framework dependency** — the service only needs `POST /processes/{id}/instances` (start, blocking) and a `GET /health`; `uvicorn`/`httpx` are already project dependencies, and a hand-rolled couple of routes is well within "shortest working diff" territory. *(If the eventual scenario-runner needs richer routes — pause/step introspection, concurrent instances — upgrading to a small ASGI app is a contained follow-up; don't build that now.)*
- `__main__.py` — `python -m cuga_flo.adapters.simpn.runtime <app-dir>` entry point, so `scripts/build_simpn_app.sh`'s generated `run.sh` has something to exec.

### 4. G3 — realizing the full `HookAction` vocabulary in SimPN

No graph mutation is needed. The bounded set of valid targets for any hook is already known statically from config — exactly the way `flow_agent.py`'s `_llm_hook_decision` today lists "remaining unexecuted tasks" as the valid `skip_to` targets, and the way a gateway's `flows:` pre-declares admissible routes. The same idea applies to SimPN events:

- When `engine.py` builds the `DecProblem`, a hook's event is declared with **every admissible outgoing arc**, not just the nominal one: the normal next place, every other task's input place that is a legal `skip_to`/`swap_nodes` target, and a dedicated terminal sink place (`__terminated__`).
- The hook's `behavior` callback blocks on `evaluate_hook` (same MCP call as today), gets back a `HookResult`, and returns a result list aligned with the declared outgoing arcs: a token on exactly the one arc matching the chosen action, `None` everywhere else.
  - `continue` → token on the normal-next arc.
  - `skip_node` / `skip_to` → token on the named target's input-place arc instead.
  - `swap_nodes` → this hook's token goes to the other node's place and vice versa — both sides must be modeled as a hook pair (or one hook holding both tokens briefly), still no shared-graph mutation.
  - `terminate` → token on `__terminated__` only; nothing continues for this case.
  - `remove_node` → functionally identical to `skip_to` the removed node's successor, scoped to this case's token only — other concurrently-simulated cases still visit that node normally, which is correct (Petri-net transitions/places are shared across cases; only the per-case token routing differs).
  - `add_node` → the "extra" corrective step must be a **pre-declared** event with its own input place (e.g. a rework/escalation task already present in the `DecProblem`, just normally unreached); the hook routes this case's token into its input place. This is the one real constraint this design imposes: an `add_node` target must be modeled in advance (from the app's policy/hook config), not invented at runtime — consistent with "a bounded intervention vocabulary," not arbitrary graph editing.
- This keeps per-case routing entirely inside SimPN's existing `behavior → list of tokens-or-None` contract (`simulator.py:1212-1263`), concurrency-safe, with no monkey-patching of `SimProblem`/`DecProblem` internals.

### 5. Unified execution trace

New module `src/cuga_flo/trace/` (stdlib only — `dataclasses` + `json`, no new dependency):
- `event.py` — a `TraceEvent` dataclass matching the common envelope from the design doc (`experiment_id`, `run_id`, `case_id`, `timestamp`, `source: Literal["simpn","cuga","agent","tool","policy"]`, `event_type`, `bpmn_element_id`, `process_state`, `payload`). `scenario_id`/`configuration`/`repetition`/`seed` are present but `Optional` — they stay `None` until the scenario-runner (deferred) populates them.
- `writer.py` — a `TraceWriter` that appends one JSON line per event to a run-scoped `.jsonl` file. Both processes write to the **same file path**, passed to the Simulation Runtime Service at instance-start time (alongside `cugaProcessKey`/`cugaMcpUrl`) the same way Kogito's callback URL is injected today. Plain POSIX append of small lines is safe across two processes at this (single-case-at-a-time, research-scale) volume — *ponytail: this assumes modest concurrent write volume; if the later scenario-runner fires many parallel instances, switch to one file per run_id or a proper queue — not needed yet.*
- Call sites: `flow_agent.py` (`_handle_task`/`_handle_gateway`/`_handle_hook`/`_handle_complete`), `decision_agent.py` (condition eval, routing decision), and `engine.py` in the new runtime service (every `DecProblem.step()` firing — case start/end, activity enable/start/complete, resource allocation, hook/gateway resolution) each emit one `TraceEvent`. This runs **alongside** the existing `ActivityTracker.collect_step` calls, not replacing them — the tracker's free-text trajectory log and the new typed trace serve different consumers.

### 6. Deterministic evaluator, including the `expected_properties` oracle

New module `src/cuga_flo/evaluation/`:
- `expected_properties.py` — a small Pydantic schema for a **standalone** predicate file (no `initial_state`/`perturbations` — those stay deferred), matching design-doc §6.3 verbatim: `eventually: [...]`, `must: [...]`, `must_not: [...]`, `acceptable_routes: {route_id: utility}`, `intervention: {required_after: [...]}`. Named predicates (`case_resolved`, `valid_verification_before_approval`, a hook's id for `required_after`, etc.) are resolved against the trace by a small registry of checker functions, the same "named, bounded vocabulary" discipline already used for `HookAction` and gateway `flows:` — not an arbitrary expression language.
- `metrics.py` — reads one run's `.jsonl` trace plus the app's `BPMNProcess`/`action_permissions`, and an **optional** `expected_properties` file for that run, and computes:
  - Always (trace + BPMN + policies only): process completion, cycle time, activity/agent/tool/retry counts, human-interaction count, policy compliance rate, violation-escape rate, authorized-vs-unauthorized deviation.
  - When an `expected_properties` file is supplied: `goal_achieved` (all `must` hold, no `must_not` holds, all `eventually` eventually hold), `decision_utility` (looks up the actually-taken route in `acceptable_routes`), `intervention_{precision,recall,f1}` (cross-checks each `intervention.required_after` trigger against whether the corresponding hook actually intervened, using the TP/FP/FN/TN table from design-doc §13.7).
  - Always still `not_yet_computable`, with a one-line reason: `perturbation_recovered` (needs perturbation injection to know what "recovered from" means — see Scope) and anything that would require comparing multiple runs (e.g. robustness curves) rather than judging one.
- `cli.py` — a `cuga-flo evaluate <trace.jsonl> [--expected <expected_properties.yaml>]` subcommand (lazy-imported, following the existing `cuga_flo/cli/__init__.py` pattern) prints the computed scorecard.

### 7. New dependency

Add `simpn` (MIT, PyPI `simpn>=1.10`) to `pyproject.toml`. It's a small, pure-Python library — adding it as a core dependency (not an extras group) keeps things simple; flag to reconsider only if it turns out to drag in heavy transitive deps.

> **As-built correction:** this turned out to be wrong — `simpn` is not pure-Python; installing it pulls in PyQt6, pygame, matplotlib, and python-igraph. `simpn` was **not** added to the main `pyproject.toml`. Instead the Simulation Runtime Service gets its own isolated venv via `scripts/build_simpn_app.sh`, installing only `simpn`+`pyyaml` (`src/cuga_flo/adapters/simpn/runtime/requirements.txt`), reached from the generated `run.sh` via `PYTHONPATH` rather than a real `pip install` of `cuga_flo` — see `docs/README-SIMPN.md`.

### 8. Build/run lifecycle script

New `scripts/build_simpn_app.sh <app-name> [--out DIR] [--port N] [--no-build] [--clean]`, modeled on `scripts/build_kogito_app.sh`: validates the app has a `config/simpn-parameters.yaml`, resolves the port the same way, and (since there's no actual compile step — this is pure Python) just stages the app's BPMN + sim-parameters + policies into `build/simpn/<app-name>/` alongside a generated `run.sh` that execs `python -m cuga_flo.adapters.simpn.runtime.__main__ <staged-dir> --port <port>`. Update the root `CLAUDE.md`'s "Running a Kogito app" section with a parallel "Running a SimPN app" section once this works, following the same three-step shape.

### 9. Demo application

New `applications/loan_approval_simpn/`, mirroring `loan_approval_kogito/`: reuse `BPMNdiagram.bpmn` and all of `policies/*.md` verbatim (policies are engine-independent by design, per the existing comment in `loan_approval_kogito_config.yaml`), add `config/loan_approval_simpn_config.yaml` (`workflow_engine.type: simpn`) and a new `config/simpn-parameters.yaml` with one resource pool and a simple fixed/triangular duration for the one task. `loan_approval_kogito`'s existing hooks only use `continue`/`skip_to` (`hook-approval_intercept.md`'s regulatory override) — this app already exercises task_agent (G1), decision_agent (G2), and a real `skip_to` hook (G3) through the real service.

Add two `expected_properties` fixtures under `config/expected_properties/`, one per demo case: a normal applicant (`eventually: [case_resolved]`, `must: [credit_check_before_decision]`, `intervention.required_after: []` — no override expected) and applicant `"4321"` (same `eventually`/`must`, plus `intervention.required_after: [hook-approval_intercept]` — the regulatory override is expected to fire). These give the evaluator a real oracle to check `goal_achieved` and `intervention_f1` against for both runs.

### 10. Tests

New `tests/test_simpn_adapter.py`, following the conventions in `tests/test_remote_agent.py` (docstring-driven rationale, `asyncio.run()` for async paths, a fake MCP target instead of a mock framework, no real network where avoidable):
- one test building a tiny `DecProblem` by hand with a stubbed `behavior` standing in for the MCP call, proving `engine.py`'s task/decision wiring produces the right tokens;
- one test exercising a hook's multi-arc declaration, proving `skip_to`/`terminate` route a case's token to the correct declared arc and leave other cases' routing untouched;
- one test exercising `SimPNProxy.start_process` against a local `ThreadingHTTPServer` instance of the runtime service;
- one test for `src/cuga_flo/evaluation/metrics.py` against a small hand-written trace fixture, asserting both the computable metrics and the explicit `not_yet_computable` markers;
- one test per `expected_properties` checker (`eventually`/`must`/`must_not`/`acceptable_routes`/`intervention.required_after`) against hand-written trace fixtures, including one case where an unauthorized deviation should make `goal_achieved` false and one where a missed required intervention should show up as a false negative in `intervention_recall`.

---

## Verification

1. `uv run ruff check --fix` and `uv run pytest tests/test_simpn_adapter.py`.
2. End-to-end, following the same three-terminal shape as the Kogito workflow in `CLAUDE.md`:
   ```bash
   ./scripts/build_simpn_app.sh loan_approval_simpn
   build/simpn/loan_approval_simpn/run.sh          # terminal 1, left running
   source .venv/bin/activate && cuga-flo start loan_approval_simpn   # terminal 2
   ```
   Open http://127.0.0.1:8001 and run two applicants: a normal one, and applicant id `"4321"` (triggers `hook-approval_intercept.md`'s regulatory override). Confirm: the credit-check task is delegated to the Task Agent (G1), the approve/reject gateway is resolved by the Decision Agent (G2), the `"4321"` case's hook actually fires `skip_to` and visibly diverts the case to the rejection path while the normal case's hook fires `continue` (G3) — and that the end results match `loan_approval_kogito`'s existing behavior for the same two inputs, proving the new engine type is a drop-in peer to Flowable/Kogito, not a special case.
3. Run `cuga-flo evaluate <trace.jsonl> --expected config/expected_properties/normal.yaml` for the normal case and `... --expected config/expected_properties/applicant_4321.yaml` for the override case. Confirm the scorecard reports correct cycle time, activity/agent counts, and policy compliance for both, and now reports real values for `goal_achieved`, `decision_utility`, and `intervention_f1` (both cases should score `goal_achieved = true`, and `intervention_f1` should reflect that the hook stayed quiet for the normal case and correctly intervened for `"4321"`). `perturbation_recovered` stays explicitly `not_yet_computable` — no perturbation injection exists yet, so there is nothing to call "recovered."

> **As-built verification result:** run live against `loan_approval_simpn` with a real
> LLM (not the UI — via `applications/run.py`/a direct `FlowAgent.invoke()` driver, to
> avoid needing a browser). Normal applicant: credit score 0.88, gateway approved,
> both hooks `continue`, `goal_achieved=true`, `decision_utility=1.0`. Applicant
> `"4321"`: credit score 0.86, gateway approved on score alone (correct — regulatory
> restriction isn't the gateway's job), the `Flow_0ybszcv` hook's own LLM correctly
> recognized the restricted id and fired `skip_to` to the rejection task with its
> `state_updates` applied, `goal_achieved=true`, `intervention_f1=1.0`. This live run
> also caught a real bug (`decision_utility` picking the wrong gateway's resolution
> when a process has more than one) and a mismatch in the `applicant_4321` oracle
> fixture itself — both fixed, with a regression test added for the former.

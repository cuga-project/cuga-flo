> **Status: implemented and verified** on branch `cuga-flo-sim`. All four design-doc
> pieces this plan ever scoped are now built, each in a form matched to what the
> available data/dependencies actually justify:
> - **Milestone 1 — infrastructure** (commits `c98fcfd`, `77b45c2`): the adapter,
>   G0-G3, the unified trace, the deterministic evaluator.
> - **Milestone 2 — scenarios, perturbations, the G0-G3 runner** (commits `39be97a`
>   and the schema-relocation `8eae91c`): live-verified across the full G0-G3
>   ladder with real LLM calls.
> - **Milestone 3 — LLM-assisted authoring + comparative analysis** (commit
>   `fc51431`): live-verified; comparative analysis is deliberately descriptive
>   (aggregates + ablation + CSV export), not a fitted mixed-effects model — see
>   that section for exactly why.
> - **Milestone 4 — native `formula:` for a task running with no agent at all**
>   (see the section below): closes a real gap the design doc's own
>   framing of G0 implied but this build never had — a "conventional BPMN/BPS"
>   task with zero deterministic logic, not just zero agentic reasoning. Changes
>   the G0 story for the regulatory-restriction scenario in Milestone 2's
>   verification result — see that section's as-built note and Milestone 4.
> - **Milestone 5 — per-app `formulas.py` instead of a shared adapter registry**
>   (see that section): Milestone 4 shipped `credit_score_formula`
>   inside the generic `src/cuga_flo/adapters/simpn/runtime/` package; moved to
>   `applications/loan_approval_simpn/config/formulas.py`, loaded dynamically by
>   file path, so a second app's formula never requires editing shared adapter
>   code.
> - **Milestone 6 — per-run process input/output/activity in the `--csv`
>   export, and a real port-race bug found verifying it** (see that section):
>   `input_<key>`/`output_<key>` columns, read back from each run's own trace;
>   `activity_<name>` boolean columns, one per BPMN task, read from the app's
>   own BPMN file; and a fix for `ServiceHandle.start()` silently validating
>   against a *different*, already-running SimPN service on the same port
>   instead of its own (just-failed) subprocess — found live, while
>   re-verifying the input/output columns.
> - **Milestone 7 — progress output and a folder mode for `run-scenario`**
>   (see the new section at the end): prints a line per `(config, scenario,
>   repetition)` as it runs instead of staying silent until the final tables;
>   `scenario_path` can now be a directory, running every `*.yaml` under it as
>   one combined matrix (one service build per config, reused across every
>   scenario found — not rebuilt per scenario) with a single combined `--csv`.
>   The single-scenario form is unchanged and still fully supported.
> - **Milestone 8 — a second, BPS-simulated cycle time** (see the new section
>   at the end): `cycle_time_seconds` is real wall-clock time, near-zero for a
>   non-agentic config since SimPN's discrete-event engine never actually
>   sleeps out a task's duration — easy to misread as "this process takes no
>   time." `simulated_cycle_time_seconds` (new `Scorecard` field) is the sum of
>   each activity's own configured duration instead, the figure that actually
>   represents the modeled process's length.
>
> Supersedes `CUGA_FLO_simulation_experimental_setup_v2.md` and
> `CUGA_FLO_Prosimos_Executive_Integration.md` for the engine choice (SimPN, not
> Prosimos) and for everything both docs described. What's left unbuilt now is
> narrower than either doc anticipated: a fitted mixed-effects/GLMM model and a
> severity dimension on the scenario schema (needed for the `AutonomyLevel ×
> PerturbationSeverity` interaction specifically) — see Milestone 3. See
> `docs/README-SIMPN.md` and `docs/running-simulation-experiments.md` for the
> as-built integration guide, usage walkthrough, and known gaps.

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

> **As-built update:** everything in this "deferred" list except the last line
> (LLM-assisted authoring, statistical analysis) was built in Milestone 2 — see the
> new section below. `perturbation_recovered` stays `not_yet_computable` for the
> documented reason even there: it compares a perturbed run against a nominal one,
> and nothing yet designates which runs form such a pair.

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

---

## Milestone 2 — Scenarios, Perturbations, and the G0–G3 Runner

Implements the rest of design doc §5–§8 beyond the oracle Milestone 1 already built:
the scenario DSL's `initial_state`/`perturbations`, the semantic world, G0–G3
autonomy-ladder config variants, and a scenario runner executing N repetitions
across configs. LLM-assisted scenario authoring (§5's natural-language-to-YAML
compiler) and the statistical/mixed-effects analysis layer (§16) remain deferred —
narrower now than Milestone 1 left it.

### The semantic world needed zero engine changes

The design doc frames `W_semantic` (§4.2) as a layer the engine has to support.
In practice it needed none: `initial_state.semantic` and every `semantic_change`
just become `semantic.<dotted.key>` entries in `process_variables` — ordinary data
any task/gateway/hook policy can already read. `cuga_flo.scenarios.schema`'s
`InitialState.semantic`/`Perturbation.semantic_change` are flat dicts (matching the
design doc's own `services.alternative_verification.available: true`-style flat
dotted-key YAML, not nested structure), flattened by
`cuga_flo.scenarios.runner.build_run_inputs` before the run starts.

### Perturbation injection — bounded, not full BPS fidelity

`src/cuga_flo/adapters/simpn/runtime/perturbations.py` (new, stdlib-only — the
runtime service still cannot import `cuga_flo`, see its package docstring) is
checked by `engine.py` at every task/gateway/hook/end-event's enter and complete
point. Only `activities.<element_id>.duration_multiplier` has a real effect,
consistent with Milestone 1's documented one-`DecProblem`-per-instance scope (no
cross-case resource contention to perturb) — anything else under `bps_change`
(design doc §6.1's `resources.X.capacity`, etc.) is recorded as
`unsupported_bps_keys` in a `PERTURBATION_APPLIED` trace event, never silently
applied or dropped. A synthetic `when: "__start__"` entry (built from
`initial_state.bps`) applies once before the first token is seeded; real
`perturbations:` entries apply at the matching element's `enter`/`complete` point,
each at most once per case.

**Wire mechanism:** perturbations ride into the Simulation Runtime Service as one
more process variable — `cugaPerturbations`, JSON-encoded — via the exact mechanism
`cugaMcpUrl`/`cugaProcessKey`/`cugaTracePath` already use. No change to
`MCPFlowBridge`/`SimPNProxy`/the wire format was needed.

### G0–G3 config variants

One config file per level, `G0_config.yaml` .. `G3_config.yaml`, under the app's
`config/`, sharing the same BPMN model and policies — only `tasks:`/`gateways:`/
`hooks:` differ (`loan_approval_simpn`'s four variants are the worked example). G0
strips the task to `mode: native` and the gateway to a plain condition; since no
conventional/deterministic credit-scoring formula exists in this demo app,
`credit_score` never leaves its 0.0 default under G0 — the gateway's
`${credit_score} > 0.6` condition is always false, so **G0 always rejects, for
every applicant, regardless of scenario.** This was designed as the deliberate
experimental point (a conventional workflow with no real scoring mechanism cannot
adapt to who the applicant is), not discovered as a side effect — but its full
consequence (G0 "succeeding" on a regulatory-restriction scenario by accident,
rather than by oversight) only became visible once run live — see below.

> **As-built update (Milestone 4):** "no conventional/deterministic credit-scoring
> formula exists in this demo app" stopped being true — see Milestone 4's `formula:`
> field. G0 no longer always rejects; the regulatory-restriction scenario's result
> below describes G0's *pre-formula* behavior, which Milestone 4 supersedes with a
> cleaner (and arguably more honest) story.

### The scenario runner manages the service lifecycle itself, one repetition per subprocess

`cuga_flo.scenarios.runner` builds and starts each G-level's service in turn
(`scripts/build_simpn_app.sh --config ...`, then its generated `run.sh`), one at a
time on one reused port, stopped before the next — unattended N-repetitions-×-4-
configs is the point, so the manual three-terminal workflow `CLAUDE.md` documents
for a single interactive run doesn't fit.

**Found necessary, not assumed:** each repetition runs in its own subprocess
(`cuga_flo/scenarios/_worker.py`), not a loop of `asyncio.run()` calls in the
runner's own process. Looping `FlowAgent.invoke()` calls collides on the MCP
callback port — `MCPFlowBridge._ensure_http_server` guards per-bridge, not
per-port, a gap `docs/README-KOGITO.md`'s "Known gaps" already flagged for "two
`FlowAgent`s in one process" — and the scenario runner's repetitions are the first
thing in this codebase to actually trigger it in practice. A fresh process per
repetition also means no other module-level state can leak between repetitions
either, which is the right isolation for a benchmark runner regardless of the port
issue specifically.

New `cuga-flo run-scenario <app> <scenario.yaml> --configs G0,G1,G2,G3
--repetitions N` command.

### Two more real bugs found by running this live, not by any mocked test

- **`goal_achieved` ignored route acceptability entirely.** A run could satisfy
  every `must`/`must_not`/`eventually` predicate while taking a route nobody
  declared acceptable — exactly G0 rejecting a well-qualified applicant (scenario
  S01) for lack of a real scoring mechanism. Fixed: when `acceptable_routes` is
  declared, the chosen route must be in it (`decision_utility > 0`) for the goal to
  count as achieved.
- **`intervention_counts` only iterated over hooks that actually fired.** A
  required intervention that never had a hook to fire through *at all* (G0–G2's
  configs register zero hooks) silently contributed nothing instead of a false
  negative — `intervention_recall`/`intervention_f1` came back `None` ("no
  information") on a run that in fact completely missed a required intervention.
  Fixed: iterate `required | fired`, not just `fired`; and `precision_recall_f1`
  now only returns `None` in the fully vacuous case (`tp == fp == fn == 0`) —
  otherwise zero positive predictions scores `0.0`, matching the standard
  precision/recall convention for "no positive predictions were made."

Both fixed with regression tests; both are exactly the kind of gap a live,
multi-config run surfaces that a single-scenario mocked test cannot, because they
only show up when a required intervention's absence has to be scored, not just its
presence.

### Verification result — the full G0–G3 differentiation story, confirmed live

`cuga-flo run-scenario loan_approval_simpn
applications/loan_approval_simpn/scenarios/S02-regulatory-restriction.yaml
--configs G0,G1,G2,G3 --repetitions 1`, real LLM calls throughout:

| Config | goal_achieved | decision_utility | intervention_f1 | cycle_s |
|---|---|---|---|---|
| G0 | `True` | 1.0 | **0.0** | 0.0 |
| G1 | **`False`** | 1.0 | 0.0 | 20.0 |
| G2 | **`False`** | 1.0 | 0.0 | 12.1 |
| G3 | `True` | 1.0 | **1.0** | 17.5 |

Exactly the story this scenario was designed to tell: G0 rejects applicant "4321"
by accident (no real scoring at all — `intervention_f1=0.0` correctly shows no
governance happened); G1/G2 compute a real, correct credit score and — with no
hook to catch the restriction — wrongly grant the loan (`goal_achieved=False`); G3
computes the same real score but its hook's own LLM reasoning catches the
restriction and overrides to rejection (`intervention_f1=1.0` proves the oversight
was genuine, not accidental). G0 and G3 reach the same correct terminal outcome for
entirely different reasons, and the evaluator correctly tells them apart — the
precise "progressively broader autonomy improves governance" claim design doc §10
is built around, now demonstrated end to end rather than asserted.

---

## Milestone 3 — LLM-Assisted Scenario Authoring and Comparative Analysis

Closes the last two items design doc §5 and §16 ever named — the only two Milestone
2 left deferred.

### LLM-assisted scenario authoring (§5) — offline, once, schema+consistency checked

`cuga_flo/scenarios/author.py`, driven by `cuga-flo author-scenario <app> <id>
--idea "<natural language>"`: builds a prompt from this app's **real** element/flow/
hook ids and declared variables (`load_app_context`, reusing `BPMNParser`/
`FlowConfig` — the exact same parsing `scripts/build_simpn_app.sh` already does, so
the LLM's context can never drift from what the engine actually sees), asks a
`CugaAgent` (same `LLMManager`/prompt-JSON-extraction conventions already
established by `flow_agent.py`'s `_llm_hook_decision`/`_extract_initial_variables`
— no new ad hoc LLM-calling pattern) to produce a structured scenario, validates it
against the real `Scenario` Pydantic schema, then runs `check_consistency` — a
second validation pass beyond Pydantic's structural check, confirming every
element/flow/hook id the LLM used is real for this app, not invented. Matches the
design doc's own pipeline exactly: NL idea → LLM → structured scenario → schema/
consistency validation → **optional researcher review** → frozen scenario — the
tool prints (and optionally writes) a draft; nothing is "frozen" until a human
reviews and saves it.

**Found live, not hypothetically, and fixed in the same session:** the first prompt
version gave the LLM only variable *names and defaults* — `credit_score: 0.0` — not
what range that default implies or whether the variable is even a real input. The
LLM invented `credit_score: 780.0` (a FICO-scale value) for an app whose own task
policy defines that field as 0.0-1.0. Schema-valid, consistency-valid (it's a real
variable name), semantically wrong — and the gap was specifically that
`credit_score` is a Task Agent **output**, normally overwritten the moment
`Activity_0oydey5` runs, so there was never a good reason to give it an initial
value at all. Fix: `load_app_context` now also exposes `output_variables` (derived
from every task's `output_mapping`, already present in `cfg.tasks_config` — no new
data needed), and the prompt instructs the LLM to leave those out of
`initial_state` rather than inventing a value. Re-run with the same idea
immediately after: `credit_score` no longer appears in the generated scenario at
all. This is exactly the sequence the design doc's "optional researcher review"
step exists for — a live run surfacing a real gap, fixed, re-verified live again —
not a hypothetical risk noted in passing.

### Comparative analysis (§16) — descriptive aggregation, not a fitted model

`cuga_flo/scenarios/analysis.py`, wired into `run-scenario`'s existing output:
per-config aggregates (Goal Achievement Rate, mean decision utility, mean
intervention F1, mean cycle time) and the G0→G1→G2→G3 ΔGAR ablation (§16.4),
printed automatically; `--csv PATH` writes one row per `(config, repetition)` —
literally §16's own comparison table (`Scenario | Group | Run | Goal | Recovery |
Cycle time | Cost | DU | Intervention correct | Violations | Tokens`, modulo column
names matching the real `Scorecard` fields) — ready to load into pandas/R/
statsmodels.

**Deliberately short of §16.1-§16.3** — a fitted mixed-effects/GLMM model for
binary and continuous/count outcomes, and the `AutonomyLevel × PerturbationSeverity`
interaction test. Two real reasons, not just caution: (1) the design doc itself
qualifies §16.1/§16.2 with "where sample size permits," and a benchmark run with
N=1-3 repetitions per cell — exactly what this tool produces today — isn't that;
fitting a GLMM on data that thin would manufacture false statistical confidence,
not provide real insight. (2) the `PerturbationSeverity` half of §16.3's interaction
needs a severity dimension the scenario schema doesn't carry at all — scenarios
here are qualitative ("a regulatory restriction applies" / "a service is slow"),
not graded. The CSV export is the deliberate handoff point: once a scenario suite
with real repetition counts (and, if wanted, a severity field added to the schema)
exists, fitting a real model over that CSV externally is the right next step — not
something to half-build now on data too thin to support it.

### Verification

- 10 new tests (`tests/test_scenarios.py`): `_extract_json`/`check_consistency`
  unit tests, `load_app_context` against the real `loan_approval_simpn` app (no LLM
  — pure `BPMNParser`/`FlowConfig` parsing), `author_scenario` with a monkeypatched
  LLM call (both a clean case and one surfacing invented ids as warnings), and
  `analysis.py`'s aggregate/ablation/CSV functions against hand-built `Scorecard`s.
  50/50 passing across the full suite.
- Live: `cuga-flo author-scenario loan_approval_simpn S99 --idea "..."` against the
  real demo app with a real LLM call, twice (before and after the
  `output_variables` fix) — see above.
- Live: `cuga-flo run-scenario ... --csv PATH` against a free (no-LLM, G0-only)
  run — comparison table, aggregate table, ablation message, and CSV all verified
  correct, including the CSV's exact column/row shape.

## Milestone 4 — native `formula:` for a task running with no agent at all

### The gap

G0 ("conventional BPMN/BPS, no agentic control") ran every task — including
`loan_approval_simpn`'s credit-check — as a pure SimPN native task: duration
sampling only, no scoring logic at all. `credit_score` simply stayed at its
Pydantic default (`0.0`) for the rest of the case. This happened to make every
G0 run reject every applicant, which accidentally "passed" some scenarios
(e.g. the original S02 regulatory-restriction scenario, where G0 rejected
"4321" — not because it caught the regulatory issue, but because it rejects
everyone). That's a false success: it collapses `goal_achieved` and
`intervention_f1` into agreeing for the wrong reason, and it understates what
G0 actually is — "no agentic *control*," not "no deterministic *logic*." A
conventional workflow engine's native tasks routinely run real business
logic (a rate table, a scoring formula, a tax calculation) with zero agent
involvement; the build had no way to express that.

### Why not reuse `tool:`?

The user asked directly: "why would the config mention the inner
functionality of a native task as 'tool', isn't its inner logic implemented
in python (i.e., formulas.py) as part of the simpn config?" Reusing `tool:`
would conflate two different things:

- `tool:` (`TaskConfig.tool`) already means an **externally-injected**
  Python callable — the caller building `FlowAgent` passes `tools={name: fn}`
  at construction time. Nothing in the YAML itself supplies the function.
  (In fact no engine's `FlowConfig.to_flow_agent()` threads a `tools=` dict
  through today, so this field is currently unreachable from pure YAML for
  *any* engine — a pre-existing gap this milestone didn't touch.)
- What G0's credit-check task actually needs is the opposite: a computation
  **built into the engine's own code**, looked up by name, that the YAML
  never supplies — the SimPN runtime service ships `formulas.py` the same
  way it ships `engine.py` itself.

Overloading `tool:` to mean both would make the same config key mean
different things depending on which engine reads it, breaking the
config-portability goal the schema is meant to preserve. So this milestone
adds a second, clearly-scoped field instead: `formula: Optional[str] = None`
on `TaskConfig` (`src/cuga_flo/schemas/app_yaml_schema.py`), with a docstring
contract spelling out the distinction so neither field's meaning drifts later.

### Implementation

- `src/cuga_flo/schemas/app_yaml_schema.py` — `TaskConfig.formula`, documented
  against `TaskConfig.tool` as described above.
- `src/cuga_flo/engine/flow_config.py` — `to_engine_config()` extracts a
  `native_formulas: {task_id: formula_name}` map, populated only for tasks
  that aren't agentic (mirrors how `tool_tasks` is already extracted in the
  same branch).
> **As-built update (Milestone 5):** the shared registry this next bullet
> describes moved out of the generic adapter package into the app's own
> `config/formulas.py` — see Milestone 5. The formula's content below is
> unchanged; only *where the function lives* changed.

- `src/cuga_flo/adapters/simpn/runtime/formulas.py` (now relocated — see the
  as-built note above) — a small `REGISTRY: dict[str, Callable[[dict], dict]]`,
  today holding one entry: `credit_score_formula`, a deterministic approximation of
  `policies/task-credit_check.md`'s rubric (payment history 35%,
  debt-to-income 30%, length of credit history 15%, credit mix 10%, recent
  enquiries 10%; identity-unverified and no-credit-history edge cases;
  debt-to-income weighted 10pp higher above a $50k loan). `credit_mix` and
  `recent_enquiries` have no structured input in this demo, so both are
  fixed at `0.5` — calibrated against what the G1/G2 LLM itself typically
  assigns when a factor goes unmentioned in a profile, observed in earlier
  live transcripts.
- `src/cuga_flo/adapters/simpn/runtime/engine.py` — `make_task_behavior()`
  now checks, in order: is this task agentic (G1+, existing MCP-callback
  path) → else does it have a `formula:` (new: look up `REGISTRY`, apply it
  to `process_variables`, trace `formula_applied`) → else plain native
  duration-only behavior, unchanged. An unknown formula name traces
  `FORMULA_NOT_FOUND` rather than crashing the simulation.
- `applications/loan_approval_simpn/config/G0_config.yaml` — the credit-check
  task gets `formula: "credit_score_formula"`; the file's header comment no
  longer claims G0 always rejects.

### A real bug the live check caught

A manual validation script compared the formula's score for S01's qualifying
applicant ("Excellent payment history, no defaults") against a deliberately
bad test case ("Multiple missed payments and a default") and got the *same*
depressed score for both. Cause: `"no defaults"` contains the substring
`"default"`, so a negative-keyword-only check (`"default" in text.lower()`)
misread the good history as bad. Fixed by checking a `positive_phrases` list
(`"excellent"`, `"good standing"`, `"strong"`, `"clean record"`, `"no
default"`, `"no late"`, `"no missed"`) **first**, with priority over the
negative-keyword check — only falling through to the negative check when no
positive phrase matches. Regression test:
`test_credit_score_formula_does_not_misread_a_negated_keyword`.

### Live verification

- S01 (qualifying applicant) under G0: `credit_score` now computes to `0.84`
  (previously stuck at `0.0`) → `goal_achieved` flips `False → True`.
- A deliberately bad profile: `0.57`, correctly below S01's.
- Identity-unverified and no-credit-history edge cases: exact match to the
  policy's stated defaults (`0.0`+`rejection_reason`, `0.3`+`no_history`).
- A >$50k loan: measurably stricter (debt-to-income weighted higher), as the
  policy specifies.
- **This changes S02's story**, and the live run confirmed it isn't cosmetic:
  under G0, applicant "4321" now also scores well on the formula (same
  ~0.8-range result the regulatory restriction has no notion of) and gets
  wrongly approved — joining G1/G2 instead of "succeeding by accident." G0,
  G1, and G2 now fail S02 identically (no oversight hook in any of them);
  only G3 catches it. This is a cleaner, more honest 3-vs-1 differentiation
  than before, and the four places that described the old behavior were
  updated to match: `S02-regulatory-restriction.yaml`'s header/description,
  `docs/running-simulation-experiments.md` §7's worked table/aggregates/
  ablation/interpretation, `docs/README-SIMPN.md`'s G0-G3 mechanism section,
  and the as-built note added inline in this plan's Milestone 2 section
  above — rather than silently rewriting history, the old claim is left in
  place with a blockquote pointing here.

### Verification (tests)

- `tests/test_simpn_adapter.py`: `test_native_task_with_a_formula_computes_without_any_mcp_call`,
  `test_unknown_formula_name_is_traced_not_crashed` (engine-level), plus four
  pure-function tests on `credit_score_formula` itself — a well-qualified
  applicant, the negated-keyword regression, the policy's literal edge cases,
  and the >$50k stricter-weighting case. 56/56 passing across the full suite.
- `docs/running-simulation-experiments.md` and `docs/README-SIMPN.md` updated
  to document the `formula:` field and its effect on the G0 story.

## Milestone 5 — per-app `formulas.py` instead of a shared adapter registry

### The gap

Milestone 4 shipped `credit_score_formula` and its `REGISTRY` inside
`src/cuga_flo/adapters/simpn/runtime/formulas.py` — the generic SimPN adapter
package, alongside `engine.py`, `server.py`, `mcp_client.py`, none of which
mention "credit" or "loan" anywhere. The user asked directly: wouldn't it make
more sense for each app to own its own `formulas.py`, the same way it already
owns `simpn_parameters.yaml`? It would — `build_simpn_app.sh` already stages
`simpn_parameters.yaml`, the BPMN, and the policies per-app into
`build/simpn/<app-name>/`; `formulas.py` living centrally instead broke that
pattern and meant a second app's formula would require editing shared adapter
code and growing one `REGISTRY` dict for every app that ever uses SimPN —
exactly the coupling the per-app config split exists to avoid.

### Implementation

- `src/cuga_flo/adapters/simpn/runtime/formulas.py` — stripped down to a
  single generic function, `load_registry(path: Path) -> dict[str, Callable]`:
  dynamically loads a module from a file path (stdlib `importlib.util`) and
  returns its `REGISTRY`, or `{}` if the file doesn't exist. No app-specific
  content remains in this package.
- `applications/loan_approval_simpn/config/formulas.py` (new) — the actual
  `credit_score_formula` + `REGISTRY`, moved verbatim from the old shared file,
  now owned by the app alongside its BPMN/policies/`simpn_parameters.yaml`.
- `src/cuga_flo/adapters/simpn/runtime/engine.py` — `ProcessModel.__init__`
  gains `formula_registry: dict | None = None`, stored as
  `self.formula_registry`; `make_task_behavior()`'s formula branch now looks up
  `process.formula_registry.get(formula_name)` instead of a module-level
  `formulas.REGISTRY`.
- `src/cuga_flo/adapters/simpn/runtime/__main__.py` — loads
  `build_dir / "formulas.py"` via `formulas.load_registry(...)` and passes the
  result into `ProcessModel(..., formula_registry=...)`.
- `scripts/build_simpn_app.sh` — copies `$APP_DIR/config/formulas.py` into
  `$OUT_DIR/formulas.py` if the app has one (optional — most apps won't).

### Verification

- `tests/test_simpn_adapter.py`'s formula-wiring tests now inject a trivial
  stub `formula_registry` directly rather than relying on any real formula
  content — the generic adapter's tests no longer need to know any specific
  app's business logic. Two new tests cover `load_registry` itself: loading a
  real file by path, and the missing-file → `{}` case.
- The four `credit_score_formula`-specific tests moved out of
  `test_simpn_adapter.py` into a new `tests/test_loan_approval_simpn_formulas.py`,
  which loads the registry the real way (`load_registry` against
  `applications/loan_approval_simpn/config/formulas.py`, not a plain import) —
  proving the real loading path end-to-end, not just the formula's own logic.
  59/59 passing across the full suite.
- Live: rebuilt `build/simpn/loan_approval_simpn/` from scratch and confirmed
  `formulas.py` was staged into the build output; started the service and
  confirmed it came up healthy (it would have crashed at `load_registry` if
  the staged file were broken); ran S01-nominal and S02-regulatory-restriction
  under G0 via `cuga-flo run-scenario` and got the identical `goal`/`util`/
  `intervention_f1` results as before this refactor (`True`/`1.0`/`None` and
  `False`/`1.0`/`0.0` respectively) — confirming the relocation changed nothing
  observable about the formula's behavior, only where its code lives.

## Milestone 6 — per-run process input/output/activity in `--csv`, and a port-race bug found verifying it

### The gap

The `--csv` export (Milestone 3) wrote every `Scorecard` field — whether a run
succeeded, its cycle time, its intervention F1 — but nothing about *what the
run actually did*: which applicant, what credit score it computed, what it
decided, and which of the process's tasks actually fired. A researcher
loading the CSV into pandas couldn't connect a `goal_achieved=False` row back
to the applicant profile that produced it, or see *why* it failed (e.g. the
loan was actually granted) without separately re-opening that run's trace
file by hand.

### Implementation

- `src/cuga_flo/adapters/simpn/runtime/engine.py` — `CASE_START`'s trace
  payload now carries `process_variables` (the process input, post any
  `__start__` perturbation); `CASE_COMPLETE`'s and `CASE_HALTED`'s both carry
  the final `process_variables` (the execution result — a halted case's own
  state, same as a completed one's, just via the other terminal event).
- `src/cuga_flo/evaluation/trace_run.py` — `TraceRun.initial_variables()`
  (reads `CASE_START`'s payload) and `TraceRun.final_variables()` (reads
  `CASE_COMPLETE`'s, falling back to `CASE_HALTED`'s).
- `src/cuga_flo/scenarios/runner.py` — factored the per-repetition trace path
  naming (`{scenario_id}__{config}__rep{rep}.jsonl`) into a shared
  `trace_path_for(...)` function, so `analysis.py` can locate and re-read the
  same files `run_repetition` already wrote.
- `src/cuga_flo/scenarios/analysis.py` — `write_run_table` now takes
  `trace_dir` and `app_name`, re-loads each run's `TraceRun`, and flattens
  `initial_variables()`/`final_variables()` into `input_<key>`/`output_<key>`
  columns via `csv.DictWriter` with a union-of-keys header (different
  configs/scenarios can reach different variables — e.g. identity-unverified
  vs. a fully-scored case — so the header is the union across all rows, and a
  row missing a key gets a blank cell rather than shrinking the table).
- `src/cuga_flo/scenarios/analysis.py`'s new `_activity_columns(app_name)` —
  loads the app's BPMN directly (`BPMNParser`/`FlowConfig`, the same way
  `build_simpn_app.sh`'s subprocess does), filters to task elements only
  (`task`/`serviceTask`/`userTask`/`scriptTask`/`manualTask` — not gateways,
  events, or hooks), and maps each one to a readable `activity_<slugified
  name>` column, disambiguated by appending the element id if two tasks
  happen to share a sanitized name. Unlike the `input_`/`output_` columns,
  this set is fixed per app — the BPMN model doesn't change across G0-G3 — so
  it's read once from the BPMN file, not from any individual run's trace.
  `write_run_table` sets each column to `element_id in run.execution_path()`
  per row — a direct, per-task "did this fire in this run" boolean.

### A real bug found re-verifying this live

Re-running `run-scenario` against port 8082 while a *different*,
separately-started SimPN service (from an unrelated interactive debugging
session) was already listening there produced a trace with empty
`CASE_START`/`CASE_COMPLETE` payloads — looking exactly like the fix hadn't
taken effect. It had; the actual bug was in `ServiceHandle.start()`
(Milestone 2): it polled `GET /health` on the target port to decide the
service was ready, with no check that *its own* subprocess was the one
answering. If another process already held the port, the new subprocess
would fail to bind and exit — but its own startup (cold venv, BPMN parse,
`DecProblem` build) is slower than the pre-existing listener's instant
response, so the health check would succeed against the *other* service
before ours had even reached its own (soon-to-fail) bind call. The whole
repetition matrix then silently ran against a stale, unrelated service with
no error at all. Confirmed the race concretely: a direct Python-level test
showed `start()` return "success" while `lsof` showed only the old PID
actually listening on the port, and the new subprocess sat alive-but-silent
for several seconds before crashing with `OSError: [Errno 48] Address already
in use` — well after `start()` had already returned.

Fixed by abandoning the network health check entirely in favor of a
deterministic one: wait for *our own* subprocess's own stdout to contain its
startup line (`server.serve`'s `"[simpn-runtime] serving process ..."`,
redirected into a per-build `service.log` instead of `subprocess.PIPE`, since
nothing was draining a PIPE after `start()` returned and a long matrix run
logging enough could eventually block the service's own writes). That line
can only be written by a process that itself bound successfully, so there is
no other listener left to be fooled by. `self.process.poll()` is still
checked on each loop iteration for a fast, clear error if the subprocess dies
before ever producing that line.

### Verification

- `tests/test_simpn_adapter.py`: `test_initial_and_final_variables_read_the_case_start_and_complete_payloads`,
  `test_final_variables_falls_back_to_case_halted_when_terminated`, and
  `test_run_instance_populates_case_start_and_complete_with_real_variables`
  (the real engine wiring end to end, not just a hand-written trace fixture).
- `tests/test_scenarios.py`: `test_write_run_table_round_trips_via_csv` updated
  for the new `trace_dir`/`app_name` parameters and `input_`/`output_`/
  `activity_` columns; `test_write_run_table_blanks_variable_keys_other_runs_dont_have`
  covers the union-header/blank-cell behavior across two configs with
  disjoint variable sets; `test_activity_columns_covers_tasks_only_not_gateways_or_events`
  confirms the four-task universe against the real `loan_approval_simpn` BPMN
  and that gateways/events are excluded. 64/64 passing across the full suite.
- Live: ran `loan_approval_simpn`'s S01-nominal under G0 with `--csv`, on a
  cleanly-freed port 8082 — `input_applicant_id`, `input_credit_history`,
  `output_credit_score: 0.84` (the Milestone 4 formula's real output), and
  every other process variable all appeared correctly in the generated CSV.
  Separately reproduced the port-race bug live (a stale service from an
  unrelated debugging session on the same port), confirmed the old code
  silently validated against it, confirmed the fix now raises a clear
  `OSError`-quoting error instead.
- Live, for the activity columns specifically: ran S02-regulatory-restriction
  under G0 — `activity_check_credit=True`, `activity_give_loan_of_1000_usd=True`,
  `activity_inform_account_manager=True`, `activity_send_a_rejection_letter=False`,
  correctly showing *why* `goal_achieved=False` (the loan was actually granted
  to the restricted applicant), not just that it was.

## Milestone 7 — progress output and a folder mode for `run-scenario`

### The gap

`run-scenario` ran silently from invocation until the final comparison/
aggregate/ablation tables printed — fine for a quick G0-only check (sub-second
per cell), but a real G1-G3 matrix with live LLM calls can take tens of
seconds per cell and minutes overall, with zero indication anything was
happening in between. Separately, running every scenario in an app's
`scenarios/` directory required an external shell `for` loop issuing one
`run-scenario` invocation per file — functional, but it meant rebuilding and
restarting each config's SimPN service once per scenario file, pure overhead,
and it meant N separate CSVs to stitch back together by hand instead of one.

### Implementation

- `src/cuga_flo/scenarios/runner.py` — the single-scenario matrix loop was
  generalized into `run_matrix_many(app_name, scenario_paths: list[str],
  configs, repetitions, trace_dir) -> dict[str, dict[str, list[Scorecard]]]`
  (keyed `scenario_id -> config -> per-repetition scorecards`), looped
  config-outermost, scenario-middle, repetition-innermost — each config's
  service is built and started exactly once regardless of how many scenarios
  are given, with every scenario's repetitions run against that one instance
  before it's torn down. Prints `[{config}] building and starting the SimPN
  service...` before each config's service comes up, and
  `[{step}/{total}] {scenario_id} / {config} / rep {n} of {repetitions} ...
  done (goal=..., {cycle}s)` per repetition, flushed immediately. The original
  `run_matrix(app_name, scenario_path, ...)` (unchanged signature and return
  type) is now a thin wrapper: `run_matrix_many(app_name, [scenario_path],
  ...)[scenario.scenario_id]` — one implementation behind both forms, rather
  than two near-duplicate loops to keep in sync.
- `src/cuga_flo/scenarios/runner.py`'s new `discover_scenarios(folder) ->
  list[str]` — sorted `*.yaml` files directly under a directory, for
  deterministic, repeatable ordering across runs.
- `src/cuga_flo/scenarios/cli.py`'s `run()` — detects whether `scenario_path`
  is a directory (`Path.is_dir()`) or a single file; either way it now calls
  `run_matrix_many` with one or many scenario paths, then loops the *existing*
  `format_comparison`/`aggregate`/`format_ablation` calls once per scenario
  (unchanged output shape, just repeated) — combining the ablation itself
  across different scenarios wouldn't be meaningful, since each one tests a
  different condition, so that part deliberately stays per-scenario.
- `src/cuga_flo/scenarios/analysis.py`'s `write_run_table` — generalized from
  `(scenario_id: str, results: dict[str, list[Scorecard]])` to `(results:
  dict[str, dict[str, list[Scorecard]]])`, keyed `scenario_id -> config ->
  scorecards`. A single-scenario call just wraps its old `results` dict as
  `{scenario.scenario_id: results}`; a folder-mode call passes
  `run_matrix_many`'s return value straight through — one CSV-writing
  implementation handles both, producing one combined file with every
  scenario's rows when there's more than one.
- `src/cuga_flo/cli/__init__.py` — `run-scenario`'s `scenario_path` help text
  and command docstring updated to describe folder mode and the progress
  output.

### Verification

- `tests/test_scenarios.py`: `test_discover_scenarios_finds_every_yaml_file_sorted`
  (against the real `loan_approval_simpn/scenarios/` directory — S01/S02/S03);
  `test_write_run_table_covers_multiple_scenarios_in_one_csv` (two scenarios'
  rows combined into one CSV, disambiguated by `scenario_id`); the existing
  `write_run_table` tests updated for the new nested-dict signature. 66/66
  passing across the full suite.
- Live: ran `loan_approval_simpn`'s single-scenario form under G0 with 2
  repetitions — progress lines appeared correctly
  (`[G0] building and starting the SimPN service...`, then two
  `[n/2] S01 / G0 / rep n of 2 ... done (...)` lines), confirming the
  single-scenario path's behavior and output are unchanged apart from the new
  progress lines.
- Live: ran folder mode against all three of `loan_approval_simpn/scenarios/`
  under G0 — one `[G0] building...` line (not three), six total progress
  lines (`[1/3]` through `[3/3]`, one per scenario), each scenario's own
  comparison/aggregate/ablation block printed in turn, and one combined CSV
  with three rows (`S01`/`S02`/`S03`), each with correct `input_`/`output_`/
  `activity_` columns for its own run — confirming the config-once/
  scenario-reused loop ordering, the per-scenario reporting, and the combined
  CSV all work as designed.

## Milestone 8 — a second, BPS-simulated cycle time

### The gap

A user reading Milestone 7's progress output asked directly why every G0
repetition showed `0.0s` — `cycle_time_seconds` is real wall-clock time
(`time.time()` at each trace event, see `trace.py`), and SimPN's
`DecProblem.step()` is a discrete-event simulator: it advances its own
internal notion of time logically when a token becomes available after a
`delay=duration`, it does not actually sleep out that duration. So a whole
G0 case (no LLM calls at all) really does execute in well under a
millisecond of real time, regardless of how long the *modeled* process is
supposed to represent — `0.0s` was correct, not a bug, but genuinely easy to
misread as "this process takes no time at all." The simulated duration was
already in the trace (each `ACTIVITY_COMPLETE` event's own `duration`
payload, in simulated seconds per `simpn_parameters.yaml`) but never
aggregated anywhere a researcher would see it.

### Implementation

- `src/cuga_flo/evaluation/trace_run.py` — `TraceRun.simulated_cycle_time_seconds()`
  sums every `ACTIVITY_COMPLETE` event's `duration` payload value. Gateways and
  hooks are instantaneous in this model (no `duration` key at all) and the
  execution path is always serial (no true parallel-gateway join — see
  `engine.py`'s module docstring), so a plain sum is correct; `None` (not
  `0.0`) when the run never completed a single activity, since those are
  different facts. `cycle_time_seconds`'s own docstring was expanded to
  explain the wall-clock-vs-simulated distinction explicitly, pointing at the
  new method.
- `src/cuga_flo/evaluation/metrics.py` — `Scorecard` gains
  `simulated_cycle_time_seconds: float | None`, populated by `evaluate()`
  alongside the existing `cycle_time_seconds`. Being a plain `Scorecard`
  field, it's automatically picked up by `--csv`'s `_SCORECARD_FIELDS` (which
  iterates `dataclasses.fields(Scorecard)` generically) with no change needed
  in `analysis.py` — and by `cuga-flo evaluate`'s JSON output, same reason.
  Deliberately *not* added to the printed comparison table
  (`format_comparison`'s `cycle_s` column) or the progress-line format — this
  was scoped as "add the field," not "change what's printed by default";
  both remain the single, lazy source of this number everywhere else.

### Verification

- `tests/test_simpn_adapter.py`:
  `test_simulated_cycle_time_sums_activity_durations_not_gateways_or_hooks`
  (hand-written fixture, confirms gateways/hooks are excluded);
  `test_simulated_cycle_time_is_none_without_any_completed_activity`; and an
  assertion added to the existing `test_run_instance_populates_case_start_and_complete_with_real_variables`
  (`== 2.0`, the real engine wiring, not just a fixture). `tests/test_scenarios.py`'s
  `_card()` helper updated for the new required `Scorecard` field. 68/68
  passing across the full suite.
- Live: ran `loan_approval_simpn`'s S01-nominal under G0 with `--csv` —
  `cycle_time_seconds` came back as `0.00028...` (sub-millisecond wall-clock,
  as expected) while `simulated_cycle_time_seconds` came back as `4.0`
  (check credit 2.0s + give loan 1.0s + inform account manager 1.0s, exactly
  matching `simpn_parameters.yaml`'s configured durations for the path this
  applicant took) — confirming the two fields measure genuinely different
  things and both are now visible side by side in the same row.

# Split CUGA FLO into its own repo (`cuga-project/cuga-flo`)

Backlog item 2. Supersedes nothing; the remote-agent work (`remote-agent-binding-plan.md`)
lands *before* this and moves with it.

## Context

CUGA FLO (FLow Oversight) is a **process harness for policy-aware, structurally-enforced
agent workflows** — it separates deterministic process execution from agentic reasoning and
governance. It does **not** run the BPMN graph itself: a pluggable `WorkflowEngine` does, and
the harness talks to it only through `MCPFlowBridge` (a FastMCP server), so CUGA FLO
integrates with *any* workflow engine over MCP. A LangGraph engine ships as the demo/default
backend; Flowable and Apache KIE (Kogito) adapters are also included. LLM reasoning is
confined to three control points — task fulfilment (`TaskAgent`), gateway routing
(`DecisionAgent`), and hook-governed flow adaptation (`FlowAgent`); the engine, never an LLM,
decides what executes next, and every permitted adaptation is policy-governed and audited.
The authoritative description is the engine `README.md` (moves with the code — see below).

It started life standalone and was folded into `cuga-agent` on the `cugaflo` branch by commit
`f67217af` ("feat(flow): merge cuga-flo BPMN-driven flow engine into cuga-agent"). The branch
is now **178 commits** ahead of `origin/main`: **121 net‑new files** (the harness, the MCP
bridge, the LangGraph engine, the Flowable/Kogito adapters, the example apps, build/run
scripts, docs, diagrams, `.claude/` material) plus **18 modified** `cuga-agent` core files.

The engine-decoupling is why this split is clean: the harness already depends on `cuga-agent`
only for the reasoning agent (`CugaAgent`) and shared infrastructure, and on the engine only
through MCP tool schemas — no engine-specific code reaches back into it.

We want CUGA FLO back in its own repo — `https://github.com/cuga-project/cuga-flo.git` — with
`cuga-agent` (`https://github.com/cuga-project/cuga-agent.git`) consumed as an installed
**dependency**, not a host. Everything created on the branch is ported, history preserved.
All startup behaviour is preserved: `cuga start flow_agent_inline <app>` becomes
`cuga-flo start flow_agent_inline <app>` and still enables the CugaSupervisor and starts the
Carbon UI.

### Decisions taken (from the user)

- **Renamespace to a new top-level `cuga_flo/` package.** Forced, not optional:
  `cuga-agent` ships a real `src/cuga/__init__.py` (setuptools regular package,
  `package-dir = {"" = "src"}`), so a second distribution cannot legally add modules under
  `cuga.*` — PEP 420 namespace coexistence is off the table.
- **Vendor the `cuga-agent` core patches and apply them on install.** cuga-flo carries the
  diffs and a `cuga-flo patch-host` command applies them to the installed `cuga/` package.
  No PR to `cuga-agent` required. (Trade-off: any `pip install -U cuga-agent` silently
  reverts them — the CLI must hard-check a marker; see Risks.)
- **Preserve history with `git filter-repo`.** Extract the flow files *with their commits*
  from the `cugaflo` branch into the new repo.

## Target repo shape

```
cuga-flo/
  pyproject.toml            # name = "cuga-flo"; depends on cuga (cuga-agent), a2a-sdk<1.0, …
  README.md                # ← the engine README.md (the authoritative "what CUGA FLO is")
  CLAUDE.md  BACKLOG.md  .gitignore  .env.example
  src/cuga_flo/
    engine/                 # ← nodes/cuga_flow/*.py
      flow_agent.py  flow_agent_state.py  decision_agent.py  task_agent.py
      flow_config.py  langgraph_engine.py  workflow_engine.py  bpmn_parser.py
      hook_manager.py  process_registry.py  remote_agent.py  app_yaml_schema.py
    mcp/bridge.py            # ← server/cuga_flo_mcp/bridge.py  (MCPFlowBridge — always instantiated)
    adapters/
      flowable/proxy.py      # ← server/flowable/flowable_proxy.py   (standalone, no cuga imports)
      kogito/proxy.py         # ← server/kogito/kogito_proxy.py
      kogito/runtime/         # ← CugaFlo.java, FlowRedirect.java, *.template, redirectspike.bpmn
    cli/                     # new: the `cuga-flo` Typer app
      __init__.py  start.py  # start.py = lifted `flow_agent_inline` block from cuga/cli/main.py
  applications/              # ← the runnable apps, promoted out of docs/examples/
    loan_approval/  loan_approval_kogito/  receive_order/  trip_planner/  excel_flows_kogito/
    run.py                   # headless runner; resolves an app by name in ./
  scripts/
    build_kogito_app.sh  serve_flow.py    # paths repointed to applications/
  patches/                   # vendored cuga-agent diffs + applier metadata
    01-supervisor-delegation-flowagent.patch  02-…  03-…  06-llm-http-client-timeout.patch
  docs/
    README-FLOWABLE.md  README-KOGITO.md  kogito-to-CUGA-FLO.md
    model_transform_knowledge/{flowable,kogito}/*.md   # ← BPMN-authoring know-hows (docs, not apps)
    diagrams/  images/cugaflo-logo.png
  tests/
    test_remote_agent.py  test_flow_config_engine_dispatch.py
  .claude/plans/  .claude/commands/cuga-*.md
```

**Why `applications/` and not `docs/examples/`.** In cuga-agent these were demo material
living under `docs/`. In a dedicated repo the apps *are* the product surface — the artifact
triples (`config/*_config.yaml` + `.bpmn` + `policies/*.md`) that CUGA FLO Studio authors and
the harness runs — so they get a first-class top-level folder.

**Dropped, not ported:**
- `docs/examples/flow_agent_app/` (the Python-agent variant — agents written in Python
  instead of compiled from YAML). No longer a used modality.
- `docs/examples/flow_agent_app_inline/external_mcp_engine/` — a stub of the *removed*
  "external workflow engine" modality (CUGA FLO as MCP **client** driving someone else's
  engine, the "ordo" experiment). Its `server.py` imports `cuga_flow.remote`, a module
  deleted in `9f7f9e04` ("remove ordo integration … lives on cugaflo-ordo"), so it no
  longer even imports. Unrelated to `cuga_flo/mcp/` (see below).

### The MCP bridge is core, not a modality

`MCPFlowBridge` (`src/cuga_flo/mcp/bridge.py`) is instantiated on **every** run, all three
engines. It is the single `FastMCP` server the engine and the FlowAgent harness talk through:
FlowAgent registers `execute_task` / `route_gateway` / `evaluate_hook` / `complete_process`,
the `ProcessRegistry` registers `register_flow` / `get_bpmn_process` / `get_flow_annotations`,
and the active engine registers `run_process`. LangGraph uses it in-process
(`FastMCPTransport`); Flowable and Kogito additionally get an HTTP listener
(`_ensure_http_server`) so the out-of-process engine can call back. This is not the
`external_mcp_engine` stub — that was the opposite direction and is gone.

## What moves — inventory

| Source (on `cugaflo` branch) | Destination in `cuga-flo` | Notes |
|---|---|---|
| `src/cuga/backend/cuga_graph/nodes/cuga_flow/*.py` (12 modules) | `src/cuga_flo/engine/` | internal imports rewritten (below) |
| `…/cuga_flow/README.md` | repo root `README.md` | the authoritative description; `../../../../../../docs/images/…` logo path fixed to `docs/images/cugaflo-logo.png` |
| `…/cuga_flow/{README-FLOWABLE,README-KOGITO,kogito-to-CUGA-FLO}.md` | `docs/` | internal links repointed |
| `…/cuga_flow/test_remote_agent.py` | `tests/test_remote_agent.py` | |
| `src/cuga/backend/server/cuga_flo_mcp/` | `src/cuga_flo/mcp/` | `MCPFlowBridge` — the engine↔harness bridge, instantiated on every run (all 3 engines) |
| `src/cuga/backend/server/flowable/` | `src/cuga_flo/adapters/flowable/` | proxy has **no** `cuga` imports — moves clean |
| `src/cuga/backend/server/kogito/` | `src/cuga_flo/adapters/kogito/` | `kogito_proxy.py` standalone; Java/templates are app-independent |
| `docs/examples/flow_agent_app_inline/{loan_approval,loan_approval_kogito,receive_order,trip_planner,excel_flows_kogito}/` | `applications/<app>/` | the 5 runnable apps (dirs with `config/`) |
| `docs/examples/flow_agent_app_inline/run.py` | `applications/run.py` | headless runner; app-dir resolution stays relative to the file |
| `docs/examples/flow_agent_app_inline/schemas/app_yaml_schema.py` | `src/cuga_flo/engine/app_yaml_schema.py` | promoted into the package (drop `schemas/__pycache__/`); fix the two path comments in `flow_config.py:21,30` that point at the old location |
| `docs/examples/flow_agent_app_inline/model_transform_knowledge/` | `docs/model_transform_knowledge/` | authoring know-hows — docs, not apps |
| `docs/examples/flow_agent_app_inline/external_mcp_engine/` | — | **dropped**: dead stub of the removed external-engine modality (`server.py` imports a deleted module) |
| `docs/examples/flow_agent_app/` | — | **dropped**: Python-agent variant, no longer a used modality |
| `scripts/build_kogito_app.sh`, `scripts/serve_flow.py` | `scripts/` | `RUNTIME_DIR`, `APPS_DIR`, `run.py` path repointed |
| `docs/diagrams/cuga-flo-*` (8), `docs/images/cugaflo-logo.png` | `docs/` | |
| `tests/unit/test_flow_config_engine_dispatch.py` | `tests/` | |
| `CLAUDE.md`, `BACKLOG.md`, `.claude/plans/*`, `.claude/commands/cuga-*.md` | same paths | CLAUDE.md `cuga start` → `cuga-flo start` |
| `.gitignore` / `.env.example` additions | new repo equivalents | keep `.quarkus/`, `build/kogito/`, drop `docs/paper/`; keep `FLOWABLE_*` block |

## The `cuga-agent` dependency — import surface

`cuga-flo` code keeps these imports **unchanged** (they resolve to the installed `cuga-agent`):

| Import | Used for |
|---|---|
| `from cuga.sdk import CugaAgent` | the reasoning agent at every control point |
| `from cuga.config import settings` | dynaconf settings |
| `from cuga.backend.llm.models import LLMManager` | model construction |
| `from cuga.backend.activity_tracker.tracker import ActivityTracker, Step` | UI trace steps |
| `from cuga.backend.cuga_graph.state.agent_state import AgentState` | task-agent state type |
| `from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import fetch_agent_card, delegate_task_via_a2a_sdk, HAS_A2A_SDK` | remote-agent binding |
| CLI only: `cuga.cli.app_manager.AppManager`, `cuga.backend.server.{config_store,managed_mcp,demo_manage_setup}`, `cuga.backend.secrets.seed`, `cuga.supervisor_utils.supervisor_config`, `cuga.configurations.instructions_manager` | reproduce the `flow_agent_inline` startup |

`pyproject.toml` dependency: `cuga` from git (`cuga @ git+https://github.com/cuga-project/cuga-agent.git@<pinned-tag-or-sha>`) until it is on PyPI, plus `a2a-sdk[http-server]>=0.3.22,<1.0` (constrains the resolved env — the 1.0 API break silently disables A2A).

### Internal import rewrite (mechanical, ~30 sites)

| From | To |
|---|---|
| `cuga.backend.cuga_graph.nodes.cuga_flow.<mod>` | `cuga_flo.engine.<mod>` |
| `cuga.backend.server.cuga_flo_mcp` / `.bridge` | `cuga_flo.mcp.bridge` |
| `cuga.backend.server.flowable.flowable_proxy` | `cuga_flo.adapters.flowable.proxy` |
| `cuga.backend.server.kogito.kogito_proxy` | `cuga_flo.adapters.kogito.proxy` |

Do this **inside `git filter-repo`** with `--path-rename` for file moves, then a follow-up
`sed`/`ruff --fix`-style commit for the `import` lines so history stays bisectable.

## Vendored `cuga-agent` patches (`patches/`) — **4, as shipped**

| # | Target file in `cuga-agent` | What it does | Status |
|---|---|---|---|
| 01 | `cuga_graph/nodes/cuga_supervisor/delegation.py` | `create_agent_delegation_func` dispatches to `FlowAgent.invoke` (guarded `try/except` import of `cuga_flo.engine.flow_agent` — inert without cuga-flo) | **shipped** — required for supervisor→FlowAgent delegation |
| 02 | `supervisor_utils/supervisor_config.py` | handle `type: flow_agent` (imports `cuga_flo.engine.flow_config.load_flow_from_yaml`) and accept `FlowAgent` from `import_from` | **shipped** — every app's `supervisor*.yaml` uses `type: flow_agent` |
| 03 | `cuga_graph/utils/agent_loop.py` | drain `Task: ` / `Gateway ` / `Hook ` tracker steps into the event stream | **shipped** — the live trace in the Carbon UI |
| 06 | `backend/llm/models.py` | set `httpx.Timeout` on the OpenAI client so it can't hang | **shipped** — generic robustness |
| ~~04~~ | `backend/server/manage_routes.py` — `/flow/policies` | dropped: orphan endpoint, no caller | — |
| ~~05~~ | `config.py` + `cuga_lite` executors — code-exec timeout | **dropped: superseded upstream.** current `cuga-agent` main already has `settings.advanced_features.sandbox_execution_timeout` doing the same thing; the old patch now conflicts | — |

Patches 01/02 have their `cuga.backend.cuga_graph.nodes.cuga_flow.*` imports repointed to
`cuga_flo.engine.*`. `a2a-sdk<1.0` is a cuga-flo `pyproject.toml` constraint, not a host patch.

### Applier — `src/cuga_flo/_hostpatch.py` + `cuga-flo patch-host [--check|--revert]`

- Locates the installed `cuga` via `find_spec`; walks up for a `.git` to tell **editable
  checkout** (the normal dev case) from **wheel**.
- **Editable:** `git checkout HEAD -- <4 target files>` then re-apply all with
  `git apply --3way` — fully idempotent and drift-tolerant (the `--3way` merge re-bases each
  hunk onto current context; only a genuine conflict in the flow region fails). **Wheel:**
  `patch -p2 --forward --fuzz=3`.
- Marker `<cuga_pkg_dir>/.cuga_flo_hostpatch.json` records `cuga-agent` version + per-patch
  sha256. `--check` verifies the marker, the version, the sha set, and that each patch's
  signature string is still present in the target file; exits non-zero otherwise.
- `cuga-flo start` and `cuga-flo doctor` run `--check` first and refuse rather than failing
  deep in supervisor load.

## The `cuga-flo` CLI

`cuga-flo` is a Typer app (`[project.scripts] cuga-flo = "cuga_flo.cli:app"`).
`cuga-flo start flow_agent_inline <app>` reproduces `cuga/cli/main.py` lines ~969–1100
verbatim except paths:

1. `DYNACONF_SUPERVISOR__ENABLED=true`; resolve `<app>` under `applications/<app>`,
   require `config/supervisor*.yaml`.
2. `DYNACONF_SUPERVISOR__CONFIG_PATH=<yaml>`; `settings.reload()`.
3. env: `CUGA_MANAGER_MODE=true`, `DYNACONF_POLICY__FILESYSTEM_SYNC=false`,
   `MCP_SERVERS_FILE=none`, `CUGA_AGENT_NAME=FlowAgent`, `CUGA_AGENT_DESCRIPTION=…`.
4. `ensure_managed_mcp_file_exists`, `reset_config_db`,
   `resolve_llm_api_key_ref` (best-effort).
5. build the `flow_agent_config` dict → `save_draft` + `save_config` (key `"cuga-default"`).
6. `AppManager`: `kill_processes_by_port([registry_port, demo])`, `start_registry(host)`,
   `start_demo(host, sandbox)` → **Carbon UI on `http://127.0.0.1:8001`**.
7. print the running panel; `wait_for_direct_processes()`.

Also repoint the app-path strings: `applications/run.py` usage text, `build_kogito_app.sh`
(`APPS_DIR` → `applications/`, `RUNTIME_DIR` → `src/cuga_flo/adapters/kogito/runtime/`,
`run.py` path, and step 3 `cuga start …` → `cuga-flo start …`), `serve_flow.py`
(`REPO`/`APPS`), and `CLAUDE.md`.

## Execution outline

1. **Repo exists.** `https://github.com/cuga-project/cuga-flo.git` is already created. The
   `git filter-repo` output is a fresh, unrelated history, so the first publish is
   `git push -u origin main` if the repo was created empty, or `git push -u --force origin main`
   if GitHub seeded it with a README/LICENSE/`.gitignore` (one throwaway commit, safe to
   overwrite on a repo with no other work in it). `gh` is not authenticated in this
   environment, so the push itself is user-run.
2. **Extract with history.** From a fresh clone of `cuga-agent` at the `cugaflo` tip:
   ```
   git filter-repo \
     --path src/cuga/backend/cuga_graph/nodes/cuga_flow/ \
     --path src/cuga/backend/server/cuga_flo_mcp/ \
     --path src/cuga/backend/server/flowable/ \
     --path src/cuga/backend/server/kogito/ \
     --path docs/examples/flow_agent_app_inline/ \
     --path docs/diagrams/ --path docs/images/cugaflo-logo.png \
     --path scripts/build_kogito_app.sh --path scripts/serve_flow.py \
     --path tests/unit/test_flow_config_engine_dispatch.py \
     --path CLAUDE.md --path BACKLOG.md \
     --path .claude/plans/ --path .claude/commands/ \
     --path-rename src/cuga/backend/cuga_graph/nodes/cuga_flow/:src/cuga_flo/engine/ \
     --path-rename src/cuga/backend/server/cuga_flo_mcp/:src/cuga_flo/mcp/ \
     --path-rename src/cuga/backend/server/flowable/:src/cuga_flo/adapters/flowable/ \
     --path-rename src/cuga/backend/server/kogito/:src/cuga_flo/adapters/kogito/ \
     --path-rename docs/examples/flow_agent_app_inline/:applications/
   ```
   `--path-rename` is prefix-only, so it lands the whole `flow_agent_app_inline/` tree under
   `applications/`. A follow-up ordinary commit then sorts the non-app members:
   `applications/schemas/app_yaml_schema.py` → `src/cuga_flo/engine/app_yaml_schema.py`;
   `applications/model_transform_knowledge/` → `docs/`; of the `cuga_flow/*.md` docs (which
   arrive under `src/cuga_flo/engine/`), `README.md` → repo root, the other three → `docs/`;
   **delete** `applications/external_mcp_engine/` and `applications/schemas/__pycache__/`.
   `docs/examples/flow_agent_app/` is simply not in the `--path` set, so it never enters the
   new history. History is preserved for everything kept; the `git mv`s carry it for the
   moved files.
3. **Rewrite imports** — one commit: `cuga.backend.cuga_graph.nodes.cuga_flow` →
   `cuga_flo.engine`, etc. (table above). Run `python -m pyflakes src/cuga_flo` to catch misses.
4. **Add packaging** — `pyproject.toml` (`name = "cuga-flo"`, `[project.scripts]`,
   `package-dir`, `package-data` for `*.java`/`*.template`/`*.bpmn`), `cuga` git dep pinned,
   `a2a-sdk<1.0`.
5. **Add `patches/` + `_hostpatch.py` + `cuga-flo patch-host`/`doctor`.** Generate the 4
   patches with `git diff origin/main...cugaflo -- <file>` per row.
6. **Add `src/cuga_flo/cli/`** — lift the `flow_agent_inline` block; app root → `applications/`.
7. **Repoint scripts** — `build_kogito_app.sh` (`RUNTIME_DIR` → `src/cuga_flo/adapters/kogito/runtime/`,
   `APPS_DIR` → `applications/`, `run.py` path), `serve_flow.py` (`REPO`/`APPS`).
8. **`.gitignore` / `.env.example`** — new-repo versions (Python + venv + `.quarkus/` +
   `build/kogito/` + the `FLOWABLE_*` block; drop `docs/paper/`).
9. **Verify** (below), then hand back for `git push`.

## Verification — results

Run against a fresh venv built from cuga-agent's `uv.lock` (`uv sync --frozen` in a
`git worktree` at the pinned ref `fbb9f185`) + `uv pip install -e cuga-flo`.

| Check | Result |
|---|---|
| `cuga-flo patch-host` — apply / `--check` / idempotent re-run / `--revert` | ✅ all pass (`git apply --3way`, 4 patches, clean against cuga-agent `0.3.0` @ `fbb9f185`) |
| `import cuga_flo` + engine / mcp / adapters / remote_agent | ✅ resolve |
| `pytest tests/` | ✅ **15 passed** |
| `python applications/run.py receive_order` — headless, LangGraph engine | ✅ end-to-end: 3 task agents, parallel gateway join, `is_complete=True`, real LLM via MCPFlowBridge |
| `python scripts/serve_flow.py receive_order` | ✅ boots, MCP HTTP on :8090 returns 200 to `tools/list` |
| `cuga-flo start flow_agent_inline receive_order` | ✅ registry :8001 → 200; Carbon UI :7860 → 200 (`<title>CUGA</title>`), `/manage` → 200; supervisor compiled the FlowAgent from `cuga_flo.engine` via patch 02; **0 tracebacks** |

**Not run this session (environment, not port):**

- **Flowable engine** (`loan_approval`) — needs `flowable/flowable-ui` on :8080 (not running).
- **Kogito engine** (`loan_approval_kogito`, `excel_flows_kogito`) — host has JDK 11/8 only;
  Kogito/Quarkus codegen needs **JDK 17**. `scripts/build_kogito_app.sh` cannot run here.
- Host regression (`cuga start demo_supervisor` with cuga-flo absent) — the seams are
  `try/except`-guarded so `HAS_FLOW_AGENT=False` when cuga-flo is not importable; not
  re-exercised.

## Critical files

- New: `src/cuga_flo/cli/__init__.py` + `cli/start.py`, `src/cuga_flo/_hostpatch.py`,
  `patches/0{1,2,3,6}-*.patch`, `pyproject.toml`, `.gitignore`, `.env.example`.
- Moved-and-edited: `src/cuga_flo/engine/flow_config.py` (imports + schema-path comments),
  `…/engine/remote_agent.py` (keeps `cuga.backend…a2a_protocol`), `…/mcp/bridge.py`,
  `scripts/build_kogito_app.sh`, `scripts/serve_flow.py`, `applications/run.py`,
  `README.md` (FLO expansion, logo path, module tree, `applications/` paths).
- Patch contents: `git diff origin/main...cugaflo -- <file>`, then `git apply --3way`.

## Risks & open questions

- **Patch drift.** `pip install -U cuga` reverts the host patches and bumps the version;
  `cuga-flo start` / `doctor` run `patch-host --check` and refuse on a mismatch. `git apply
  --3way` re-bases hunks onto new context, so a routine cuga-agent bump is just "re-run
  `patch-host`"; only a genuine conflict in the flow region needs the patch itself updated.
- **Patch 04 (`/flow/policies`).** No caller exists, so the leanest choice is to **drop it**
  from the initial split. If a consumer appears, the cleaner form is cuga-flo mounting its own
  `APIRouter` onto the running demo app rather than patching `manage_routes.py` (needs an
  app-factory hook in cuga-agent).
- **`gh` not authenticated here.** The repo exists; the final `git push` is user-run. This
  plan stops at a verified local `cuga-flo` tree ready to push (force-push if GitHub seeded
  the repo with an initial commit).
- **`cuga` not on PyPI.** Dependency is a pinned git ref until it is; document the pin-bump +
  `patch-host` re-run as the upgrade ritual.
- **`AppManager` / `config_store` surface.** cuga-flo's CLI leans on cuga-agent internals
  that have no stability guarantee. Acceptable now; revisit if they churn.

## Out of scope (other backlog items / follow-ons)

- **CUGA FLO Studio** (backlog item 3) and embedding the **Apache KIE** process editor
  (item 4). Studio is a **separate project** — a BPMN-annotation tool that *authors* the
  artifact triples an app is made of (the `*_config.yaml`, the `.bpmn`, and the `policies/*.md`).
  It sits upstream of cuga-flo's `applications/` and is not a UI on the runtime; nothing in
  this port depends on it or blocks it.
- **Context/memory sharing across agents** and **rigorous process-variable handling**
  (items 5–6).
- **The frontend.** Its source is in cuga-agent at `src/frontend_workspaces/` (a pnpm
  workspace that webpack-builds into `src/cuga/frontend/dist/`, served by the demo/manage
  server). The `cugaflo` branch changed **none** of that source, and the shipped Carbon UI
  has no CUGA-FLO screens (no `flow_agent` / `/flow/policies` / `bpmn` references in it). The
  `dist/` churn on the branch is rebuilt bundles from merging `main`. cuga-flo therefore
  ships **no frontend** — it drives the stock Carbon UI that cuga-agent already serves.
- **Upstreaming** the generic fixes (patches 05–06) to cuga-agent as normal PRs — worth doing
  independently so cuga-flo can eventually drop them from `patches/`.
- The `BACKLOG.md` "async completion / move Future wait to caller" refactor.

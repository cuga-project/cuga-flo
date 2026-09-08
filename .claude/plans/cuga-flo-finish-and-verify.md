# CUGA FLO — finish the split: push + engine verification

Runbook for the remaining work after the repo extraction. Written to be executed from a
**fresh workspace opened at the new repo** (`cuga-project/cuga-flo`), so it assumes no
memory of the extraction session.

## Where things stand

- The repo already exists locally at whatever path this workspace is open on (the extraction
  built it at `/Users/liorlimonad/Documents/cuga/cuga-flo`). Branch `main`, remote `origin` =
  `https://github.com/cuga-project/cuga-flo.git`, **not yet pushed**.
- History was preserved with `git filter-repo` (167 commits); the last 7 are the split
  commits (`chore(split)` / `refactor(split)` / `build(split)` / `fix(split)`).
- **Already verified** on macOS with a venv built from cuga-agent's `uv.lock` @ `fbb9f185`:
  - `cuga-flo patch-host` apply / `--check` / idempotent / `--revert`
  - `pytest tests/` → 15 passed
  - `python applications/run.py receive_order` → full BPMN end-to-end on the **LangGraph** engine
  - `python scripts/serve_flow.py receive_order` → MCP HTTP on `:8090` returns 200
  - `cuga-flo start flow_agent_inline receive_order` → Carbon UI `:7860` → 200, supervisor
    compiles the FlowAgent from `cuga_flo.engine` via the vendored patch
- **Not yet verified** (needs tooling the extraction host lacked): the **Flowable** and
  **Kogito** engine paths. That is the bulk of this runbook.

## Design facts the executor needs

- `cuga-flo` depends on `cuga` (cuga-agent) as an installed package, pinned in
  `pyproject.toml [tool.uv.sources]` to rev `fbb9f185`. cuga-agent core needs 4 vendored
  seams that live in `patches/` and are stamped on by `cuga-flo patch-host`
  (`git apply --3way` for an editable checkout; `patch(1)` for a wheel). `cuga-flo start` and
  `cuga-flo doctor` refuse to run if `patch-host --check` fails.
- Apps live under `applications/<app>/` — each is `config/*_config.yaml` + a `.bpmn` +
  `config/supervisor_*.yaml` + `policies/*.md`. Engine per app:

  | app | `workflow_engine.type` |
  |---|---|
  | `receive_order`, `trip_planner` | `langgraph` (in-process, no external service) |
  | `loan_approval` | `flowable` — needs the Flowable container on `:8080` |
  | `loan_approval_kogito`, `excel_flows_kogito` | `kogito` — needs a built Quarkus service on `:8081`, **JDK 17** |

- LLM config comes from a `.env` in the repo root (copy from `.env.example`; the extraction
  host used an OpenAI-compatible gateway with `MODEL_NAME=claude-sonnet-4-6`). `.env` is
  gitignored — never commit it.
- `git`, `git-filter-repo`, `uv`, `mvn` were present; **JDK was 11 (no 17)** — Kogito needs
  17, so expect to install/point `JAVA_HOME` at a 17 JDK.

---

## Step 1 — Push the repo

```bash
git -C <repo> status                 # expect: clean, branch main
git -C <repo> log --oneline -8       # sanity-check the split commits are on top
git -C <repo> push -u origin main
```

- If the push is rejected because GitHub seeded the repo with a README/LICENSE/`.gitignore`,
  it is safe to `git push -u --force origin main` — the repo has no other work in it. Confirm
  with the user first if unsure.
- Requires an authenticated `gh` / git credential for `cuga-project`. If unavailable, stop
  and hand back — do not invent another host.

**Acceptance:** `git -C <repo> status` shows `Your branch is up to date with 'origin/main'`.

---

## Step 2 — Reproduce the baseline (LangGraph path)

Confirms the port still stands in this workspace before touching the engine paths.

```bash
cd <repo>
uv venv --python 3.12 .venv
uv pip install --no-sources -e "git+https://github.com/cuga-project/cuga-agent.git@fbb9f185#egg=cuga" -e .
# faster alternative if a cuga-agent checkout is handy:
#   git -C <cuga-agent> worktree add /tmp/ca fbb9f185 && (cd /tmp/ca && uv sync --frozen)
#   uv pip install --python .venv/bin/python --no-sources --no-deps -e .   # cuga-flo only
#   then point the venv at /tmp/ca — or just `uv pip install -e /tmp/ca` into .venv

# If mcp/fastmcp conflict at import (`No module named 'mcp.server.fastmcp'`), the venv did
# not honour cuga-agent's lock — rebuild via the `uv sync --frozen` route above.

cp .env.example .env      # then fill in a real LLM key/base-url

.venv/bin/cuga-flo patch-host
.venv/bin/cuga-flo patch-host --check          # exit 0
.venv/bin/python -m pytest tests/ -q            # 15 passed
.venv/bin/python applications/run.py receive_order
```

**Acceptance:** `patch-host --check` exits 0; 15 tests pass; `run.py receive_order` logs
`cuga_flo.engine.flow_agent:invoke … Process execution completed: is_complete=True` with task
agents and a gateway firing. (A `libc++abi … recursive_mutex` line *after* the result is a
known noisy atexit crash in a torch/tokenizers dependency — ignore it.)

---

## Step 3 — Flowable engine end-to-end (`loan_approval`)

### Prereqs

```bash
docker run --rm -d -p 8080:8080 --name flowable flowable/flowable-ui:latest
# wait until http://localhost:8080/flowable-ui/ is up (~60–90s)
```

`.env` (defaults match the all-in-one image):

```
FLOWABLE_BASE_URL=http://localhost:8080/flowable-ui/process-api
FLOWABLE_USER=admin
FLOWABLE_PASSWORD=test
```

### Run

`loan_approval`'s BPMN must be **deployed** to Flowable. `MCPFlowBridge.register_flowable_engine`
takes `deploy=True`; `flow_config.py` wires it from the app YAML. Check
`applications/loan_approval/config/loan_approval_config.yaml` for a `deploy:` / `workflow_engine`
block; if deployment is not automatic, deploy the model once:

```bash
.venv/bin/python -m cuga_flo.adapters.flowable.proxy deploy \
    applications/loan_approval/config/Loan-Approval-Process.bpmn20.xml
```

Then:

```bash
.venv/bin/python applications/run.py loan_approval
```

### Acceptance

- No `Request to Flowable failed: timed out` / Tomcat 404.
- Log shows `cuga_flo.mcp.bridge … registered FlowableProxy run_process tool` and the MCP
  HTTP callback server starting (`MCP HTTP server listening on port 8090`).
- Flowable drives the process; CUGA FLO answers `execute_task` / `route_gateway` /
  `evaluate_hook` callbacks; `run.py` prints a terminal `FlowState` with
  `is_complete=True` and `task_results` for the credit-check + approve/reject tasks.
- The credit-decision **gateway** and the **hook** on the outgoing flow both appear in
  `gateway_decisions` / `hook_evaluations`.

Tear down: `docker rm -f flowable`.

---

## Step 4 — Kogito engine end-to-end (`loan_approval_kogito`)

### Prereqs — JDK 17

Kogito/Quarkus codegen rejects JDK < 17.

```bash
/usr/libexec/java_home -V                        # list installed JDKs
# install one if needed, e.g. `brew install openjdk@17`
export JAVA_HOME=$(/usr/libexec/java_home -v 17)
java -version                                    # must report 17.x
```

(Also recorded in memory `kogito-jvm-environment`: export `JAVA_HOME`; Kogito serves on
`:8081`, Flowable on `:8080` — do not run both engines on the same port.)

### Build the per-app Quarkus service

```bash
./scripts/build_kogito_app.sh loan_approval_kogito
```

- The script reads the runtime from `src/cuga_flo/adapters/kogito/runtime/` and the app from
  `applications/loan_approval_kogito/`, emitting `build/kogito/loan_approval_kogito/`.
- **Acceptance for the build:** completes with **no `missing <bpmn2:property>` warnings** and
  produces `build/kogito/loan_approval_kogito/run.sh`.

### Run

```bash
build/kogito/loan_approval_kogito/run.sh          # leave running; serves :8081
# in another shell:
.venv/bin/python applications/run.py loan_approval_kogito
```

### Acceptance

- `applications/loan_approval_kogito/config/*_config.yaml` has
  `workflow_engine: {type: kogito, url: http://localhost:8081, process_id: loan_approval}`.
- Log shows `cuga_flo.mcp.bridge … registered KogitoProxy run_process tool (MCP HTTP callback
  on port 8090)`.
- The Kogito service starts the instance; `CugaFlo.java` calls back over MCP HTTP for
  `execute_task` / `route_gateway` / `evaluate_hook` and finally `complete_process`.
- `run.py` returns `is_complete=True`; the run is visible in the Kogito Management Console
  instance view (`README-KOGITO.md` / `docs/kogito-to-CUGA-FLO.md` explain the console).
- **The 120s exposure:** a hook/gateway that waits on a human past the `CugaFlo.java` ceiling
  makes the script task rethrow and fails the instance — this is expected and documented, not
  a regression. Do not paper over it by raising the timeout.

### Also worth running

```bash
.venv/bin/python scripts/serve_flow.py excel_flows_kogito
# then call the MCP `start_process` tool on the printed endpoint
```

`excel_flows_kogito` exercises the A2A remote-agent bindings (`agent_type: agent0`,
`human_consultation: agent0`) — needs a reachable agent0 (see
`.claude/plans/agent0-team-brief.md`). Without agent0 it will fail at the first delegated
task; that still confirms the Kogito↔bridge wiring up to that point.

---

## Step 5 — Host regression (cuga-agent unaffected when cuga-flo is absent)

The 4 patches are `try/except`-guarded. In a venv with **cuga-agent but not cuga-flo**:

```bash
python -c "import cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation as d; print(d.HAS_FLOW_AGENT)"
# -> False, no ImportError
```

And cuga-agent's own `cuga start demo_supervisor` should behave exactly as on stock main.

**Acceptance:** import succeeds with `HAS_FLOW_AGENT=False`; a stock cuga-agent demo is
unchanged.

---

## If a patch stops applying

`cuga-flo patch-host` will say which one and that the flow region drifted. Fix:

1. `git -C <cuga-agent checkout> show HEAD:<target file>` — current upstream version.
2. Re-apply the flow-specific hunk (the patch shows it) onto that, resolve any real conflict.
3. `git -C <cuga-agent checkout> diff -- <target file> > patches/<NN>-*.patch`, keeping the
   `cuga_flo.engine.*` import paths.
4. Bump `[tool.uv.sources] cuga.rev` in `pyproject.toml` to the ref you resolved against.
5. `cuga-flo patch-host` again.

## Out of scope here

Studio, the KIE editor embed, cross-agent memory, process-variable rigor (backlog items 3–6);
upstreaming patch 06 to cuga-agent as a normal PR.

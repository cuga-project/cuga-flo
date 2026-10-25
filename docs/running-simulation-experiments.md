# Running Simulation Experiments with CUGA FLO × SimPN

A practical, end-to-end guide to taking a BPMN application from "regular CUGA FLO
app" to "simulated across the G0–G3 autonomy ladder, with results you can read and
further analyze." For the architecture and *why* behind every design choice, see
[`README-SIMPN.md`](README-SIMPN.md) — this guide is the "how to actually use it"
companion, and cross-references it throughout rather than repeating it.

Everything below uses `loan_approval_simpn` (`applications/loan_approval_simpn/`)
as the running, worked example — a real application in this repo, not a
hypothetical.

---

## 1. The integration, at a glance

CUGA FLO drives BPMN process execution; SimPN is the simulation engine that stands
in for a conventional Business Process Simulation (BPS) backend. Unlike Flowable
or Kogito, SimPN isn't a third-party engine CUGA FLO connects *to* — the
**Simulation Runtime Service** is built from this repo's own code
(`src/cuga_flo/adapters/simpn/`) and runs as its own standalone process, in its own
isolated Python venv (kept separate because `simpn` itself pulls in a GUI/plotting
stack that has no business in CUGA FLO's process).

```
   CUGA FLO (main process)                Simulation Runtime Service
   ────────────────────────                ──────────────────────────
   FlowAgent / TaskAgent /        MCP      - builds one simpn.DecProblem
   DecisionAgent (LLM reasoning) <──HTTP──>   per simulated case
   MCPFlowBridge                            - blocks on execute_task /
   SimPNProxy  ───────HTTP (start)────────>   route_gateway / evaluate_hook
                                              at each agentic control point
```

Four **autonomy configurations** can be applied to the same BPMN model and the
same policies:

| Level | What's agentic |
|---|---|
| **G0** | nothing — plain conditions, no LLM calls at all |
| **G1** | the task(s) marked `mode: task_agent` |
| **G1 + G2** | + the gateway(s) marked `mode: decision_agent` |
| **G1 + G2 + G3** | + the oversight hooks |

A **scenario** fixes what case is being run and what, if anything, changes
mid-execution; the same scenario can be run against every G-level to measure what
autonomy actually buys you. That comparison — one scenario × four configs × N
repetitions — is what the rest of this guide walks through.

---

## 2. Extending a seed application with simulation specs

Start from an application that already works on another engine — `loan_approval`
(Flowable) or `loan_approval_kogito` are the seeds `loan_approval_simpn` was
derived from. Three things carry over **unchanged**, by design: the BPMN model and
the policy markdown files are engine-independent, and the `tasks:`/`gateways:`/
`hooks:` blocks of the app's YAML config don't change shape either — only
`workflow_engine:` does.

```
applications/loan_approval_simpn/
├── config/
│   ├── loan_approval_simpn_config.yaml   ← new: workflow_engine.type: simpn
│   ├── simpn_parameters.yaml             ← new: BPS durations (SimPN's own file)
│   ├── BPMNdiagram.bpmn                  ← copied verbatim from the seed app
│   ├── G0_config.yaml .. G3_config.yaml  ← new: autonomy-ladder variants (§4)
│   ├── expected_properties/*.yaml        ← new: oracles for a single manual run
│   └── supervisor_loan_approval_simpn.yaml ← new: same shape as any supervisor config
├── policies/*.md                         ← copied verbatim from the seed app
└── scenarios/*.yaml                      ← new: frozen scenarios (§3)
```

### What's actually new

**`workflow_engine:` block**, in the main config file:

```yaml
workflow_engine:
  type: simpn
  url: http://localhost:8082
  process_id: BPMNdiagram       # the BPMN process id, as BPMNParser sees it
  callback_host: localhost
  callback_port: 8090
```

**`simpn_parameters.yaml`** — the BPS side of the simulation: how long each task
takes when nothing perturbs it.

```yaml
tasks:
  Activity_0oydey5:   # check credit
    duration_seconds: 2
  Activity_1h9ix55:   # give loan of 1000 USD
    duration_seconds: 1
  # one entry per task element id in the BPMN model
```

**`expected_properties/*.yaml`** — the oracle for a single manual run through
`cuga-flo evaluate` (see §6). Not needed for `run-scenario`, which carries its own
`expected_properties` block per scenario instead.

**`G0_config.yaml` .. `G3_config.yaml`** — only needed if you intend to run the
scenario runner's comparative matrix (§4/§5). Each is the *same* app config with
only `tasks:`/`gateways:`/`hooks:` stripped down to that level — see §4 for how
they differ concretely.

**`formula:`** — a task field, sibling to `tool:`, for giving a `mode: native`
task some real deterministic business logic instead of leaving it a pure
pass-through. The two fields mean opposite things, deliberately kept distinct so
the same YAML value can't mean two different things on two different engines:
`tool:` names an **externally-injected** Python callable (the LangGraph engine's
pattern — a caller constructing `FlowAgent` supplies `tools={name: fn}`; there is
no pure-YAML path to it for any engine today), while `formula:` names a
deterministic computation the **engine itself** provides, built in — looked up by
name in a small registry shipped with that engine's own code
(`src/cuga_flo/adapters/simpn/runtime/formulas.py` for SimPN). `G0_config.yaml`'s
`Activity_0oydey5` task uses `formula: "credit_score_formula"` — a crude but real
approximation of `policies/task-credit_check.md`'s scoring rubric, run with no LLM
and no MCP call at all, giving G0 *some* conventional rule instead of none (see
§7 for why that distinction turned out to matter for how a result should be read).

### Turning this into a runnable service

```bash
source .venv/bin/activate
./scripts/build_simpn_app.sh loan_approval_simpn
```

This dumps the BPMN model and the engine-consumable config to JSON
(`process_model.json`, `engine_config.json`) and copies `simpn_parameters.yaml`
into a staged build directory (`build/simpn/loan_approval_simpn/`), alongside a
fresh isolated venv with `simpn`+`pyyaml` — never CUGA FLO's main venv. The
service reads only that staged copy at runtime, so **re-run this after any change**
to the BPMN model, the app config, or `simpn_parameters.yaml` — none of them take
effect until the next build.

---

## 3. Populating scenarios with workflow parameters and perturbations

A scenario is a frozen YAML file under `applications/<app>/scenarios/`. It has
three parts (`cuga_flo.scenarios.schema.Scenario`):

```yaml
scenario_id: S02
description: >
  One paragraph explaining what this scenario tests and why.

initial_state:
  process_variables:              # the actual case being run
    applicant_name: "Jane Smith"
    applicant_id: "4321"
    loan_amount: 1000
    credit_history: "Excellent payment history, no defaults"
    income: 75000
    employment_years: 5
  semantic: {}                    # thin semantic-world facts present from the start
  bps: {}                         # almost always empty — see the note below

perturbations:                    # changes injected DURING execution
  - when: "Activity_0oydey5.enter"            # "<element_id>.<enter|complete>"
    bps_change:
      activities.Activity_0oydey5.duration_multiplier: 5
  - when: "Gateway_09ad5fc.complete"
    semantic_change:
      evidence.contradictory_information: true

expected_properties:              # the oracle — see §6/§7
  eventually: [case_resolved]
  must: [Activity_0oydey5]
  must_not: [Activity_1h9ix55]
  acceptable_routes: {Flow_0ybszcv: 1.0, Flow_1jgea85: 1.0}
  intervention: {required_after: [Flow_0ybszcv]}
```

### `initial_state` vs. `perturbations`

`initial_state` is what's true **before the first token is seeded** — the case's
own data (`process_variables`), any semantic facts known from the start
(`semantic`), and any BPS parameter change that should apply for the whole run
(`bps`). `perturbations` are changes that happen **during** execution, each tied to
a real BPMN element reaching `enter` (before it does its work) or `complete`
(after).

### The "thin semantic world"

`initial_state.semantic` and every perturbation's `semantic_change` become
`semantic.<dotted.key>` process variables — ordinary data a task/gateway/hook
policy can read, nothing the engine treats specially. Use this for context a
conventional BPS model can't naturally express: a document being missing, a
service being unavailable, contradictory evidence arriving, a customer
preference — exactly the design doc's own examples. There's no enforcement that a
policy actually *reads* a given semantic fact — if you reference one, make sure
the relevant policy's prose actually reasons about it, or the perturbation will
have no observable effect.

### What `bps_change` can actually do

Only one key shape has a real effect: **`activities.<element_id>.duration_multiplier`**
— it scales that task's configured duration for the rest of the run. This engine
doesn't model resource contention/capacity between cases (see README-SIMPN.md's
"Known gaps"), so anything else under `bps_change` — `resources.X.capacity`, etc. —
is recorded as `unsupported_bps_keys` in the trace rather than applied. If the idea
calls for "a service becomes unavailable," express it as a `semantic_change` fact
instead (e.g. `services.X.available: false`) and make sure a policy reads it.

### Writing a scenario by hand vs. with the LLM-authoring tool

Either write the YAML directly (as above), or describe the idea in plain English
and let an LLM draft it:

```bash
cuga-flo author-scenario loan_approval_simpn S04 \
    --idea "The applicant's credit check takes much longer than usual because the \
verification service is backed up, but they're otherwise a strong candidate who \
should still get approved once it completes." \
    --out applications/loan_approval_simpn/scenarios/S04-draft.yaml
```

This is grounded in the app's *real* element/flow/hook ids and variable names (so
the LLM can't reference something that doesn't exist) and checked for consistency
afterward — any invented id shows up as a warning on stderr. **Nothing is frozen
until you review it** — read the draft, fix anything off (the LLM can still invent
a semantically wrong value even when every id it used is real — see
README-SIMPN.md's worked example of exactly this happening and being fixed), then
rename away the `-draft` suffix once you're satisfied.

---

## 4. Configuring the number of simulation runs

Two independent dimensions, both flags on `cuga-flo run-scenario`:

- **`--configs G0,G1,G2,G3`** — which autonomy levels to include (a comma-separated
  subset is fine, e.g. `--configs G0,G3` to skip G1/G2). Each named level needs a
  matching `applications/<app>/config/<level>_config.yaml`.
- **`--repetitions N`** — how many times to run *each* config against this
  scenario (default 1). Useful once task/decision/hook reasoning is stochastic
  enough that one run isn't representative.

Every `(config, repetition)` pair is one fully independent run: its own subprocess
(no state — not even an in-memory cache — carries over from the previous
repetition), its own trace file. Building the G0–G3 variants is covered in §2;
concretely, for `loan_approval_simpn`, the four files differ only in
`tasks:`/`gateways:`/`hooks:`:

| File | `Activity_0oydey5` | `Gateway_09ad5fc` | hooks |
|---|---|---|---|
| `G0_config.yaml` | `mode: native` | `mode: native`, per-flow `condition:` | none |
| `G1_config.yaml` | `mode: task_agent` | `mode: native` | none |
| `G2_config.yaml` | `mode: task_agent` | `mode: decision_agent` | none |
| `G3_config.yaml` | `mode: task_agent` | `mode: decision_agent` | both |

G0's task has no conventional/deterministic scoring formula in this demo, so its
`credit_score` never leaves its 0.0 default — worth knowing before you're surprised
that G0 rejects every applicant regardless of scenario; that's the point, not a bug
(see README-SIMPN.md for the full reasoning).

---

## 5. Invoking the simulator

Two ways to run, for two different purposes.

### A single, interactive, manual run

For exploring one case by hand through the normal CugaSupervisor chat UI. You
manage the service lifecycle yourself — three terminals, same shape as the
Kogito/Flowable workflows in `CLAUDE.md`:

```bash
# terminal 1
./scripts/build_simpn_app.sh loan_approval_simpn
build/simpn/loan_approval_simpn/run.sh

# terminal 2
source .venv/bin/activate
export CUGA_FLO_TRACE_PATH=/tmp/run1.jsonl   # optional — see §6
cuga-flo start loan_approval_simpn
```

Then open <http://127.0.0.1:8001> and give it an applicant.

### An automated scenario, across G-levels, N repetitions

For the experiment this guide is really about. One command — it manages the
service's build/start/stop itself, one config at a time, on one reused port:

```bash
source .venv/bin/activate
cuga-flo run-scenario loan_approval_simpn \
    applications/loan_approval_simpn/scenarios/S02-regulatory-restriction.yaml \
    --configs G0,G1,G2,G3 --repetitions 3 \
    --trace-dir traces/ --csv traces/S02_runs.csv
```

This is genuinely unattended — it builds and tears down an isolated service venv
per config, runs every repetition through the real `FlowAgent` (each in its own
subprocess, so nothing leaks between repetitions — see README-SIMPN.md for why
that isolation turned out to matter, not just be tidy), and prints results when
done. Expect it to take a while: each config build is a fresh venv + `pip install`,
and every G1+ repetition makes real LLM calls.

---

## 6. Where results are captured and analysed

### The unified trace

Every run, however it was started, can produce one JSONL file — one JSON object
per line, each tagged with a `run_id`/`case_id`, an `event_type`, and a `source`
(`"cuga"` for the main-process side, `"simpn"` for the Simulation Runtime
Service). For a manual run (§5A), set `CUGA_FLO_TRACE_PATH` before `cuga-flo
start`. For `run-scenario` (§5B), one trace file per `(scenario, config,
repetition)` is written automatically under `--trace-dir`, named
`<scenario_id>__<config>__rep<N>.jsonl`.

### The deterministic evaluator

`cuga-flo evaluate <trace.jsonl> [--expected <properties.yaml>] [--run-id ID]`
reads one trace and prints a JSON **scorecard**:

```json
{
  "run_id": "...",
  "process_completed": true,
  "cycle_time_seconds": 17.5,
  "activity_count": 6,
  "agent_task_invocations": 1,
  "agent_gateway_invocations": 1,
  "agent_hook_invocations": 2,
  "hook_target_fallbacks": 0,
  "policy_compliant": true,
  "goal_achieved": true,
  "decision_utility": 1.0,
  "intervention_precision": 1.0,
  "intervention_recall": 1.0,
  "intervention_f1": 1.0,
  "not_yet_computable": {"perturbation_recovered": "..."}
}
```

The first group (`process_completed` through `hook_target_fallbacks`) is always
computed from the trace alone. `goal_achieved`, `decision_utility`, and
`intervention_*` need an `expected_properties` oracle — either a standalone file
(§2) for a manual run, or the scenario's own `expected_properties:` block,
automatically, for `run-scenario`. `not_yet_computable` lists anything the tool
deliberately refuses to guess at rather than silently reporting a wrong number —
check that dict before trusting a metric's absence means "zero."

### Comparative analysis across the matrix

`run-scenario` runs the evaluator for you, once per `(config, repetition)`, and
layers three more views on top automatically:

1. The **per-run comparison table** (every repetition, one row each).
2. **Per-config aggregates** — Goal Achievement Rate, mean decision utility, mean
   intervention F1, mean cycle time, across that config's repetitions.
3. The **G0→G1→G2→G3 ablation** — the change in Goal Achievement Rate at each
   step, the direct answer to "what did adding this layer of autonomy buy us?"

`--csv PATH` additionally writes one row per `(config, repetition)` with every
scorecard field — the raw material for anything beyond what's printed (a pandas
notebook, a spreadsheet, a proper statistical model once you have enough
repetitions to justify fitting one — see README-SIMPN.md's "Comparative analysis"
section for exactly what's intentionally not built yet and why).

---

## 7. Reviewing analysis results

### Reading the terminal output

A real run of S02 (`applicant_id: "4321"`, a regulatory restriction only a hook can
catch) across all four configs produced the comparison table below; the aggregate
and ablation views are computed from that same real data (shown together here, as
`run-scenario` now prints all three in one pass):

```
config rep  goal   util   interv_f1  cycle_s
-----------------------------------------------
G0     0    False  1.0    0.0        0.0
G1     0    False  1.0    0.0        20.0
G2     0    False  1.0    0.0        12.1
G3     0    True   1.0    1.0        17.5

config n    GAR    mean_util  mean_interv_f1  mean_cycle_s
G0     1    0.00   1.00       0.00            0.00
G1     1    0.00   1.00       0.00            20.00
G2     1    0.00   1.00       0.00            12.10
G3     1    1.00   1.00       1.00            17.50

Ablation (ΔGAR — Goal Achievement Rate):
  G0 -> G1: +0.00
  G1 -> G2: +0.00
  G2 -> G3: +1.00
  G0 -> G3: +1.00
```

Reading it: **all three of G0/G1/G2 fail identically, and for the same reason.**
G0's `check credit` task now runs a real (if crude) native formula — see §2's note
on `formula:` — so it scores this applicant's profile a real credit_score too,
same as G1/G2's LLM-driven score, and approves on it exactly the way they do. None
of the three has a hook, so all three wrongly grant the restricted loan:
`goal=False`, `intervention_f1=0.0` across the board — the required intervention
never had a hook to fire through in any of them. Only G3 adds the hook, and its
`intervention_f1=1.0` proves the catch was genuine, not incidental. The whole
`ΔGAR` gain is concentrated in the `G2 -> G3` step — exactly the point of this
scenario: it isolates **oversight autonomy specifically**, with task and decision
autonomy alone (G1, G2) contributing nothing to this particular failure mode.

This wasn't always this clean a story. Before `G0_config.yaml` gained its
`formula:` field, G0's `credit_score` sat at its 0.0 default for every applicant,
so G0 rejected everyone unconditionally — "succeeding" on this scenario by
accident, for a reason unrelated to oversight, which needed real caveat-reading to
interpret correctly (`goal=True` but `intervention_f1=0.0` telling two different
stories). Giving G0 a real, if crude, scoring rule made it fail for the *same
legible reason* G1/G2 do, which is arguably the more honest baseline: a real
legacy system's limits should show up as *missing capability* (here, oversight),
not as a side effect of not being able to do arithmetic at all.

### Loading the CSV for more

```python
import pandas as pd
df = pd.read_csv("traces/S02_runs.csv")
df.groupby("config")[["goal_achieved", "cycle_time_seconds"]].mean()
```

Every `Scorecard` field is a column, correctly typed once pandas parses it. This is
the point to reach for a confidence interval, a plot across more scenarios, or
(once you have enough repetitions to justify it — see §6) a real statistical model.

### What's still out of scope for review

- **`perturbation_recovered`** always shows up in `not_yet_computable` — there's
  no "nominal vs. perturbed" pairing mechanism yet, so there's nothing to call
  "recovered."
- **No fitted mixed-effects model, no `AutonomyLevel × PerturbationSeverity`
  interaction test** — the aggregates/ablation above are descriptive statistics,
  not a regression; see README-SIMPN.md for exactly why and what's missing
  (mainly: enough repetitions, and a severity field the scenario schema doesn't
  have).
- **A scorecard is only as good as its `expected_properties`.** `goal_achieved`
  and `decision_utility` reflect whatever oracle you wrote — a loosely-specified
  `acceptable_routes` or an empty `must_not` will under-report real problems. When
  a result looks surprising, check the scenario's oracle before concluding the
  system behaved wrongly.

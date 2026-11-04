> **Status: proposed, not yet implemented.** Grounded in the real as-built code —
> `BPMNdiagram.bpmn`'s own DI layout, `loan_approval_simpn`'s actual G1/G2/G3
> config files, policy files, and scenario YAMLs — checked against the real
> files before writing this, not assumed. Three open design questions were
> put to the user before finalizing this plan; their answers are recorded
> inline below and already applied throughout.

# Human-Baseline Survey Materials (paper-and-pencil)

## Context — what this is for

Everything built so far (`run-scenario`, the evaluator, the time/cost plan)
measures how the *agentic* system (G1-G3) performs. This plan generates
**printable survey materials that let a human stand in for the agent** at
each agentic control point — a task, a gateway decision, or a hook
evaluation — for a given configuration and scenario, so a human's answer can
later be compared against the real agent's actual answer at that same
point. This is a materials-generation task only: producing the documents.
Administering the survey and scoring/comparing answers against the real
agent's decisions is a separate, later step (explicitly out of scope below).

Scoped to `loan_approval_simpn` throughout, the worked example used by every
other plan in this repo. It has exactly one agentic task (`Activity_0oydey5`,
"check credit"), one agentic gateway (`Gateway_09ad5fc`, approve/reject), and
two hooks (on edges `Flow_1e5ztf6` and `Flow_0ybszcv`) — confirmed by reading
`applications/loan_approval_simpn/config/G1_config.yaml`/`G2_config.yaml`/
`G3_config.yaml` directly. The autonomy ladder's agentic scope is strictly
additive and already fixed by these files:

| Config | Agentic elements |
|---|---|
| G0 | none — nothing to survey |
| G1 | `Activity_0oydey5` (task) only |
| G2 | + `Gateway_09ad5fc` (gateway) |
| G3 | + both hooks (`Flow_1e5ztf6`, `Flow_0ybszcv`) |

G0 is excluded from every survey — there is nothing agentic to ask a human
about. "G1-G3 configurations × scenario" (the request's own framing) is
3 configs × the app's 5 existing frozen scenarios (`S01`-`S05`) = **15
survey documents**, by default.

## Design decisions (resolved with the user before writing the implementation plan)

1. **Facts shown: the scenario's initial input only, identical at every
   station.** Asked whether a downstream station (e.g. the gateway) should
   see the real `credit_score` a reference run actually produced, the user
   said no: **every station in a document is given only `initial_state.
   process_variables`/`semantic` — the same static snapshot — and is asked
   independently about its own task/decision/hook.** This removes any need
   for a real reference run, `TraceRun`, or a "known facts evolve as you go"
   reconstruction — a significant simplification over the first draft of
   this plan. One direct consequence, stated here rather than left implicit:
   **mid-run `perturbations:` entries (a fact that only becomes true partway
   through a real run, e.g. `S05-slow-credit-check.yaml`'s
   `evidence.contradictory_information` set at `Gateway_09ad5fc.complete`)
   are never reflected** — the survey presents one static snapshot, not a
   simulated timeline, so a scenario whose differentiator is entirely a
   mid-run perturbation will show the same initial-state facts at every
   station regardless. Flagged as a known, deliberate scope boundary.
2. **Configuration label shown plainly** — a document's header says
   "Configuration: G2" (etc.) outright, not blinded.
3. **Task question: the numeric score only** — `credit_score` (0.0-1.0),
   matching `output_mapping` exactly, no added qualitative sanity-check field.

## What's real and already built, that this plan reuses rather than reinvents

- **BPMN visual layout is already in the file.** `BPMNdiagram.bpmn`'s
  `<bpmndi:BPMNShape>`/`<bpmndi:BPMNEdge>` section (confirmed: 40 DI elements)
  gives absolute `x/y/width/height` bounds for every shape and a waypoint
  list for every edge — enough to draw a faithful diagram with no new
  rendering dependency. `cuga_flo.engine.bpmn_parser.BPMNParser`/
  `BPMNProcess`/`BPMNElement`/`BPMNFlow` (`src/cuga_flo/engine/bpmn_parser.py:
  26-47`) already gives element id/name/type and flow source/target/
  condition, but *not* the DI bounds — this plan adds a small, separate
  DI-section parser alongside it (same `xml.etree.ElementTree`, no new
  dependency) rather than extending the engine's own parser with rendering
  concerns it has no other reason to carry.
- **Policies are already one markdown file per agentic element**, referenced
  by relative path directly in each config (`policy: "../policies/task-
  credit_check.md"`, etc. — confirmed in `G3_config.yaml:42,48,68,72`). No new
  policy format needed; read the files the config already points at.
- **`action_permissions` already declares the exact hook-action vocabulary**
  per config (`G3_config.yaml:55-62`: `permitted_actions: [continue, skip_to,
  terminate]`, `prohibited_actions: [skip_node, swap_nodes]`) — the hook
  question's multiple-choice options come directly from this, not a fixed
  list this plan invents.
- **Scenario YAMLs already carry "the input process variable values
  corresponding to the scenario"** — `initial_state.process_variables` (the
  case facts) and `initial_state.semantic` (facts present from the start),
  confirmed in `S02-unverifiable-id.yaml`/`S04-regulatory-restriction.yaml`.
  Per design decision 1, this is the *only* facts section a survey shows.
- **The gateway's own `flows:`/`decision:` text already labels its
  options** (`Gateway_09ad5fc.flows.Flow_0ybszcv.decision: "Approve — credit
  score sufficient"`, etc., `G3_config.yaml:49-53`) — the gateway question's
  multiple-choice labels come directly from this, not invented wording.

## Design

### One document per (scenario, config); a sequence of stations inside it

Matches the request's own structure literally: items 1-3 (process model,
policies, scenario input) describe the *whole configuration*, item 4 ("a
question... concerning the respective task/decision/hook") becomes one
question per agentic element present in that configuration — so a G3
document has 4 stations (task, gateway, hook, hook), a G2 document has 2, a
G1 document has 1.

**Station order** is the BPMN's own topological order — task(s) before the
gateway they feed, the gateway before the hooks on its outgoing edges — read
directly from each config's `tasks:`/`gateways:`/`hooks:` declaration order
(which already follows the model's real flow for this app) together with
`BPMNFlow.source_ref`/`target_ref` to place hooks after the gateway whose
edge they sit on. No run of the simulator is needed to determine this order,
since (per design decision 1) nothing about a later station depends on an
earlier one's answer.

### Each station shows

1. **The annotated process model** — one diagram per *configuration* (shared
   across all 5 scenarios' documents for that config, since the BPMN model
   and each config's agentic scope don't change per scenario), with the
   *current* station's element highlighted distinctly from the config's
   other agentic elements (e.g. current = solid red border, other-agentic-
   in-this-config = dashed amber border, non-agentic = plain).
2. **That element's policy**, read verbatim from the file its config entry
   points at.
3. **The scenario's initial input** — `initial_state.process_variables` and
   `initial_state.semantic` (the latter's `semantic.` prefix stripped for
   display), rendered once per document (not per station, since it's
   identical at every station per design decision 1) and referenced from
   each station section.
4. **One question, shaped by element type:**
   - **Task** (`Activity_0oydey5`): free-response — "what `credit_score`
     (0.0-1.0) would you assign?" (numeric only, per design decision 3).
   - **Gateway** (`Gateway_09ad5fc`): multiple-choice over its real declared
     `flows:` (labels taken verbatim from each flow's own `decision:` text),
     plus a reasoning line.
   - **Hook**: multiple-choice over *that config's own*
     `action_permissions.permitted_actions` only (never an action the
     deployed system itself couldn't take), a target field for `skip_to`,
     plus a reasoning line.

### Output format: self-contained, printable HTML — no new dependency

One `.html` file per (scenario, config), with the diagram as inline SVG
(hand-built from the DI bounds — plain string-built SVG markup, no rendering
library) and print-friendly CSS (`@media print` page breaks between
stations, blank ruled answer areas). "Print to PDF" from any browser is the
PDF path if one is wanted — no `weasyprint`/`reportlab`/pandoc dependency
needed for a first pass.

## Implementation

New package `src/cuga_flo/surveys/`:

- **`diagram.py`** — parses `BPMNdiagram.bpmn`'s `<bpmndi:...>` section into
  `{element_id: (x, y, w, h)}` and `{flow_id: [(x, y), ...]}`, independent of
  `BPMNParser` (which doesn't carry DI bounds). `render(process: BPMNProcess,
  shapes, edges, highlight: dict[str, str]) -> str` draws one SVG string —
  rects for tasks, diamonds for gateways, circles for events, lines+arrowheads
  for flows, each `highlight`-ed element given a distinct stroke/badge.
- **`stations.py`** — `build_stations(config: FlowConfig, bpmn: BPMNProcess)
  -> list[Station]`. A `Station` dataclass: `element_id`, `element_type`
  (`"task"`/`"gateway"`/`"hook"`), `policy_text` (read from the config's own
  `policy:` path), `question: QuestionSpec` (gateway's flow labels, or that
  config's own `action_permissions.permitted_actions` for a hook). Pure
  function of one config file plus the BPMN model — no trace, no simulator
  run, no scenario-specific state (the scenario only supplies the initial
  input shown once per document, handled in `render.py`).
- **`render.py`** — `write_survey(app_name, scenario: Scenario, config_name:
  str, stations: list[Station], svg: str, out_path: Path)`: one
  self-contained HTML file — header (scenario id/description, plainly
  labeled configuration per design decision 2), the diagram, the scenario's
  initial input table once, then one `<section>` per station (policy,
  question, ruled answer area), print CSS.
- **`cli.py`** — `generate(app_name: str, scenario_paths: list[str], configs:
  list[str], out_dir: str)`: for each (scenario, config) pair, loads the
  scenario YAML (`cuga_flo.scenarios.runner.load_scenario`) and that config's
  `FlowConfig`/`BPMNProcess` (same loaders `cuga_flo.scenarios.analysis.
  _activity_columns` already uses), builds stations once per config (cached
  across scenarios, since `stations.py`'s output doesn't depend on the
  scenario), renders, writes to `out_dir/<scenario_id>__<config>.html`.
- New `cuga-flo generate-survey-materials <app> <scenario_path_or_dir>
  --configs G1,G2,G3 --out-dir surveys/<app>/` command in `src/cuga_flo/cli/
  __init__.py`, lazy-imported like every other subcommand there. No
  dependency on `run-scenario` having been run first — this command only
  reads config/BPMN/policy/scenario files, matching design decision 1's
  removal of any reference-trace requirement.

### Tests

New `tests/test_survey_materials.py`:
- one test asserting `build_stations()` for `G3_config.yaml` returns exactly
  4 stations in the order task → gateway → hook(`Flow_1e5ztf6`) →
  hook(`Flow_0ybszcv`), and for `G1_config.yaml` returns exactly 1 (the task
  only);
- one test asserting the hook station's question options equal that
  config's own `action_permissions.permitted_actions` list, not a hardcoded
  one;
- one test asserting the gateway station's question labels equal the
  config's own `flows.<id>.decision` text verbatim;
- one test for `diagram.py` parsing a small hand-written DI fragment and
  rendering an SVG whose string output contains the expected highlighted
  element's id and a distinct style for it vs. a non-highlighted element;
- one test asserting the rendered HTML for the same scenario across G1/G2/G3
  shows byte-identical "Scenario's initial input" tables (design decision 1
  — nothing about the shown facts should vary by configuration).

## Verification plan (once implemented)

1. Generate all 15 documents (`S01`-`S05` × `G1`-`G3`) and open a sample —
   `S04×G3`, `S05×G3` — in a browser. Confirm the diagram's highlight set
   matches that config's real agentic scope from the table above, confirm
   the hook question's choices for G3 are exactly `continue`/`skip_to`/
   `terminate` (not `skip_node`/`swap_nodes`), confirm the gateway's two
   options read "Approve — credit score sufficient"/"Reject — credit score
   insufficient" verbatim from the config.
2. Confirm `S05`'s document shows nothing about `evidence.
   contradictory_information` anywhere (per design decision 1 — that fact
   only exists as a mid-run perturbation, never part of `initial_state`).
3. Print-preview one document to confirm page breaks land between stations,
   not mid-section.

## Explicitly out of scope

- Administering the survey, collecting human answers, or scoring them
  against the real agent's decisions — a separate downstream analysis step
  once materials exist.
- Reflecting mid-run `perturbations:` effects in survey content — see design
  decision 1's own stated consequence.
- Any app other than `loan_approval_simpn` — not attempted until proven on
  this worked example.
- Multi-task/multi-gateway/parallel-topology diagrams — this app has exactly
  one of each agentic element type, so `diagram.py`'s highlight logic and
  `stations.py`'s ordering don't need to handle ambiguous "which instance"
  cases yet.

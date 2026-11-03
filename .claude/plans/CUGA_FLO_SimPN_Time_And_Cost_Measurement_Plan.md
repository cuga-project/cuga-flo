> **Status: implemented and live-verified.** Every part below (1a, 1a-extra, 1b,
> 1c, 2a, 2b, 2c, 2d) is built, covered by unit tests (`tests/test_simpn_adapter.py`),
> and confirmed against real `loan_approval_simpn` runs: `simulated_cost`/
> `agentic_cost`/`total_cost` computed correctly across G0 (native-only cost,
> confirmed `$0` agentic rather than `not_yet_computable`) and G1 (adds real
> token cost on top of the same native cost); the exponential-distribution
> sampler verified to produce genuinely varying `simulated_cycle_time_seconds`
> across repetitions; `--csv`/`--report` both carry the new columns
> automatically. `applications/loan_approval_simpn/config/simpn_parameters.yaml`
> now carries illustrative `hourly_rate`/`model_token_cost` values.

# Time and Cost Measurement for Simulation Experiments

## Context

The current trace and evaluator capture *whether* and *how long the whole run
took* (`cycle_time_seconds`, real wall-clock; `simulated_cycle_time_seconds`, the
BPS-simulated sum — Milestone 8), but nothing breaks that down by **where the
time went**, and nothing captures **cost** at all — neither the native
SimPN-simulated side nor the agentic (real LLM) side. Concretely, as of this
writing:

- `ACTIVITY_COMPLETE` events already carry a `duration` field (the task's
  *simulated* duration — [engine.py's `make_task_behavior`](../../src/cuga_flo/adapters/simpn/runtime/engine.py)),
  but this is the same number regardless of whether the task was agentic or
  native — it's `simpn_parameters.yaml`'s configured duration, not the real
  wall-clock time an agentic call actually took.
- `GATEWAY_RESOLVED` events carry **no duration at all** — `make_gateway_behavior`
  returns `SimToken(state)` with no `delay=`, so a gateway resolves in zero
  simulated time, always, whether native or agentic.
- `AGENT_TASK_COMPLETED` / `AGENT_GATEWAY_ROUTED` / `AGENT_HOOK_EVALUATED`
  (emitted by `flow_agent.py`'s `_handle_task` / `_handle_gateway` / `_handle_hook`)
  carry status/routing/action info but **no timing and no token count** — the
  real wall-clock cost of an individual agentic call is invisible; only the
  *whole run's* `cycle_time_seconds` hints at it, with no per-call breakdown.
- `simpn_parameters.yaml` has exactly one field per task, `duration_seconds`
  (confirmed against `applications/loan_approval_simpn/config/simpn_parameters.yaml`)
  — no gateway entries, no rate/cost fields of any kind, native or agentic.
- There is no cost concept anywhere in the pipeline — not in the trace, not in
  `Scorecard`, not in `--csv`, not in `--report`.

**A real, already-wired mechanism this plan leans on:** `cuga.backend.
activity_tracker.tracker.ActivityTracker` is a process-wide **singleton**
(`__new__` returns the shared `_instance` — verified in the installed package).
`flow_agent.py` already imports it (`tracker = ActivityTracker()`, same shared
instance). Separately, cuga-agent's own LangChain callback handler
(`agent_loop.py`'s `on_llm_end`) already calls `tracker.collect_tokens_usage(...)`
on **every** real LLM call made anywhere in the process, accumulating a running
`tracker.token_usage` total. This means a per-call token count is obtainable
today with **zero new instrumentation inside cuga-agent**: snapshot
`tracker.token_usage` immediately before and after each agentic call in
`flow_agent.py`, and the delta is that call's token count. This plan uses that
mechanism rather than inventing a new one.

## Scope

Two measurement dimensions — **time** and **cost** — each touching the same
five layers: (a) the SimPN-native path, (b) the CUGA-FLO-agentic path, (c) the
trace event schema, (d) evaluator aggregation (`TraceRun`/`Scorecard`), (e)
report rendering (`--csv` columns, `--report`'s markdown, including its
Footnotes glossary — Milestone 11).

**Design principle carried over from every prior milestone:** the trace records
*raw observed facts* (durations, token counts); the *rate* that turns a raw
fact into a cost (an hourly rate, a token price) lives in config
(`simpn_parameters.yaml`), not in the trace event itself. The evaluator is what
multiplies fact × rate into a cost, the same way it already turns raw trace
facts into `goal_achieved`/`decision_utility` by combining them with an
`expected_properties` oracle. This keeps the trace schema engine-agnostic and
re-analyzable if a rate changes later, and mirrors the existing not_yet_computable
discipline: **no configured rate means the cost metric is `not_yet_computable`,
never a silently-fabricated `0.0`.**

**Open design question this plan flags rather than resolves unilaterally:** the
phrase "aggregative costs should also be added in the traces" could instead mean
literally writing a computed cost number into each trace event (requiring the
rate to be available at trace-write time — on the SimPN-engine side that's easy,
`simpn_parameters.yaml` is already loaded there; on the CUGA-FLO-agentic side it
is **not**, since `flow_agent.py` runs in the main CUGA FLO process/venv and has
no existing path to the SimPN service's `simpn_parameters.yaml` without crossing
the dependency-isolation boundary the whole adapter is built to respect — see
`cuga_flo.adapters.simpn.runtime`'s package docstring). This plan's design
instead keeps raw facts (durations, token counts) in the trace and computes
cost downstream in the evaluator, which already runs in the main process and
can read the app's config directly (the same way `analysis._activity_columns`
already reads the app's BPMN file by `app_name` — Milestone 6). If a literal
per-event cost figure turns out to be the actual requirement, that's a
straightforward variant of Part 2 below, not a different architecture.

---

## Part 1 — Time

### 1a. Native SimPN decision duration (parallel to the existing task duration)

Tasks already have a configurable, traced duration. Gateways don't. Add the
same mechanism to gateways:

- `simpn_parameters.yaml` gains an optional `gateways:` section, parallel to
  `tasks:`:
  ```yaml
  tasks:
    Activity_0oydey5:
      duration_seconds: 2
  gateways:
    Gateway_09ad5fc:
      duration_seconds: 0.5
  ```
  Omitted entirely (as today) means duration `0` — zero behavior change for
  every existing app/scenario that doesn't add this section.
- `engine.py`'s `_duration(task_id, simpn_params)` helper (currently:
  `(simpn_params.get("tasks", {}).get(task_id, {}) or {}).get("duration_seconds",
  _DEFAULT_DURATION_SECONDS)`, i.e. defaults to `_DEFAULT_DURATION_SECONDS` =
  `1.0` when a task is unconfigured) is generalized to
  `_duration(element_id, simpn_params, section="tasks", default=_DEFAULT_DURATION_SECONDS)`.
  `make_gateway_behavior` calls it with `section="gateways", default=0.0` —
  **not** the task default — so a gateway with no `gateways:` entry at all
  (every existing app today, including `loan_approval_simpn`) keeps resolving
  in zero simulated time exactly as now. Only a gateway explicitly listed under
  a new `gateways:` section gets a nonzero duration. This avoids the trap of
  reusing the task default and silently changing every existing gateway's
  behavior the moment this ships.
- `make_gateway_behavior`'s `behavior` closure computes `duration` the same way
  `make_task_behavior` already does. The underlying `state["_duration_overrides"]`
  dict is keyed by raw element id, so it technically applies to a gateway's id
  unchanged — but the perturbation key a scenario author writes is matched by
  `perturbations.py`'s regex `^activities\.(.+)\.duration_multiplier$`, i.e.
  the word "activities" is hardcoded into the key's namespace, BPMN-speak for
  tasks specifically. Reusing that same prefix for a gateway's duration would
  work mechanically but read confusingly in a scenario YAML
  (`activities.Gateway_09ad5fc.duration_multiplier`, naming a gateway as if it
  were a task). This plan proposes widening the regex to also accept
  `gateways.<id>.duration_multiplier` as a second, parallel key — a small,
  additive change to `perturbations.py`, not a rename of the existing one
  (`activities.*` keeps meaning what it already means, for every existing
  scenario). The `behavior` closure returns `SimToken(state, delay=duration)`
  instead of today's bare `SimToken(state)`.
- `GATEWAY_RESOLVED`'s trace payload gains `"duration": duration`, mirroring
  `ACTIVITY_COMPLETE`'s existing field exactly.

### 1a-extra. Duration distributions, not just flat values

Checked against the installed SimPN library before proposing this, not
assumed: SimPN ships **no distribution library of its own** — `numpy` is only
a `dev` extra in its declared dependencies, and there is no
`random.expovariate`/`gauss`/etc. call anywhere in SimPN's own source. But
SimPN's own idiom for exactly this already exists — `BPMNStartEvent`'s
`interarrival_time` parameter explicitly accepts *"a numeric value or a
function that produces a numeric value, such as a sampling function from a
random distribution"* ([prototypes.py:445](../../.venv/lib/python3.12/site-packages/simpn/prototypes.py#L445)),
normalizing a flat number to `lambda: value` internally. More fundamentally,
`SimToken`'s `delay=` is just a plain number at token-construction time — the
**behavior function** (ours, not SimPN's) computes it however it likes, each
time it fires, before constructing the token. There is no SimPN-side obstacle
to sampling; our own `_duration()` is what's flat today, by choice, not by any
constraint the library imposes.

Given that, this plan adds distribution support to `simpn_parameters.yaml`
now rather than filing it as a later gap:

- Each task/gateway entry may specify **either** the existing flat
  `duration_seconds`, **or** a new `duration:` block naming a distribution —
  never both (a config with both set is a validation error at build time, not
  a silent "one wins"):
  ```yaml
  tasks:
    Activity_0oydey5:
      duration_seconds: 2                # flat — unchanged, still the default form
    Activity_1h9ix55:
      duration:
        distribution: exponential
        mean: 1.5
  gateways:
    Gateway_09ad5fc:
      duration:
        distribution: triangular
        low: 0.2
        mode: 0.5
        high: 1.0
  ```
- A small, **stdlib-only** sampler registry in `engine.py` (no new dependency
  — `random` is already stdlib, consistent with this package's existing
  "no `cuga_flo` import, minimal dependencies" discipline):
  ```python
  _SAMPLERS: dict[str, Callable[[dict], float]] = {
      "exponential": lambda p: random.expovariate(1 / p["mean"]),
      "normal": lambda p: max(0.0, random.gauss(p["mean"], p["stddev"])),
      "triangular": lambda p: random.triangular(p["low"], p["high"], p.get("mode")),
      "uniform": lambda p: random.uniform(p["low"], p["high"]),
  }
  ```
  Four distributions — the common BPS set (matching what tools like Prosimos
  typically offer: fixed/exponential/normal/triangular/uniform), not an
  attempt to cover every `random`/`numpy` distribution. An unrecognized
  `distribution` name is a build-time validation error, the same "fail loud,
  not silently" discipline as the both-fields-set case above. `normal` is
  clipped at `0.0` — a negative duration is nonsensical — documented with a
  `ponytail:` comment at the clip site, since clipping a Gaussian shifts its
  effective shape slightly near zero; a real limitation worth naming, not
  hiding.
- `_duration()` is extended once more: if the entry has a `duration:` block,
  dispatch to `_SAMPLERS[spec["distribution"]](spec)`; otherwise fall back to
  today's `duration_seconds`-or-`default` lookup (1a). Called fresh on every
  firing (same call site as today — `make_task_behavior`/`make_gateway_behavior`
  already call `_duration()` once per behavior invocation, so a distribution
  resamples on every case exactly as "frozen scenario, stochastic underlying
  behavior" already assumes for agentic calls — see `docs/README-SIMPN.md`'s
  existing framing on why `--repetitions` exists at all).
- **No trace or evaluator changes needed beyond what 1a/1c already specify.**
  The trace already records the *realized* duration for that specific firing
  (`ACTIVITY_COMPLETE`/`GATEWAY_RESOLVED`'s `duration` field), not the
  distribution spec that produced it — so a sampled duration flows through
  `simulated_cycle_time_seconds()` and `simulated_cost` exactly like a flat one
  already does, with the randomness fully visible across repetitions rather
  than needing any new plumbing.
- **Deliberately scoped to duration only, not cost rates.** `hourly_rate` and
  `model_token_cost` (Part 2) stay flat-only in this plan — a stochastic *rate*
  (as opposed to a stochastic *duration*, which legitimately varies case to
  case) is a much rarer real need, and would roughly double this section's
  surface for a case this plan doesn't have a concrete use for yet. Naming it
  here so the omission is a visible decision, not an oversight — the same
  `duration:`-block mechanism would extend to rates with no new design needed
  if a real need for it shows up.

### 1b. Agentic wall-clock duration (task, decision, *and* hook)

`flow_agent.py`'s three MCP-tool handlers each wrap their one real
agent-invocation line with a timer:

```python
start = time.monotonic()
result = await agent.execute(ctx.current_state, task_input)
agent_duration_seconds = time.monotonic() - start
```

(symmetrically for `agent.route(...)` in `_handle_gateway` and
`self._llm_hook_decision(hook, ctx)` in `_handle_hook`). The existing
`AGENT_TASK_COMPLETED` / `AGENT_GATEWAY_ROUTED` / `AGENT_HOOK_EVALUATED` trace
calls each gain one new payload key, `"duration_seconds": agent_duration_seconds`
— no new event types, just one more field on events that already exist and are
already traced on every agentic call.

### 1c. Evaluator aggregation

`TraceRun` (`cuga_flo/evaluation/trace_run.py`) gains:

- `simulated_cycle_time_seconds()` (Milestone 8) is **extended**, not replaced:
  it currently sums only `ACTIVITY_COMPLETE`'s `duration`; it now also sums
  `GATEWAY_RESOLVED`'s new `duration` field, so it genuinely represents "total
  simulated BPS time across the whole case," tasks and gateways together.
- New `total_agent_duration_seconds()`: sums the new `duration_seconds` field
  across every `AGENT_TASK_COMPLETED`, `AGENT_GATEWAY_ROUTED`, and
  `AGENT_HOOK_EVALUATED` event in the run. `None` (not `0.0`) if no agentic
  event fired at all — same "unknown vs. genuinely zero" discipline as every
  other metric in this evaluator.

`Scorecard` (`cuga_flo/evaluation/metrics.py`) gains one new field,
`total_agent_duration_seconds: float | None`, populated by `evaluate()` the
same way `simulated_cycle_time_seconds` already is. Being a plain dataclass
field, it's automatically picked up by `--csv` (`_SCORECARD_FIELDS` iterates
`dataclasses.fields(Scorecard)` generically — no `analysis.py` change needed)
and by `cuga-flo evaluate`'s JSON output.

Net effect: for any run, `cycle_time_seconds` (real wall-clock) splits into
roughly `simulated_cycle_time_seconds` (time the simulator says the process
took) and `total_agent_duration_seconds` (time actually spent waiting on real
LLM calls) — the gap between those two and the real `cycle_time_seconds` is
scheduling/transport overhead, now visible for the first time rather than
buried in one opaque number.

---

## Part 2 — Cost

### 2a. Native SimPN execution cost (task + decision)

- `simpn_parameters.yaml`'s `tasks:` and (new, from 1a) `gateways:` entries
  each gain an optional sibling field, `hourly_rate` (currency units per hour,
  unitless/whatever the researcher's own convention is — this plan doesn't
  impose a currency):
  ```yaml
  tasks:
    Activity_0oydey5:
      duration_seconds: 2
      hourly_rate: 45
  ```
- No per-event trace changes needed beyond what 1a/1b already add — cost here
  is `(duration_seconds / 3600) * hourly_rate`, computable from facts already
  in the trace (duration) plus the rate (read from `simpn_parameters.yaml` at
  evaluation time — see below). Consistent with this plan's stated design
  principle: the trace keeps the raw duration; cost is derived downstream, not
  baked into the event.

### 2b. Agentic invocation cost (task, decision, hook)

- `flow_agent.py`'s three handlers additionally snapshot
  `tracker.token_usage` immediately before and after the agent call (the same
  `time.monotonic()`-style before/after pattern as 1b, reusing the already-
  verified `ActivityTracker` singleton mechanism from this plan's Context
  section) and include the delta as a new payload key, `"num_of_tokens":
  tokens_after - tokens_before`, on the same three existing `AGENT_*` trace
  events.
  - Caveat worth stating plainly: `ActivityTracker` is one process-wide
    counter. Since `FlowAgent`'s control-point execution is strictly sequential
    (one agentic call resolves fully before the next control point is even
    reached — verified against `_handle_task`/`_handle_gateway`/`_handle_hook`,
    none of which run concurrently with each other inside one case), the
    before/after delta is not corrupted by overlapping calls *within one run*.
    It would be, if a future change made two agentic calls run concurrently in
    the same process — a real but currently-inapplicable caveat worth a
    ponytail comment at the snapshot site.
- `simpn_parameters.yaml` gains one new **top-level** field (not per-task,
  since one `MODEL_NAME` applies to the whole run today — see `.env`),
  `model_token_cost` (currency units per token):
  ```yaml
  model_token_cost: 0.000003
  tasks:
    ...
  ```
- Agentic cost for one call = `num_of_tokens * model_token_cost`, computed
  downstream the same way native cost is (2a) — `num_of_tokens` is the raw
  fact in the trace; `model_token_cost` is the rate in config; the evaluator
  combines them.

### 2c. Evaluator aggregation — the part that needs new plumbing

Unlike every other metric this evaluator computes, cost is the **first one
that needs information from outside the trace file** (the rate). Concretely:

- `evaluate(trace_path, expected=None, run_id=None)` in `metrics.py` currently
  takes no app/config reference at all. It needs a new optional parameter,
  e.g. `rates: dict | None = None` (or `simpn_parameters_path: str | None`,
  loaded internally) — when `None`, every cost-derived `Scorecard` field reports
  `not_yet_computable` with reason `"no simpn_parameters.yaml rates supplied"`,
  never a fabricated `0.0`.
- `cuga-flo evaluate`'s CLI (`cuga_flo/evaluation/cli.py`) gains a new
  `--simpn-parameters PATH` option threading through to this new parameter —
  for a manual single-trace evaluation, the researcher points at the app's
  config file explicitly, the same way `--expected` already points at an
  oracle file.
- `cuga_flo.scenarios.runner.run_repetition` (which already knows `app_name`
  and constructs the SimPN service's config path via `ServiceHandle`) passes
  the app's `simpn_parameters.yaml` path into `evaluate()` automatically — no
  new CLI flag needed for `run-scenario`, since the app is already known there.
- New `Scorecard` fields: `simulated_cost: float | None` (native task + gateway
  cost, summed) and `agentic_cost: float | None` (token cost, summed), each
  `not_yet_computable` rather than `0.0` when the relevant rate(s) are absent
  for *any* activity/gateway/call involved (not just all-or-nothing — if only
  some tasks have `hourly_rate` set, `simulated_cost` is still computable for
  the ones that do, `not_yet_computable` only if *none* do, consistent with how
  `decision_utility`/`intervention_f1` already handle partial applicability).
- A third convenience field, `total_cost: float | None` =
  `simulated_cost + agentic_cost` when both are known, `None` if either is
  unknown — avoiding the same "reporting 0 because one half is missing" trap.

### 2d. Report rendering

- `--csv`: all five new `Scorecard` fields (`total_agent_duration_seconds`,
  `simulated_cost`, `agentic_cost`, `total_cost`, plus the gateway-duration
  contribution already folded into `simulated_cycle_time_seconds`) appear
  automatically, same mechanism as every prior `Scorecard` field addition —
  no `analysis.py` change needed for the CSV path.
- `--report`'s markdown (`analysis.write_report`, Milestone 11): the per-scenario
  **Per-config aggregates** table gains two columns, `mean_cost` and
  `mean_agent_duration_s`, computed by extending `ConfigAggregate`
  (`goal_achievement_rate`, `mean_decision_utility`, etc. — one more `_mean(...)`
  call each, same pattern). The closing **Footnotes** glossary
  (`_GLOSSARY` dict) gains entries for every new term introduced here
  (`duration_seconds` on `GATEWAY_RESOLVED`, `num_of_tokens`, `simulated_cost`,
  `agentic_cost`, `total_cost`, `total_agent_duration_seconds`) — the report
  must stay readable standalone, the same requirement that drove Milestone 11's
  glossary addition in the first place.
- The **Scores Matrix** section (Scenario × Configuration GAR, Milestone 11)
  is unaffected — GAR isn't a time/cost metric. No change there.

---

## Explicitly out of scope for this plan

- **Resource-pool/queueing cost** (the `BPMNTask`/`BPMNLane`/`ProcessReporter`-
  style resource-contention modeling SimPN itself supports natively but this
  adapter deliberately doesn't use — see `engine.py`'s own module docstring on
  why: one `DecProblem` per case, no cross-case contention). Hourly rates here
  are attached to *activities*, not *resources* — simpler, and matches what
  `simpn_parameters.yaml` already models (per-task, not per-resource-pool).
  Upgrading to resource-pool-based cost is the same upgrade path already
  flagged for resource contention generally, not a new gap this plan opens.
- **Per-model-tier token pricing** (different cost for input vs. output
  tokens, or for different models mid-run). `model_token_cost` is one flat
  rate for the one `MODEL_NAME` configured for the whole run via `.env` —
  consistent with how model selection already works today (one model per
  process, read fresh from `.env` at each `cuga-flo run-scenario` invocation —
  see `cuga.config`'s `load_dotenv` and `cuga.backend.llm.models`'s
  `MODEL_NAME` read). An input/output-token-split rate is a natural but
  separate follow-up once `tracker`'s own token accounting is confirmed to
  expose that split (it currently exposes only `total_tokens`, per
  `agent_loop.py`'s callback).
- **Changing `cycle_time_seconds`'s existing meaning.** It stays exactly what
  it is today (real wall-clock, whole run) — this plan only adds finer-grained
  siblings alongside it, never redefines it.

## Verification plan (once implemented)

- Unit: `_duration()`'s generalized `section` parameter, tested against both
  `tasks:` and a new `gateways:` fixture, including the "omitted entirely"
  default-zero case.
- Unit: `_duration()`'s distribution dispatch — each of the four samplers
  called with a fixed `random.seed(...)` to assert a reproducible value;
  `normal`'s clip-at-zero with parameters chosen to actually exercise the
  clip; an unrecognized `distribution` name and a both-`duration_seconds`-
  and-`duration`-set entry both raising the intended validation error, not
  silently picking one.
- Unit: a hand-written trace fixture with `GATEWAY_RESOLVED.duration`,
  `AGENT_*` events carrying `duration_seconds`/`num_of_tokens`, asserting
  `TraceRun.total_agent_duration_seconds()` and the extended
  `simulated_cycle_time_seconds()` sum correctly, including the "no agentic
  event at all → `None`" case.
- Unit: `evaluate()` with `rates=None` → every cost field `not_yet_computable`
  with the stated reason, never `0.0`; with a partial-rates fixture (one task
  priced, one not) → `simulated_cost` still computable from the priced one.
- Live: rerun `loan_approval_simpn`'s existing five scenarios (S01-S05) across
  G0-G3 with `hourly_rate`/`model_token_cost` added to its
  `simpn_parameters.yaml`, confirm `agentic_cost` is non-zero only for
  configs that actually made LLM calls (G1+), confirm `simulated_cost` is
  nonzero for every config (native tasks run under every G-level), and spot-
  check one real trace's `num_of_tokens` against `tracker.token_usage`'s own
  before/after values logged separately, to confirm the delta mechanism is
  sound in practice, not just in theory.
- Live: `--report` on that same rerun, confirming the new aggregate columns
  and glossary entries render correctly and the existing Scores Matrix section
  is untouched.
- Live: one scenario's `check credit` task switched to a `duration:
  distribution: exponential` entry, run with `--repetitions 5` under G0 —
  confirm the per-run `--csv` rows show five genuinely different
  `simulated_cycle_time_seconds` values (not five copies of one flat number),
  and that `mean_cycle_s` in the per-config aggregates is a real mean across
  them, not a repeated constant.

"""
LLM-assisted scenario authoring — design doc §5: natural-language scenario idea ->
LLM -> structured scenario -> schema/consistency validation -> optional researcher
review -> frozen scenario.

The LLM is used offline, once, to help populate a scenario — never at benchmark
runtime (the design doc's own caution against a second stochastic LLM in the loop,
which this codebase already treats as settled: the deterministic evaluator is
deliberately not an LLM either). Reuses the same CugaAgent/LLMManager plumbing
every other reasoning point in this codebase already uses (TaskAgent, DecisionAgent,
FlowAgent's hook reasoning), and the same robust-JSON-extraction convention
flow_agent.py's `_llm_hook_decision`/`_extract_initial_variables` already
established, not a new ad hoc parsing scheme.

This module runs in the main CUGA FLO process (full `cuga_flo`/`cuga` available),
unlike the Simulation Runtime Service — there is no dependency-isolation concern
here.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from cuga_flo.scenarios.schema import Scenario

_REPO_ROOT = Path(__file__).resolve().parents[3]

_SCHEMA_DESCRIPTION = """\
A scenario is a JSON object with this exact shape:

{
  "scenario_id": "<string, e.g. 'S04'>",
  "description": "<one paragraph, plain English>",
  "initial_state": {
    "process_variables": {"<variable_name>": <value>, ...},
    "semantic": {"<dotted.fact.name>": <value>, ...},
    "bps": {}
  },
  "perturbations": [
    {
      "when": "<element_id>.<enter|complete>",
      "bps_change": {"activities.<element_id>.duration_multiplier": <number>},
      "semantic_change": {"<dotted.fact.name>": <value>}
    }
  ],
  "expected_properties": {
    "eventually": ["<named predicate or real element id>", ...],
    "must": ["<...>"],
    "must_not": ["<...>"],
    "acceptable_routes": {"<flow_id>": <utility 0.0-1.0>, ...},
    "intervention": {"required_after": ["<hook_id>", ...]}
  }
}

Rules:
- "case_resolved" is always a valid predicate for eventually/must/must_not.
- Any other predicate name must be a real element id from the list below (it means
  "this element appears in the execution path").
- element_id in a perturbation's "when" MUST be one of the real element ids below.
- "activities.<element_id>.duration_multiplier" is the ONLY bps_change key with any
  effect — this engine does not model resource capacity/contention. Still include a
  resource-unavailability idea as a semantic_change fact instead (e.g.
  "services.X.available": false) if that is what the idea calls for — a policy can
  reason about that even though the engine can't enforce queueing from it.
- flow_id in acceptable_routes MUST be one of the real flow ids below.
- hook_id in intervention.required_after MUST be one of the real hook ids below —
  if the idea implies oversight is needed but this app/config has no hooks at all,
  leave required_after empty rather than inventing a hook id.
- initial_state.bps should normally be empty {} — see the duration_multiplier note
  above.
"""


def load_app_context(app_name: str, repo_root: Path | None = None) -> dict[str, Any]:
    """Everything the LLM needs to reference real element/flow/hook ids and
    variable names for this app — reuses BPMNParser/FlowConfig, the same parsing
    `scripts/build_simpn_app.sh` already does at build time, so this never drifts
    from what the engine actually sees."""
    from cuga_flo.engine.bpmn_parser import BPMNParser
    from cuga_flo.engine.flow_config import FlowConfig

    repo_root = repo_root or _REPO_ROOT
    app_dir = repo_root / "applications" / app_name
    config_path = app_dir / "config" / f"{app_name}_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"no {config_path} (see loan_approval_simpn for the naming convention)")

    cfg = FlowConfig.from_yaml(str(config_path))
    bpmn = BPMNParser().parse_file(cfg.get_bpmn_file())
    engine_config = cfg.to_engine_config()

    # Found live, not assumed: without this, the LLM invented a FICO-scale
    # credit_score (780.0) for an app whose own task policy defines it as 0.0-1.0 —
    # name:default alone doesn't say a variable is a Task Agent's OUTPUT, normally
    # overwritten the moment that task runs (G1+), so there's rarely a good reason
    # to give it a specific initial value at all.
    output_variables = sorted(
        {
            target
            for task in cfg.tasks_config
            for target in (task.get("output_mapping") or {}).values()
        }
    )

    return {
        "elements": {eid: {"type": e.element_type, "name": e.name} for eid, e in bpmn.elements.items()},
        "flows": [
            {"id": f.id, "name": f.name, "source": f.source_ref, "target": f.target_ref} for f in bpmn.flows
        ],
        "hooks": engine_config.get("hooks", []),
        "variables": cfg.variables,
        "output_variables": output_variables,
    }


def _extract_json(raw: str) -> dict[str, Any]:
    """Same fenced-code-block/substring-extraction convention as
    flow_agent.py's `_llm_hook_decision`."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    if not raw.startswith("{"):
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            raw = raw[start : end + 1]
    return json.loads(raw)


async def _ask_llm(scenario_id: str, idea: str, context: dict[str, Any]) -> str:
    from cuga.backend.llm.models import LLMManager
    from cuga.config import settings
    from cuga.sdk import CugaAgent

    prompt = f"""You are a scenario-authoring assistant for a BPMN process benchmark.
Convert the researcher's natural-language scenario idea into a structured scenario,
using ONLY real element/flow/hook ids from this process — never invent one.

## This process's real ids
Elements: {json.dumps(context['elements'], indent=2)}
Flows: {json.dumps(context['flows'], indent=2)}
Hooks: {json.dumps(context['hooks'], indent=2)}
Declared variables (name: default, implying type): {json.dumps(context['variables'], indent=2)}
Task-Agent OUTPUT variables (computed by a task when it runs, not supplied upfront):
{json.dumps(context['output_variables'], indent=2)}
Do NOT set an initial_state.process_variables value for any output variable listed
above unless the idea specifically calls for a frozen/legacy value in a config with
no Task Agent (e.g. testing G0's native behavior) — leave it out and let the real
Task Agent compute it otherwise. Never invent a numeric scale for a variable (e.g. a
0-100 or FICO-style score) beyond what its declared default implies.

## Scenario schema
{_SCHEMA_DESCRIPTION}

## Researcher's scenario idea
{idea}

Respond ONLY with a JSON object matching the schema above, with scenario_id "{scenario_id}".
"""
    llm = LLMManager().get_model(settings.agent.planner.model)
    agent = CugaAgent(
        special_instructions=(
            "You convert natural-language process-benchmark scenario ideas into "
            "structured JSON. Respond ONLY with a valid JSON object, no prose, no "
            "markdown fences."
        ),
        model=llm,
        enable_knowledge=False,
        auto_load_policies=False,
    )
    result = await agent.invoke(message=prompt)
    return result.answer


def check_consistency(scenario: Scenario, context: dict[str, Any]) -> list[str]:
    """
    Design doc §5's "schema / consistency validation" step — Pydantic already
    enforces the schema; this enforces that every reference actually names
    something real in this app, the same discipline `expected_properties`'s named-
    predicate registry already applies. Returns warnings rather than raising: a
    near-miss is still useful for the researcher's own review pass (the design
    doc's next pipeline step) to see and fix, not something to reject outright.
    """
    warnings: list[str] = []
    element_ids = set(context["elements"].keys())
    flow_ids = {f["id"] for f in context["flows"]}
    hook_ids = {h["id"] for h in context["hooks"]}

    for p in scenario.perturbations:
        element_id, _, point = p.when.rpartition(".")
        if element_id not in element_ids:
            warnings.append(f"perturbation when={p.when!r}: {element_id!r} is not a known element id")
        if point not in ("enter", "complete"):
            warnings.append(f"perturbation when={p.when!r}: {point!r} is not 'enter' or 'complete'")

    for flow_id in scenario.expected_properties.acceptable_routes:
        if flow_id not in flow_ids:
            warnings.append(f"acceptable_routes: {flow_id!r} is not a known flow id")

    for hook_id in scenario.expected_properties.intervention.required_after:
        if hook_id not in hook_ids:
            warnings.append(f"intervention.required_after: {hook_id!r} is not a known hook id")

    return warnings


def author_scenario(
    app_name: str, scenario_id: str, idea: str, repo_root: Path | None = None
) -> tuple[Scenario, list[str]]:
    """
    Returns (scenario, consistency_warnings). Raises if the LLM's output doesn't
    even parse as JSON or doesn't validate against the Scenario schema — those are
    hard failures. Consistency warnings (an unknown element/flow/hook id) are
    returned instead of raised, per `check_consistency`'s docstring.
    """
    context = load_app_context(app_name, repo_root)
    raw = asyncio.run(_ask_llm(scenario_id, idea, context))
    data = _extract_json(raw)
    scenario = Scenario.model_validate(data)
    return scenario, check_consistency(scenario, context)

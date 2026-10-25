"""
Builds a simpn.simulator.DecProblem from a BPMN process + engine config + BPS
parameters, and runs one simulated case through it to completion.

Topology: one SimVar place per BPMN element (`place[eid]` = "a case is ready to
execute eid"). An element's event/decision consumes its place's token and, for each
outgoing BPMN flow, may emit a token onto the target element's place — selecting
exactly one outgoing arc for a task/gateway, exactly as validated against the real
`simpn` package before this was written (see the plan's technical-grounding section).

Hooked edges insert an extra place: instead of task A's event emitting straight onto
place[B] (B = the flow's normal target), it emits onto `hook_in[flow_id]`, and a
separate hook event consumes that and decides where the case actually goes next —
continue (→ B), or any of the other seven HookAction outcomes, all handled by the same
mechanism: pick a target element id and emit the token onto `place[target_id]` (or the
dedicated terminal sink for `terminate`). This is deliberately one generalized
mechanism rather than seven special cases — see `_resolve_hook_target`.

Each place's token VALUE is the running case state (a plain dict — no `cuga_flo`
import here, see the package docstring): process_variables, execution_path,
task_results, gateway_decisions, is_complete, is_halted, halt_reason,
process_instance_id. One DecProblem is built fresh per simulated instance (case), not
shared across concurrently-running instances.

ponytail: that one-DecProblem-per-instance choice sidesteps modeling resource
contention *between* cases (queueing, shared capacity) — the real BPS behavior
Prosimos-style engines bring. This pass's job was control-point interception + the
full HookAction vocabulary + trace + evaluator, not resource-contention fidelity.
Upgrade path: move to one shared DecProblem per process definition, keyed by case id
in the resource-queue layer, once multi-case throughput experiments are in scope.

ponytail: gateways are modeled as XOR choice only (one chosen outgoing arc) — a true
AND-split/AND-join `parallelGateway` is not given synchronizing join semantics (each
element has exactly one place, so a join fires once per arriving token rather than
waiting for all branches). Not exercised by this pass's demo app. Upgrade path: give
join elements one place per incoming flow and a join-specific event once a
parallel-gateway app is in scope.
"""

from __future__ import annotations

import json
import operator as _op
import re
import uuid
from collections.abc import Callable
from typing import Any

from simpn.simulator import DecProblem, SimToken

from . import formulas
from . import perturbations as _perturbations
from . import trace as _trace

_DEFAULT_DURATION_SECONDS = 1.0
_TERMINATED = "__terminated__"

_TASK_TYPES = {"task", "serviceTask", "userTask", "scriptTask", "manualTask"}
_GATEWAY_TYPES = {"exclusiveGateway", "parallelGateway", "inclusiveGateway"}

# Mirrors decision_agent.py's safe condition evaluator exactly (duplicated, not
# imported — this package has no cuga_flo dependency; see module docstring).
_CMP_OPS: dict[str, Callable[[Any, Any], bool]] = {
    ">=": _op.ge,
    "<=": _op.le,
    "!=": _op.ne,
    "==": _op.eq,
    ">": _op.gt,
    "<": _op.lt,
}


def _parse_literal(s: str) -> Any:
    s = s.strip()
    if s == "None":
        return None
    if s == "True":
        return True
    if s == "False":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _safe_compare(expr: str) -> bool:
    for op_str, op_fn in _CMP_OPS.items():
        parts = expr.split(op_str, 1)
        if len(parts) == 2:
            left = _parse_literal(parts[0])
            right = _parse_literal(parts[1])
            if left is None or right is None:
                return False
            try:
                return bool(op_fn(left, right))
            except TypeError:
                return False
    return False


def _substitute_vars(expr: str, variables: dict[str, Any]) -> str:
    def replace(match: re.Match) -> str:
        return str(variables.get(match.group(1).strip(), "None"))

    return re.sub(r"\$\{([^}]+)\}", replace, expr)


def eval_condition(condition: str | None, variables: dict[str, Any]) -> bool:
    if not condition:
        return False
    try:
        expr = _substitute_vars(condition, variables)
        if any(op in expr for op in _CMP_OPS):
            return _safe_compare(expr)
    except Exception:
        pass
    return False


class ProcessModel:
    """Thin view over the BPMNProcess + engine-config JSON this service loads at build time."""

    def __init__(self, process_model: dict, engine_config: dict):
        self.id = process_model["id"]
        self.name = process_model["name"]
        self.elements: dict[str, dict] = process_model["elements"]
        self.flows: list[dict] = process_model["flows"]
        self.start_event: str = process_model["start_event"]
        self.end_events: set[str] = set(process_model.get("end_events") or [])

        self.outgoing: dict[str, list[dict]] = {}
        self.incoming: dict[str, list[dict]] = {}
        self.flow_by_id: dict[str, dict] = {}
        for f in self.flows:
            self.flow_by_id[f["id"]] = f
            self.outgoing.setdefault(f["source_ref"], []).append(f)
            self.incoming.setdefault(f["target_ref"], []).append(f)

        self.agentic_task_ids: set[str] = set(engine_config.get("agentic_task_ids", []))
        self.decision_gateway_ids: set[str] = set(engine_config.get("decision_gateway_ids", []))
        # A native task's own built-in deterministic computation — opposite of
        # `tool:`'s externally-injected callable; see TaskConfig.formula's docstring.
        self.native_formulas: dict[str, str] = engine_config.get("native_formulas", {})
        self.task_instructions: dict[str, str] = engine_config.get("task_instructions", {})
        self.flow_conditions: dict[str, str] = engine_config.get("flow_conditions", {})
        self.hooks: dict[str, dict] = {
            h["location"]: h for h in engine_config.get("hooks", []) if h.get("enabled", True)
        }
        self.action_permissions: dict = engine_config.get("action_permissions", {})

        self.process_model_summary = {
            "process_id": self.id,
            "process_name": self.name,
            "elements": {eid: {"type": e["element_type"], "name": e["name"]} for eid, e in self.elements.items()},
            "flows": [
                {"id": f["id"], "source": f["source_ref"], "target": f["target_ref"], "condition": f.get("condition")}
                for f in self.flows
            ],
            "start_event": self.start_event,
            "end_events": sorted(self.end_events),
        }

    def flow_condition(self, flow: dict) -> str | None:
        return self.flow_conditions.get(flow["id"]) or flow.get("condition")


def _new_state(process: ProcessModel, variables: dict[str, Any]) -> dict:
    return {
        "process_instance_id": uuid.uuid4().hex,
        "process_id": process.id,
        "process_name": process.name,
        "process_variables": dict(variables),
        "execution_path": [],
        "task_results": {},
        "gateway_decisions": {},
        "is_complete": False,
        "is_halted": False,
        "halt_reason": "",
        # Scenario/perturbation bookkeeping — see perturbations.py. Empty/unused for a
        # plain (non-scenario) run.
        "_applied_perturbations": set(),
        "_duration_overrides": {},
        "_unsupported_perturbations": [],
    }


def _flow_state_dict(state: dict, current_node_id: str) -> dict:
    """Minimal FlowState-shaped dict — AgentState's other fields all default, so this
    round-trips through FlowState.model_validate() on the FlowAgent side (verified
    directly against the real class before this was written)."""
    return {
        "process_id": state["process_id"],
        "process_name": state["process_name"],
        "current_node_id": current_node_id,
        "execution_path": list(state["execution_path"]),
        "process_variables": dict(state["process_variables"]),
        "task_results": dict(state["task_results"]),
        "gateway_decisions": dict(state["gateway_decisions"]),
        "is_complete": state["is_complete"],
        "is_halted": state["is_halted"],
        "halt_reason": state["halt_reason"],
    }


def _ctx(
    state: dict,
    element_id: str,
    element_name: str,
    process: ProcessModel,
    task_instruction: str | None = None,
    available_flows: list[dict] | None = None,
    edge_id: str | None = None,
) -> dict:
    return {
        "process_instance_id": state["process_instance_id"],
        "element_id": element_id,
        "element_name": element_name,
        "current_state": _flow_state_dict(state, element_id),
        "execution_history": list(state["execution_path"]),
        "process_model_summary": process.process_model_summary,
        "task_instruction": task_instruction,
        "available_flows": available_flows,
        "edge_id": edge_id,
    }


def _duration(task_id: str, simpn_params: dict) -> float:
    return (simpn_params.get("tasks", {}).get(task_id, {}) or {}).get(
        "duration_seconds", _DEFAULT_DURATION_SECONDS
    )


def _resolve_hook_target(
    action: str,
    result: dict,
    normal_next_id: str,
    process: ProcessModel,
    trace_sink,
    state: dict,
    hook_id: str,
) -> str | None:
    """
    One mechanism for all seven HookActions: resolve to a target element id (or None
    for `terminate`), falling back to `normal_next_id` with a logged warning whenever
    the requested target can't be resolved against this (deliberately simple, linear)
    topology — see the module docstring's documented scope limits for skip_node,
    remove_node and add_node.
    """

    def successor_of(eid: str) -> str | None:
        outs = process.outgoing.get(eid, [])
        return outs[0]["target_ref"] if len(outs) == 1 else None

    def warn_fallback(reason: str) -> str:
        _trace.emit(
            trace_sink,
            run_id=state["process_instance_id"],
            case_id=state["process_instance_id"],
            source="simpn",
            event_type="HOOK_TARGET_FALLBACK",
            bpmn_element_id=hook_id,
            payload={"action": action, "reason": reason, "fell_back_to": normal_next_id},
        )
        return normal_next_id

    if action == "continue":
        return normal_next_id
    if action == "terminate":
        return None
    if action == "skip_node":
        target = successor_of(normal_next_id)
        return target or warn_fallback(f"'{normal_next_id}' has no single successor to skip to")
    if action == "skip_to":
        target = result.get("skip_to_node")
        if target and target in process.elements:
            return target
        return warn_fallback(f"skip_to_node {target!r} is not a known element")
    if action == "remove_node":
        target = result.get("remove_node")
        if target == normal_next_id:
            successor = successor_of(target)
            if successor:
                return successor
            return warn_fallback(f"remove_node {target!r} has no single successor")
        return warn_fallback(f"remove_node {target!r} is not the immediate next node — unsupported")
    if action == "add_node":
        add_node = result.get("add_node") or {}
        target = add_node.get("node_id")
        if target and target in process.elements:
            return target
        return warn_fallback(f"add_node {target!r} is not a pre-declared element")
    if action == "swap_nodes":
        pair = result.get("swap_nodes") or [None, None]
        node_a, node_b = (pair + [None, None])[:2]
        if normal_next_id == node_a and node_b in process.elements:
            return node_b
        if normal_next_id == node_b and node_a in process.elements:
            return node_a
        return warn_fallback(f"swap_nodes {pair!r} doesn't resolve against next node {normal_next_id!r}")
    return warn_fallback(f"unknown action {action!r}")


def build_problem(
    process: ProcessModel,
    simpn_params: dict,
    mcp_url: str,
    trace_sink,
    perturbations: list[dict] | None = None,
) -> tuple[DecProblem, Any]:
    """Build a fresh DecProblem for one instance. Returns (problem, start_place) —
    start_place is wherever the initial token must land (the start event's outgoing
    flow's target, or that flow's hook_in place if the very first edge is hooked)."""
    from . import mcp_client

    perturbations = perturbations or []
    problem = DecProblem()
    places: dict[str, Any] = {eid: problem.add_var(eid) for eid in process.elements}
    places[_TERMINATED] = problem.add_var(_TERMINATED)
    hook_in_places: dict[str, Any] = {loc: problem.add_var(f"hook_in__{loc}") for loc in process.hooks}

    def trace_control_point(state: dict, event_type: str, element_id: str, payload: dict) -> None:
        _trace.emit(
            trace_sink,
            run_id=state["process_instance_id"],
            case_id=state["process_instance_id"],
            source="simpn",
            event_type=event_type,
            bpmn_element_id=element_id,
            payload=payload,
        )

    def check_point(state: dict, element_id: str, point: str) -> None:
        """Apply any not-yet-applied perturbation triggered by `element_id.point` —
        see perturbations.py. A task reading `state["_duration_overrides"]` for its
        own duration must call this at "enter" BEFORE computing its duration."""
        for payload in _perturbations.check_and_apply(state, perturbations, element_id, point):
            trace_control_point(state, "PERTURBATION_APPLIED", element_id, payload)

    def target_place_for(flow: dict) -> Any:
        """The place a flow's token should land on — hook_in if this edge is hooked,
        else straight to the target element's place."""
        hook = process.hooks.get(flow["id"])
        return hook_in_places[flow["id"]] if hook else places[flow["target_ref"]]

    def make_task_behavior(eid: str, element: dict):
        is_agentic = eid in process.agentic_task_ids
        formula_name = process.native_formulas.get(eid)
        outgoing = process.outgoing.get(eid, [])
        instruction = process.task_instructions.get(eid)

        def behavior(state: dict):
            check_point(state, eid, "enter")
            base_duration = _duration(eid, simpn_params)
            multiplier = state["_duration_overrides"].get(eid, 1.0)
            duration = base_duration * multiplier
            formula_applied = None
            if is_agentic:
                ctx = _ctx(state, eid, element["name"], process, task_instruction=instruction)
                result = mcp_client.call_tool(mcp_url, "execute_task", {"task_id": eid, "ctx": ctx})
                state["process_variables"] = result.get("process_variables", state["process_variables"])
                state["task_results"].update(result.get("task_results", {}))
            elif formula_name:
                # The engine's own built-in deterministic computation — no MCP
                # call, no CUGA FLO process involved at all. See formulas.py.
                fn = formulas.REGISTRY.get(formula_name)
                if fn is None:
                    trace_control_point(
                        state, "FORMULA_NOT_FOUND", eid, {"formula": formula_name}
                    )
                else:
                    state["process_variables"].update(fn(state["process_variables"]))
                    formula_applied = formula_name
            state["execution_path"] = state["execution_path"] + [eid]
            trace_control_point(
                state,
                "ACTIVITY_COMPLETE",
                eid,
                {"agentic": is_agentic, "formula_applied": formula_applied, "duration": duration},
            )
            check_point(state, eid, "complete")
            if not outgoing:  # a task with no outgoing flow is a dead end, not a normal model
                state["is_complete"] = True
            return [SimToken(state, delay=duration)]

        return behavior, [target_place_for(outgoing[0])] if outgoing else [places[_TERMINATED]]

    def make_gateway_behavior(eid: str, element: dict):
        outgoing = process.outgoing.get(eid, [])
        is_agentic = eid in process.decision_gateway_ids

        def behavior(state: dict):
            check_point(state, eid, "enter")
            state["execution_path"] = state["execution_path"] + [eid]
            if len(outgoing) <= 1:
                chosen_flow = outgoing[0] if outgoing else None
            elif is_agentic:
                available = [
                    {"id": f["id"], "name": f.get("name") or f["id"], "source_ref": f["source_ref"],
                     "target_ref": f["target_ref"], "condition": process.flow_condition(f)}
                    for f in outgoing
                ]
                ctx = _ctx(state, eid, element["name"], process, available_flows=available)
                chosen_id = mcp_client.call_tool(mcp_url, "route_gateway", {"gateway_id": eid, "ctx": ctx})
                chosen_flow = next((f for f in outgoing if f["id"] == chosen_id), outgoing[0])
            else:
                chosen_flow = next(
                    (f for f in outgoing if eval_condition(process.flow_condition(f), state["process_variables"])),
                    outgoing[0],
                )
            state["gateway_decisions"][eid] = chosen_flow["id"] if chosen_flow else ""
            trace_control_point(state, "GATEWAY_RESOLVED", eid, {"chosen_flow": state["gateway_decisions"][eid]})
            check_point(state, eid, "complete")

            result = [None] * len(outgoing)
            if chosen_flow is not None:
                result[outgoing.index(chosen_flow)] = SimToken(state)
            return result

        return behavior, [target_place_for(f) for f in outgoing]

    def make_hook_behavior(flow: dict):
        hook = process.hooks[flow["id"]]
        normal_next_id = flow["target_ref"]
        # Every element is a legal target (skip_to/remove_node/add_node/swap_nodes may
        # name any of them); terminate routes to the dedicated sink.
        target_ids = list(process.elements.keys())
        outgoing_places = [places[eid] for eid in target_ids] + [places[_TERMINATED]]

        def behavior(state: dict):
            check_point(state, hook["id"], "enter")
            ctx = _ctx(state, normal_next_id, process.elements[normal_next_id]["name"], process, edge_id=flow["id"])
            result = mcp_client.call_tool(mcp_url, "evaluate_hook", {"hook_id": hook["id"], "ctx": ctx})
            state["process_variables"].update(result.get("state_updates") or {})
            action = result.get("action", "continue")
            target = _resolve_hook_target(
                action, result, normal_next_id, process, trace_sink, state, hook["id"]
            )
            trace_control_point(
                state, "HOOK_EVALUATED", hook["id"], {"action": action, "target": target}
            )
            check_point(state, hook["id"], "complete")

            out = [None] * len(target_ids) + [None]
            if target is None:  # terminate
                state["is_halted"] = True
                state["halt_reason"] = result.get("message") or "Hook terminated the process"
                out[-1] = SimToken(state)
            else:
                out[target_ids.index(target)] = SimToken(state)
            return out

        return behavior, outgoing_places

    # ── Wire every element ────────────────────────────────────────────────
    for eid, element in process.elements.items():
        etype = element["element_type"]
        if etype in _TASK_TYPES:
            behavior, outflow = make_task_behavior(eid, element)
            problem.add_event([places[eid]], outflow, behavior, name=f"task__{eid}")
        elif etype in _GATEWAY_TYPES:
            behavior, outflow = make_gateway_behavior(eid, element)
            if eid in process.decision_gateway_ids:
                problem.add_decision([places[eid]], outflow, behavior, name=f"gw__{eid}")
            else:
                problem.add_event([places[eid]], outflow, behavior, name=f"gw__{eid}")
        elif etype == "endEvent":
            def make_end_behavior(_eid: str):
                def behavior(state: dict):
                    check_point(state, _eid, "enter")
                    state["execution_path"] = state["execution_path"] + [_eid]
                    state["is_complete"] = True
                    trace_control_point(state, "CASE_COMPLETE", _eid, {})
                    return [SimToken(state)]

                return behavior

            problem.add_event([places[eid]], [places[_TERMINATED]], make_end_behavior(eid), name=f"end__{eid}")
        # startEvent has no incoming place/transition — the initial token is seeded
        # directly onto its outgoing flow's target (see run_instance).

    for flow_id, hook_cfg in process.hooks.items():
        flow = process.flow_by_id[flow_id]
        behavior, outflow = make_hook_behavior(flow)
        problem.add_decision([hook_in_places[flow_id]], outflow, behavior, name=f"hook__{hook_cfg['id']}")

    start_outgoing = process.outgoing.get(process.start_event, [])
    if not start_outgoing:
        raise ValueError(f"Start event {process.start_event!r} has no outgoing flow")
    start_place = target_place_for(start_outgoing[0])

    return problem, start_place


def run_instance(
    process: ProcessModel,
    simpn_params: dict,
    variables: dict[str, Any],
    trace_sink=None,
) -> dict:
    """
    Run one simulated case to completion and signal `complete_process`.

    `variables` carries `cugaMcpUrl`/`cugaProcessKey`/`cugaTracePath` the same way
    Kogito's process variables do (set by MCPFlowBridge.register_simpn_engine) — read
    here and stripped before they become part of `process_variables`, the same way
    FlowAgent's own `_build_task_input` already excludes `cugaProcessKey` by name from
    what an LLM sees. `cugaPerturbations` (a JSON-encoded list) rides the same way —
    it's how `cuga_flo.scenarios.runner` (main process) hands this run's scenario
    perturbations to the service without any bridge/proxy wire-format change: it's
    just one more process variable, exactly like the others.

    Without the final `complete_process` call, FlowAgent.invoke()'s completion future
    never resolves and the caller hangs forever — this is the one call every workflow
    engine (Flowable/Kogito/LangGraph) must make on every terminal branch.
    """
    from . import mcp_client

    variables = dict(variables)
    mcp_url = variables.pop("cugaMcpUrl", None)
    process_key = variables.pop("cugaProcessKey", None)
    trace_path = variables.pop("cugaTracePath", None)
    perturbations = json.loads(variables.pop("cugaPerturbations", "[]") or "[]")
    if not mcp_url:
        raise ValueError("cugaMcpUrl is not set in the start variables")

    owns_sink = trace_sink is None and trace_path
    if owns_sink:
        trace_sink = _trace.TraceSink(trace_path)

    problem, start_place = build_problem(process, simpn_params, mcp_url, trace_sink, perturbations)
    state = _new_state(process, variables)

    for payload in _perturbations.apply_start_perturbations(state, perturbations):
        _trace.emit(
            trace_sink,
            run_id=state["process_instance_id"],
            case_id=state["process_instance_id"],
            source="simpn",
            event_type="PERTURBATION_APPLIED",
            bpmn_element_id="",
            payload=payload,
        )

    _trace.emit(
        trace_sink,
        run_id=state["process_instance_id"],
        case_id=state["process_instance_id"],
        source="simpn",
        event_type="CASE_START",
        bpmn_element_id=process.start_event,
        payload={},
    )

    start_place.put(state)

    while problem.step() is not None:
        pass

    # Normal completion is already traced by the actual end event's own behavior
    # (make_end_behavior, tagged with the real element id reached) — only a halt
    # needs a trailing event here, since no end event fires on that path.
    if state["is_halted"]:
        _trace.emit(
            trace_sink,
            run_id=state["process_instance_id"],
            case_id=state["process_instance_id"],
            source="simpn",
            event_type="CASE_HALTED",
            bpmn_element_id=state["execution_path"][-1] if state["execution_path"] else "",
            payload={"halt_reason": state["halt_reason"]},
        )

    if process_key:
        mcp_client.call_tool(
            mcp_url,
            "complete_process",
            {"process_key": process_key, "state": _flow_state_dict(state, state["execution_path"][-1] if state["execution_path"] else "")},
        )

    if owns_sink:
        trace_sink.close()

    return state

"""
Checks for the SimPN adapter: the Simulation Runtime Service's engine/server, the
CUGA-FLO-side proxy, and the deterministic evaluator.

No real LLM calls: a tiny FastMCP bridge stands in for FlowAgent's execute_task /
route_gateway / evaluate_hook / complete_process tools, with canned responses —
everything here is about *our* wiring (G0-G3 control-point routing, the wire format,
the evaluator's predicates), the same philosophy as test_remote_agent.py.

Run: uv run pytest tests/test_simpn_adapter.py
"""

import json
import tempfile
import threading
import time

import pytest
import uvicorn
from fastmcp import FastMCP

from cuga_flo.adapters.simpn.proxy import SimPNProxy
from cuga_flo.adapters.simpn.runtime import engine, server
from cuga_flo.adapters.simpn.runtime import formulas as simpn_formulas
from cuga_flo.adapters.simpn.runtime import trace as simpn_trace
from cuga_flo.evaluation.expected_properties import ExpectedProperties
from cuga_flo.evaluation.metrics import evaluate
from cuga_flo.evaluation.trace_run import TraceRun

# ── a tiny demo process, reused across tests ──────────────────────────────────
# Start -> T1 (task_agent) --[hook H1]--> G1 (decision_agent) -> T2|T3 -> End1|End2

_PROCESS_MODEL = {
    "id": "demo",
    "name": "Demo",
    "elements": {
        "Start": {"id": "Start", "name": "Start", "element_type": "startEvent", "attributes": {}},
        "T1": {"id": "T1", "name": "T1", "element_type": "task", "attributes": {}},
        "G1": {"id": "G1", "name": "G1", "element_type": "exclusiveGateway", "attributes": {}},
        "T2": {"id": "T2", "name": "T2", "element_type": "task", "attributes": {}},
        "T3": {"id": "T3", "name": "T3", "element_type": "task", "attributes": {}},
        "End1": {"id": "End1", "name": "End1", "element_type": "endEvent", "attributes": {}},
        "End2": {"id": "End2", "name": "End2", "element_type": "endEvent", "attributes": {}},
    },
    "flows": [
        {"id": "f0", "name": "", "source_ref": "Start", "target_ref": "T1", "condition": None},
        {"id": "f1", "name": "", "source_ref": "T1", "target_ref": "G1", "condition": None},
        {"id": "f2", "name": "Approve", "source_ref": "G1", "target_ref": "T2", "condition": "${credit_score} > 0.6"},
        {"id": "f3", "name": "Reject", "source_ref": "G1", "target_ref": "T3", "condition": "${credit_score} <= 0.6"},
        {"id": "f4", "name": "", "source_ref": "T2", "target_ref": "End1", "condition": None},
        {"id": "f5", "name": "", "source_ref": "T3", "target_ref": "End2", "condition": None},
    ],
    "start_event": "Start",
    "end_events": ["End1", "End2"],
}
_ENGINE_CONFIG = {
    "agentic_task_ids": ["T1"],
    "decision_gateway_ids": ["G1"],
    "task_instructions": {"T1": "Check credit"},
    "hooks": [{"id": "H1", "hook_type": "edge", "location": "f1", "enabled": True}],
    "flow_conditions": {},
    "action_permissions": {},
}


@pytest.fixture(scope="module")
def mcp_bridge():
    """A real FastMCP HTTP server with canned execute_task/route_gateway/evaluate_hook/
    complete_process tools. `hook_action` and `route_choice` are mutable so individual
    tests can steer behavior without restarting the server."""
    route_choice = {"flow": "f2"}
    hook_action = {"action": "continue"}
    completed = []

    mcp = FastMCP("test-bridge")

    @mcp.tool(name="execute_task")
    async def execute_task(task_id: str, ctx: dict) -> dict:
        pv = dict(ctx["current_state"]["process_variables"])
        pv["credit_score"] = 0.8
        return {"execution_path": [task_id], "process_variables": pv, "task_results": {task_id: {"status": "ok"}}}

    @mcp.tool(name="route_gateway")
    async def route_gateway(gateway_id: str, ctx: dict) -> str:
        return route_choice["flow"]

    @mcp.tool(name="evaluate_hook")
    async def evaluate_hook(hook_id: str, ctx: dict) -> dict:
        return {
            "action": hook_action.get("action", "continue"),
            "message": hook_action.get("message"),
            "skip_to_node": hook_action.get("skip_to_node"),
            "user_prompt": None,
            "swap_nodes": hook_action.get("swap_nodes"),
            "graph_modifications": None,
            "state_updates": hook_action.get("state_updates", {}),
            "remove_node": hook_action.get("remove_node"),
            "add_node": hook_action.get("add_node"),
        }

    @mcp.tool(name="complete_process")
    async def complete_process(process_key: str, state: dict, bpmn: "dict | None" = None) -> dict:
        completed.append(process_key)
        return {}

    config = uvicorn.Config(mcp.http_app(stateless_http=True), host="127.0.0.1", port=8199, log_level="warning")
    uv_server = uvicorn.Server(config)
    t = threading.Thread(target=uv_server.run, daemon=True)
    t.start()
    time.sleep(1.0)

    yield {
        "url": "http://127.0.0.1:8199/mcp",
        "route_choice": route_choice,
        "hook_action": hook_action,
        "completed": completed,
    }

    uv_server.should_exit = True
    t.join(timeout=5)


@pytest.fixture
def process():
    return engine.ProcessModel(_PROCESS_MODEL, _ENGINE_CONFIG)


def _run(process, mcp_bridge, trace_sink=None, key="k", perturbations=None, simpn_params=None):
    mcp_bridge["completed"].clear()
    variables = {"cugaMcpUrl": mcp_bridge["url"], "cugaProcessKey": key}
    if perturbations is not None:
        variables["cugaPerturbations"] = json.dumps(perturbations)
    return engine.run_instance(process, simpn_params or {}, variables, trace_sink=trace_sink)


# ── G0/G1/G2: task + decision autonomy ─────────────────────────────────────────


def test_task_agent_and_decision_agent_route_through_gateway(process, mcp_bridge):
    """G1 task-agent control point runs, G2 decision-agent gateway routes on its
    output — and complete_process is called, or FlowAgent.invoke() would hang."""
    mcp_bridge["hook_action"].update(action="continue")
    mcp_bridge["route_choice"]["flow"] = "f2"
    state = _run(process, mcp_bridge, key="run1")
    assert state["execution_path"] == ["T1", "G1", "T2", "End1"]
    assert state["is_complete"] is True
    assert mcp_bridge["completed"] == ["run1"]


def test_native_gateway_uses_condition_without_decision_agent(mcp_bridge):
    """A gateway not in decision_gateway_ids routes by BPMN condition alone — no
    route_gateway call, matching the LangGraph engine's native-gateway behavior."""
    engine_config = dict(_ENGINE_CONFIG, decision_gateway_ids=[])
    process = engine.ProcessModel(_PROCESS_MODEL, engine_config)
    mcp_bridge["hook_action"].update(action="continue")
    state = _run(process, mcp_bridge, key="run-native")
    # execute_task always sets credit_score=0.8, so the "> 0.6" condition picks f2.
    assert state["execution_path"] == ["T1", "G1", "T2", "End1"]


def test_native_task_with_a_formula_computes_without_any_mcp_call(mcp_bridge):
    """A formula: task is not in agentic_task_ids — no execute_task call at all —
    yet it still updates process_variables, entirely inside this process. The
    actual function comes from the app's own formula_registry (see
    cuga_flo.adapters.simpn.runtime.formulas), not anything this module defines —
    a stub stands in here since this test is about the engine's wiring, not any
    one app's formula content."""
    engine_config = dict(
        _ENGINE_CONFIG, agentic_task_ids=[], decision_gateway_ids=[], native_formulas={"T1": "double_it"}
    )
    registry = {"double_it": lambda pv: {"credit_score": 0.5}}
    process = engine.ProcessModel(_PROCESS_MODEL, engine_config, formula_registry=registry)
    mcp_bridge["hook_action"].update(action="continue")
    state = _run(
        process,
        mcp_bridge,
        key="run-formula",
        simpn_params={},
    )
    assert state["process_variables"]["credit_score"] == 0.5
    assert mcp_bridge["completed"] == ["run-formula"]  # still reaches complete_process


def test_unknown_formula_name_is_traced_not_crashed(process, mcp_bridge):
    engine_config = dict(
        _ENGINE_CONFIG, agentic_task_ids=[], decision_gateway_ids=[], native_formulas={"T1": "no_such_formula"}
    )
    no_formula_process = engine.ProcessModel(_PROCESS_MODEL, engine_config)
    mcp_bridge["hook_action"].update(action="continue")
    path = tempfile.mktemp()
    sink = simpn_trace.TraceSink(path)
    state = _run(no_formula_process, mcp_bridge, trace_sink=sink, key="run-unknown-formula")
    sink.close()
    assert state["is_complete"] is True  # doesn't crash the run
    events = [json.loads(line) for line in open(path)]
    assert any(e["event_type"] == "FORMULA_NOT_FOUND" for e in events)


def test_load_registry_reads_an_apps_formulas_file_by_path(tmp_path):
    """The real loading path formulas.load_registry exercises: an app's own
    formulas.py, staged by build_simpn_app.sh, loaded by file path at service
    startup — not a module import, since no app directory is on sys.path."""
    formulas_file = tmp_path / "formulas.py"
    formulas_file.write_text(
        "def triple(pv):\n    return {'x': pv.get('x', 0) * 3}\n\nREGISTRY = {'triple': triple}\n"
    )
    registry = simpn_formulas.load_registry(formulas_file)
    assert registry["triple"]({"x": 2}) == {"x": 6}


def test_load_registry_returns_empty_when_the_app_has_no_formulas_file(tmp_path):
    assert simpn_formulas.load_registry(tmp_path / "no_such_file.py") == {}


# ── G3: every HookAction, one mechanism ────────────────────────────────────────


def test_hook_skip_to_bypasses_the_gateway_entirely(process, mcp_bridge):
    mcp_bridge["hook_action"].clear()
    mcp_bridge["hook_action"].update(action="skip_to", skip_to_node="T3")
    state = _run(process, mcp_bridge, key="run-skip-to")
    assert state["execution_path"] == ["T1", "T3", "End2"]
    assert "G1" not in state["execution_path"]


def test_hook_terminate_halts_before_the_gateway(process, mcp_bridge):
    mcp_bridge["hook_action"].clear()
    mcp_bridge["hook_action"].update(action="terminate", message="policy halt")
    state = _run(process, mcp_bridge, key="run-terminate")
    assert state["is_halted"] is True
    assert state["halt_reason"] == "policy halt"
    assert state["execution_path"] == ["T1"]


def test_hook_swap_nodes_redirects_to_the_paired_node(process, mcp_bridge):
    mcp_bridge["hook_action"].clear()
    mcp_bridge["hook_action"].update(action="swap_nodes", swap_nodes=["G1", "T3"])
    state = _run(process, mcp_bridge, key="run-swap")
    # Normal next (after the hook) is G1; the pair names G1<->T3, so this case goes to T3.
    assert state["execution_path"] == ["T1", "T3", "End2"]


def test_hook_add_node_routes_to_a_pre_declared_element(process, mcp_bridge):
    mcp_bridge["hook_action"].clear()
    mcp_bridge["hook_action"].update(action="add_node", add_node={"node_id": "T3"})
    state = _run(process, mcp_bridge, key="run-add-node")
    assert state["execution_path"] == ["T1", "T3", "End2"]


def test_hook_unresolvable_target_falls_back_to_continue_and_traces_it(process, mcp_bridge):
    """skip_node has no single successor here (G1 has two outgoing flows) — the
    documented fallback applies, and the evaluator can see it happened."""
    mcp_bridge["hook_action"].clear()
    mcp_bridge["hook_action"].update(action="skip_node")
    sink = simpn_trace.TraceSink(tempfile.mktemp())
    state = _run(process, mcp_bridge, trace_sink=sink, key="run-fallback")
    sink.close()
    assert state["execution_path"] == ["T1", "G1", "T2", "End1"]  # fell back to continue


def test_hook_state_updates_are_applied(process, mcp_bridge):
    mcp_bridge["hook_action"].clear()
    mcp_bridge["hook_action"].update(action="continue", state_updates={"flagged": True})
    state = _run(process, mcp_bridge, key="run-state-updates")
    assert state["process_variables"]["flagged"] is True


# ── SimPNProxy against a real server.py instance ───────────────────────────────


def test_proxy_start_process_against_a_real_service(process, mcp_bridge):
    mcp_bridge["hook_action"].update(action="continue")
    mcp_bridge["route_choice"]["flow"] = "f2"

    httpd_thread = threading.Thread(
        target=server.serve, args=(process, {}, "127.0.0.1", 8198), daemon=True
    )
    httpd_thread.start()
    time.sleep(1.0)

    proxy = SimPNProxy(base_url="http://127.0.0.1:8198")
    assert proxy.ping() is True

    result = proxy.start_process(
        "demo", variables={"cugaMcpUrl": mcp_bridge["url"], "cugaProcessKey": "proxy-run"}
    )
    assert result["credit_score"] == 0.8


# ── Deterministic evaluator ─────────────────────────────────────────────────────


def test_evaluator_reports_not_yet_computable_without_an_oracle(process, mcp_bridge):
    trace_path = tempfile.mktemp()
    sink = simpn_trace.TraceSink(trace_path)
    mcp_bridge["hook_action"].update(action="continue")
    mcp_bridge["route_choice"]["flow"] = "f2"
    _run(process, mcp_bridge, trace_sink=sink, key="eval-run-1")
    sink.close()

    card = evaluate(trace_path)
    assert card.process_completed is True
    assert card.activity_count == 5  # T1, H1 (hook), G1, T2, End1
    assert "goal_achieved" in card.not_yet_computable
    assert card.goal_achieved is None


def test_evaluator_computes_goal_achievement_and_intervention_f1_with_an_oracle(process, mcp_bridge):
    trace_path = tempfile.mktemp()
    sink = simpn_trace.TraceSink(trace_path)
    mcp_bridge["hook_action"].clear()
    mcp_bridge["hook_action"].update(action="skip_to", skip_to_node="T3")
    _run(process, mcp_bridge, trace_sink=sink, key="eval-run-2")
    sink.close()

    # Oracle: the hook was expected to intervene (required_after H1); it did (skip_to).
    expected = ExpectedProperties.model_validate(
        {
            "eventually": ["case_resolved"],
            "must": ["T3"],
            "must_not": ["T2"],
            "acceptable_routes": {},
            "intervention": {"required_after": ["H1"]},
        }
    )
    card = evaluate(trace_path, expected=expected)
    assert card.goal_achieved is True
    assert card.intervention_precision == 1.0
    assert card.intervention_recall == 1.0
    assert card.intervention_f1 == 1.0
    assert "perturbation_recovered" in card.not_yet_computable


def test_evaluator_goal_not_achieved_when_must_not_predicate_holds(process, mcp_bridge):
    trace_path = tempfile.mktemp()
    sink = simpn_trace.TraceSink(trace_path)
    mcp_bridge["hook_action"].update(action="continue")
    mcp_bridge["route_choice"]["flow"] = "f2"  # goes to T2
    _run(process, mcp_bridge, trace_sink=sink, key="eval-run-3")
    sink.close()

    expected = ExpectedProperties.model_validate({"must_not": ["T2"]})
    card = evaluate(trace_path, expected=expected)
    assert card.goal_achieved is False


def test_decision_utility_picks_the_gateway_the_oracle_is_about():
    """Found running a real live trace: loan_approval_simpn has an agentic decision
    gateway AND a plain downstream merge gateway. chosen_flow_id used to take
    whichever gateway resolved *last* in the trace — the merge gateway, not the
    decision one — so decision_utility silently came back None even though the
    decision gateway's chosen flow was a perfectly good match in acceptable_routes."""
    path = tempfile.mktemp()
    sink = simpn_trace.TraceSink(path)
    simpn_trace.emit(sink, run_id="r", case_id="r", source="simpn", event_type="CASE_START", bpmn_element_id="Start", payload={})
    simpn_trace.emit(sink, run_id="r", case_id="r", source="simpn", event_type="GATEWAY_RESOLVED", bpmn_element_id="DecisionGateway", payload={"chosen_flow": "Approve"})
    simpn_trace.emit(sink, run_id="r", case_id="r", source="simpn", event_type="GATEWAY_RESOLVED", bpmn_element_id="MergeGateway", payload={"chosen_flow": "MergeFlow"})
    simpn_trace.emit(sink, run_id="r", case_id="r", source="simpn", event_type="CASE_COMPLETE", bpmn_element_id="End", payload={})
    sink.close()

    expected = ExpectedProperties.model_validate({"acceptable_routes": {"Approve": 1.0, "Reject": 0.6}})
    card = evaluate(path, expected=expected)
    assert card.decision_utility == 1.0


def test_trace_run_loads_only_the_requested_run_id():
    path = tempfile.mktemp()
    sink = simpn_trace.TraceSink(path)
    simpn_trace.emit(sink, run_id="a", case_id="a", source="simpn", event_type="CASE_START", bpmn_element_id="Start", payload={})
    simpn_trace.emit(sink, run_id="b", case_id="b", source="simpn", event_type="CASE_START", bpmn_element_id="Start", payload={})
    sink.close()

    run = TraceRun.load(path, run_id="a")
    assert run.run_id == "a"
    assert len(run.events) == 1


# ── Perturbations ───────────────────────────────────────────────────────────────


def test_duration_multiplier_perturbation_scales_cycle_time(process, mcp_bridge):
    mcp_bridge["hook_action"].update(action="continue")
    mcp_bridge["route_choice"]["flow"] = "f2"
    simpn_params = {"tasks": {"T1": {"duration_seconds": 2.0}}}

    baseline = _run(process, mcp_bridge, key="pert-baseline", simpn_params=simpn_params)
    perturbed = _run(
        process, mcp_bridge, key="pert-slow", simpn_params=simpn_params,
        perturbations=[{"when": "T1.enter", "bps_change": {"activities.T1.duration_multiplier": 5}}],
    )
    assert perturbed["_duration_overrides"]["T1"] == 5
    assert baseline["execution_path"] == perturbed["execution_path"]  # routing unaffected


def test_start_perturbation_sets_semantic_fact_before_first_task(process, mcp_bridge):
    mcp_bridge["hook_action"].update(action="continue")
    mcp_bridge["route_choice"]["flow"] = "f2"
    state = _run(
        process, mcp_bridge, key="pert-semantic",
        perturbations=[{"when": "__start__", "semantic_change": {"evidence.contradictory_information": True}}],
    )
    assert state["process_variables"]["semantic.evidence.contradictory_information"] is True


def test_mid_run_semantic_perturbation_fires_at_the_right_point(process, mcp_bridge):
    mcp_bridge["hook_action"].update(action="continue")
    mcp_bridge["route_choice"]["flow"] = "f2"
    state = _run(
        process, mcp_bridge, key="pert-mid-run",
        perturbations=[{"when": "G1.complete", "semantic_change": {"foo": "bar"}}],
    )
    assert state["process_variables"]["semantic.foo"] == "bar"


def test_unsupported_bps_change_is_recorded_not_silently_dropped(process, mcp_bridge):
    mcp_bridge["hook_action"].update(action="continue")
    mcp_bridge["route_choice"]["flow"] = "f2"
    state = _run(
        process, mcp_bridge, key="pert-unsupported",
        perturbations=[{"when": "__start__", "bps_change": {"resources.Foo.capacity": 0}}],
    )
    assert state["_unsupported_perturbations"] == [{"when": "__start__", "keys": ["resources.Foo.capacity"]}]


def test_perturbation_fires_at_most_once(process, mcp_bridge):
    """T1 is only ever entered once per case in this linear demo, but the guard
    (state["_applied_perturbations"]) is what actually prevents a double-apply —
    assert the mechanism, not just the model's shape."""
    mcp_bridge["hook_action"].update(action="continue")
    mcp_bridge["route_choice"]["flow"] = "f2"
    state = _run(
        process, mcp_bridge, key="pert-once",
        perturbations=[{"when": "T1.enter", "semantic_change": {"count": 1}}],
    )
    assert len(state["_applied_perturbations"]) == 1


# ── Scenario schema / runner ─────────────────────────────────────────────────────


def test_build_run_inputs_flattens_semantic_and_encodes_perturbations():
    from cuga_flo.scenarios.runner import build_run_inputs
    from cuga_flo.scenarios.schema import Scenario

    scenario = Scenario.model_validate(
        {
            "scenario_id": "S99",
            "initial_state": {
                "process_variables": {"applicant_id": "4321"},
                "semantic": {"evidence.contradictory_information": False},
                "bps": {"activities.T1.duration_multiplier": 3},
            },
            "perturbations": [{"when": "T1.enter", "bps_change": {"resources.X.capacity": 0}}],
        }
    )
    inputs = build_run_inputs(scenario)
    assert inputs["applicant_id"] == "4321"
    assert inputs["semantic.evidence.contradictory_information"] is False
    perturbations = json.loads(inputs["cugaPerturbations"])
    assert perturbations[0] == {"when": "__start__", "bps_change": {"activities.T1.duration_multiplier": 3}}
    assert perturbations[1]["when"] == "T1.enter"


def test_goal_not_achieved_when_chosen_route_is_not_acceptable(process, mcp_bridge):
    """Found running a real G0 case end to end: a process can satisfy every must/
    must_not/eventually predicate while taking a route acceptable_routes doesn't
    recognize (G0 rejecting a qualifying applicant for lack of a real scoring
    mechanism) — goal_achieved must not report success on exactly that failure."""
    from cuga_flo.evaluation.expected_properties import ExpectedProperties

    trace_path = tempfile.mktemp()
    sink = simpn_trace.TraceSink(trace_path)
    mcp_bridge["hook_action"].update(action="continue")
    mcp_bridge["route_choice"]["flow"] = "f3"  # the "wrong" route for this oracle
    _run(process, mcp_bridge, trace_sink=sink, key="goal-route-mismatch")
    sink.close()

    expected = ExpectedProperties.model_validate({"must": ["T1"], "acceptable_routes": {"f2": 1.0}})
    card = evaluate(trace_path, expected=expected)
    assert card.decision_utility is None  # f3 isn't in acceptable_routes
    assert card.goal_achieved is False


def test_required_intervention_that_never_had_a_hook_to_fire_counts_as_false_negative(process, mcp_bridge):
    """Found running a real G0 case: a G-level config with zero hooks registered at
    all means the required hook can never fire — intervention_counts must still
    count that as a false negative, not silently contribute nothing to recall."""
    from cuga_flo.evaluation.expected_properties import ExpectedProperties

    engine_config = dict(_ENGINE_CONFIG, hooks=[])  # no hooks at all, like G0-G2
    no_hooks_process = engine.ProcessModel(_PROCESS_MODEL, engine_config)

    trace_path = tempfile.mktemp()
    sink = simpn_trace.TraceSink(trace_path)
    mcp_bridge["route_choice"]["flow"] = "f2"
    _run(no_hooks_process, mcp_bridge, trace_sink=sink, key="no-hooks-run")
    sink.close()

    expected = ExpectedProperties.model_validate({"intervention": {"required_after": ["H1"]}})
    card = evaluate(trace_path, expected=expected)
    assert card.intervention_recall == 0.0
    assert card.intervention_f1 == 0.0


def test_scenario_schema_round_trips_the_design_doc_example():
    """Design doc §5's worked example, validated verbatim against the real schema."""
    from cuga_flo.scenarios.schema import Scenario

    scenario = Scenario.model_validate(
        {
            "scenario_id": "S07",
            "initial_state": {
                "semantic": {
                    "services.alternative_verification.available": True,
                    "services.alternative_verification.permitted": True,
                    "evidence.contradictory_information": False,
                }
            },
            "perturbations": [
                {"when": "T2.enter", "bps_change": {"resources.PrimaryVerificationService.capacity": 0}},
                {"when": "T5.complete", "semantic_change": {"evidence.contradictory_information": True}},
            ],
            "expected_properties": {
                "eventually": ["case_resolved"],
                "must": ["valid_verification_before_approval"],
                "must_not": ["approval_without_valid_verification"],
                "intervention": {"required_after": ["contradictory_information"]},
            },
        }
    )
    assert scenario.scenario_id == "S07"
    assert scenario.perturbations[1].when == "T5.complete"
    assert scenario.expected_properties.must == ["valid_verification_before_approval"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

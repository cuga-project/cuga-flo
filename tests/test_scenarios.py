"""
Checks for cuga_flo.scenarios: LLM-assisted authoring and the comparative-analysis
module. No real LLM calls — `author_scenario`'s LLM call is monkeypatched, the same
philosophy as test_remote_agent.py/test_simpn_adapter.py.

Run: uv run pytest tests/test_scenarios.py
"""

import json
import tempfile

import pytest

from cuga_flo.evaluation.metrics import Scorecard
from cuga_flo.scenarios import author
from cuga_flo.scenarios.analysis import aggregate, format_ablation, write_run_table
from cuga_flo.scenarios.schema import Scenario

# ── author.py ────────────────────────────────────────────────────────────────────

_CONTEXT = {
    "elements": {
        "Start": {"type": "startEvent", "name": "Start"},
        "T1": {"type": "task", "name": "T1"},
        "End1": {"type": "endEvent", "name": "End1"},
    },
    "flows": [{"id": "f1", "name": "Approve", "source": "T1", "target": "End1"}],
    "hooks": [{"id": "H1", "location": "f1"}],
    "variables": {"applicant_id": ""},
    "output_variables": [],
}


def test_extract_json_strips_fenced_code_block():
    raw = "```json\n{\"scenario_id\": \"S99\"}\n```"
    assert author._extract_json(raw) == {"scenario_id": "S99"}


def test_extract_json_handles_surrounding_prose():
    raw = "Here you go:\n{\"scenario_id\": \"S99\"}\nHope that helps!"
    assert author._extract_json(raw) == {"scenario_id": "S99"}


def test_check_consistency_flags_unknown_ids():
    scenario = Scenario.model_validate(
        {
            "scenario_id": "S99",
            "perturbations": [{"when": "Nonexistent.enter"}],
            "expected_properties": {
                "acceptable_routes": {"fake_flow": 1.0},
                "intervention": {"required_after": ["fake_hook"]},
            },
        }
    )
    warnings = author.check_consistency(scenario, _CONTEXT)
    assert any("Nonexistent" in w for w in warnings)
    assert any("fake_flow" in w for w in warnings)
    assert any("fake_hook" in w for w in warnings)


def test_check_consistency_passes_for_real_ids():
    scenario = Scenario.model_validate(
        {
            "scenario_id": "S99",
            "perturbations": [{"when": "T1.enter"}],
            "expected_properties": {
                "acceptable_routes": {"f1": 1.0},
                "intervention": {"required_after": ["H1"]},
            },
        }
    )
    assert author.check_consistency(scenario, _CONTEXT) == []


def test_load_app_context_against_the_real_demo_app():
    """No LLM involved — just BPMNParser/FlowConfig, the same parsing
    scripts/build_simpn_app.sh already does."""
    context = author.load_app_context("loan_approval_simpn")
    assert "Activity_0oydey5" in context["elements"]
    assert any(f["id"] == "Flow_0ybszcv" for f in context["flows"])
    assert {h["id"] for h in context["hooks"]} == {"Flow_1e5ztf6", "Flow_0ybszcv"}
    assert "applicant_id" in context["variables"]


def test_author_scenario_with_a_mocked_llm(monkeypatch):
    canned_response = json.dumps(
        {
            "scenario_id": "S99",
            "description": "test",
            "initial_state": {"process_variables": {"applicant_id": "4321"}},
            "perturbations": [{"when": "T1.enter", "semantic_change": {"foo": "bar"}}],
            "expected_properties": {"eventually": ["case_resolved"]},
        }
    )

    async def fake_ask_llm(scenario_id, idea, context):
        return canned_response

    monkeypatch.setattr(author, "_ask_llm", fake_ask_llm)
    monkeypatch.setattr(author, "load_app_context", lambda app_name, repo_root=None: _CONTEXT)

    scenario, warnings = author.author_scenario("whatever_app", "S99", "some idea")
    assert scenario.scenario_id == "S99"
    assert scenario.initial_state.process_variables["applicant_id"] == "4321"
    assert warnings == []  # T1.enter is real in _CONTEXT


def test_author_scenario_surfaces_consistency_warnings_for_invented_ids(monkeypatch):
    canned_response = json.dumps(
        {
            "scenario_id": "S99",
            "perturbations": [{"when": "MadeUpElement.enter"}],
            "expected_properties": {},
        }
    )

    async def fake_ask_llm(scenario_id, idea, context):
        return canned_response

    monkeypatch.setattr(author, "_ask_llm", fake_ask_llm)
    monkeypatch.setattr(author, "load_app_context", lambda app_name, repo_root=None: _CONTEXT)

    _, warnings = author.author_scenario("whatever_app", "S99", "some idea")
    assert any("MadeUpElement" in w for w in warnings)


# ── analysis.py ──────────────────────────────────────────────────────────────────


def _card(goal_achieved=None, decision_utility=None, intervention_f1=None, cycle_time_seconds=None):
    return Scorecard(
        run_id="r",
        process_completed=True,
        process_halted=False,
        halt_reason=None,
        cycle_time_seconds=cycle_time_seconds,
        activity_count=1,
        agent_task_invocations=0,
        agent_gateway_invocations=0,
        agent_hook_invocations=0,
        hook_target_fallbacks=0,
        policy_compliant=None,
        goal_achieved=goal_achieved,
        decision_utility=decision_utility,
        intervention_f1=intervention_f1,
    )


def test_aggregate_computes_rate_and_means():
    results = {
        "G0": [_card(goal_achieved=False, cycle_time_seconds=1.0), _card(goal_achieved=False, cycle_time_seconds=3.0)],
        "G3": [_card(goal_achieved=True, decision_utility=1.0, intervention_f1=1.0, cycle_time_seconds=5.0)],
    }
    aggs = {a.config: a for a in aggregate(results)}
    assert aggs["G0"].goal_achievement_rate == 0.0
    assert aggs["G0"].mean_cycle_time_seconds == 2.0
    assert aggs["G3"].goal_achievement_rate == 1.0
    assert aggs["G3"].mean_decision_utility == 1.0


def test_format_ablation_reports_only_pairs_present():
    results = {
        "G0": [_card(goal_achieved=False)],
        "G3": [_card(goal_achieved=True)],
    }
    text = format_ablation(aggregate(results))
    assert "G0 -> G3: +1.00" in text
    assert "G0 -> G1" not in text  # G1 wasn't run


def test_write_run_table_round_trips_via_csv():
    import csv as csv_module
    import os

    from cuga_flo.scenarios.runner import trace_path_for

    results = {"G0": [_card(goal_achieved=True, cycle_time_seconds=1.5)]}
    trace_dir = tempfile.mkdtemp()
    trace_path = trace_path_for(trace_dir, "S99", "G0", 0)
    with open(trace_path, "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "run_id": "r",
                    "case_id": "r",
                    "source": "simpn",
                    "event_type": "CASE_START",
                    "bpmn_element_id": "Start",
                    "timestamp": 1.0,
                    "payload": {"process_variables": {"applicant_id": "1234", "loan_amount": 1000}},
                }
            )
            + "\n"
        )
        fh.write(
            json.dumps(
                {
                    "run_id": "r",
                    "case_id": "r",
                    "source": "simpn",
                    "event_type": "CASE_COMPLETE",
                    "bpmn_element_id": "End",
                    "timestamp": 2.0,
                    "payload": {"process_variables": {"applicant_id": "1234", "credit_score": 0.84}},
                }
            )
            + "\n"
        )

    path = tempfile.mktemp()
    write_run_table("S99", results, path, trace_dir)
    with open(path, encoding="utf-8") as fh:
        rows = list(csv_module.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["scenario_id"] == "S99"
    assert rows[0]["config"] == "G0"
    assert rows[0]["goal_achieved"] == "True"
    assert rows[0]["input_applicant_id"] == "1234"
    assert rows[0]["input_loan_amount"] == "1000"
    assert rows[0]["output_credit_score"] == "0.84"
    os.remove(trace_path)


def test_write_run_table_blanks_variable_keys_other_runs_dont_have():
    """Different configs/scenarios can reach different process variables (e.g. a
    case rejected at intake never reaches the fields a scored one has) — the
    header is the union across every row, not the intersection, and a row missing
    a key gets a blank cell rather than shrinking the whole table's columns."""
    import csv as csv_module

    from cuga_flo.scenarios.runner import trace_path_for

    def _write_trace(trace_dir, config, inputs, outputs):
        path = trace_path_for(trace_dir, "S99", config, 0)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {"run_id": "r", "case_id": "r", "source": "simpn", "event_type": "CASE_START",
                     "bpmn_element_id": "Start", "timestamp": 1.0, "payload": {"process_variables": inputs}}
                ) + "\n"
            )
            fh.write(
                json.dumps(
                    {"run_id": "r", "case_id": "r", "source": "simpn", "event_type": "CASE_COMPLETE",
                     "bpmn_element_id": "End", "timestamp": 2.0, "payload": {"process_variables": outputs}}
                ) + "\n"
            )

    trace_dir = tempfile.mkdtemp()
    _write_trace(trace_dir, "G0", {"applicant_id": "1234"}, {"credit_score": 0.84})
    _write_trace(trace_dir, "G1", {"applicant_id": "5678", "only_in_g1": "x"}, {"decision": "reject"})

    results = {
        "G0": [_card(goal_achieved=True)],
        "G1": [_card(goal_achieved=False)],
    }
    path = tempfile.mktemp()
    write_run_table("S99", results, path, trace_dir)
    with open(path, encoding="utf-8") as fh:
        rows = {r["config"]: r for r in csv_module.DictReader(fh)}

    assert rows["G0"]["input_only_in_g1"] == ""
    assert rows["G0"]["output_decision"] == ""
    assert rows["G1"]["input_only_in_g1"] == "x"
    assert rows["G1"]["output_credit_score"] == ""


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

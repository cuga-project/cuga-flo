"""
Checks for `loan_approval_simpn`'s own native formula (`config/formulas.py`) —
business logic that belongs to this one app, not to the generic SimPN adapter
(see tests/test_simpn_adapter.py for that; see
cuga_flo.adapters.simpn.runtime.formulas for why the split exists). Loaded the
same way the Simulation Runtime Service loads it at startup — by file path, via
load_registry — rather than a plain import, so this also exercises the real
loading path, not just the formula's own logic.

Run: uv run pytest tests/test_loan_approval_simpn_formulas.py
"""

from pathlib import Path

import pytest

from cuga_flo.adapters.simpn.runtime.formulas import load_registry

_FORMULAS_PATH = (
    Path(__file__).parent.parent / "applications" / "loan_approval_simpn" / "config" / "formulas.py"
)
_REGISTRY = load_registry(_FORMULAS_PATH)
credit_score_formula = _REGISTRY["credit_score_formula"]


def test_registry_is_loaded_from_the_apps_own_file():
    assert set(_REGISTRY) == {"credit_score_formula"}


def test_rewards_a_well_qualified_applicant():
    result = credit_score_formula(
        {
            "applicant_id": "A12345",
            "credit_history": "Excellent payment history, no defaults",
            "loan_amount": 1000,
            "income": 75000,
            "employment_years": 5,
        }
    )
    assert result["credit_score"] > 0.6  # must clear the gateway's approval threshold


def test_does_not_misread_a_negated_keyword():
    """Found live: 'no defaults' contains the substring 'default', so a naive
    negative-keyword check used to misread a *good* history as bad — this is the
    regression test for that fix."""
    good = credit_score_formula(
        {"applicant_id": "A1", "credit_history": "no defaults, no late payments", "loan_amount": 1000, "income": 75000, "employment_years": 5}
    )
    bad = credit_score_formula(
        {"applicant_id": "A1", "credit_history": "multiple missed payments and a default", "loan_amount": 1000, "income": 75000, "employment_years": 5}
    )
    assert good["credit_score"] > bad["credit_score"]


def test_edge_cases_match_the_policy_literally():
    assert credit_score_formula({"credit_history": "x", "loan_amount": 1, "income": 1}) == {
        "credit_score": 0.0,
        "rejection_reason": "identity_unverified",
    }
    assert credit_score_formula({"applicant_id": "A1", "loan_amount": 1, "income": 1}) == {
        "credit_score": 0.3,
        "no_history": True,
    }


def test_applies_the_large_loan_stricter_threshold():
    small = credit_score_formula(
        {"applicant_id": "A1", "credit_history": "excellent", "loan_amount": 1000, "income": 75000, "employment_years": 5}
    )
    large = credit_score_formula(
        {"applicant_id": "A1", "credit_history": "excellent", "loan_amount": 60000, "income": 75000, "employment_years": 5}
    )
    assert large["credit_score"] < small["credit_score"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

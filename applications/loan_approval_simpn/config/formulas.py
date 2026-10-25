"""
`loan_approval_simpn`'s own native formulas — deterministic computations its
`mode: native` tasks can run inside the Simulation Runtime Service, with no MCP
call and no CUGA FLO process involved at all. Staged into
`build/simpn/loan_approval_simpn/formulas.py` by `scripts/build_simpn_app.sh` and
loaded there by `cuga_flo.adapters.simpn.runtime.formulas.load_registry` — see
that module's docstring for why this lives here, per-app, rather than in the
generic adapter code.

This exists for the same reason the design doc frames "G0 — Legacy workflow" as
following "conventional BPMN/BPS behavior," not as doing nothing: a real legacy
system has *some* deterministic business rule, even a crude one, and G0 having no
such rule for the credit check was a gap, not a deliberate design choice
(`credit_score` just stayed at its YAML default of 0.0, making every G0 run reject
unconditionally regardless of scenario — correct as an observation about G0's
limits, but an overstatement of how badly a real legacy system would do).
"""

from __future__ import annotations

from typing import Any, Callable


def credit_score_formula(process_variables: dict[str, Any]) -> dict[str, Any]:
    """
    A crude, deterministic approximation of
    `policies/task-credit_check.md`'s scoring rubric, for the `check credit` task
    (`Activity_0oydey5`) when run natively (G0) — i.e. with no LLM at all.

    The policy's two edge cases that are already deterministic rules (identity
    unverified, no credit history) are implemented literally. The three factors
    the policy asks an LLM to *judge* from free text (payment history, credit
    mix, recent enquiries) have no structured data in this demo's process
    variables to judge from either way — this uses a crude keyword heuristic for
    payment history (the one factor `credit_history`'s free text actually
    describes) and a flat neutral 0.5 for credit mix / recent enquiries, matching
    what the real Task Agent itself typically assigns when neither factor is
    mentioned (see docs/running-simulation-experiments.md's worked transcript).
    Debt-to-income and length-of-credit-history (via `employment_years`, the same
    proxy the LLM uses) are computed directly from structured fields — no
    approximation needed there.

    ponytail: this is intentionally crude, not a real credit-scoring model — its
    only job is to give G0 *some* deterministic business logic instead of none,
    so a G0-vs-G1+ comparison measures "conventional rule vs. real judgment," not
    "no rule vs. real judgment." Upgrade path: replace with a real scoring table
    if G0's baseline needs to be more faithful than this.
    """
    applicant_id = str(process_variables.get("applicant_id") or "").strip()
    if not applicant_id:
        return {"credit_score": 0.0, "rejection_reason": "identity_unverified"}

    credit_history = str(process_variables.get("credit_history") or "").strip()
    if not credit_history:
        return {"credit_score": 0.3, "no_history": True}

    loan_amount = float(process_variables.get("loan_amount") or 0)
    income = float(process_variables.get("income") or 0)
    employment_years = float(process_variables.get("employment_years") or 0)

    # Positive phrases checked FIRST and given priority: "no defaults" contains the
    # substring "default", so a naive negative-keyword-only check misreads a good
    # history as bad — found by actually running this formula against this demo's
    # own "Excellent payment history, no defaults" text before trusting it.
    lowered = credit_history.lower()
    positive_phrases = ("excellent", "good standing", "strong", "clean record", "no default", "no late", "no missed")
    negative_keywords = ("default", "late", "missed", "delinquent", "collection", "bankrupt")
    if any(p in lowered for p in positive_phrases):
        payment_history = 0.95
    elif any(w in lowered for w in negative_keywords):
        payment_history = 0.2
    else:
        payment_history = 0.6  # genuinely unclear text — neither signal present

    dti_ratio = loan_amount / income if income > 0 else 1.0
    debt_to_income = max(0.0, 1.0 - dti_ratio)

    length_of_history = min(employment_years / 7.0, 1.0)

    credit_mix = 0.5
    recent_enquiries = 0.5

    # policy's "loan_amount > 50,000" edge case: debt-to-income gets +10pp, taken
    # evenly from the other four factors so the weights still sum to 1.0.
    weights = {
        "payment_history": 0.35,
        "debt_to_income": 0.30,
        "length_of_history": 0.15,
        "credit_mix": 0.10,
        "recent_enquiries": 0.10,
    }
    if loan_amount > 50_000:
        weights["debt_to_income"] += 0.10
        for key in ("payment_history", "length_of_history", "credit_mix", "recent_enquiries"):
            weights[key] -= 0.10 / 4

    score = (
        weights["payment_history"] * payment_history
        + weights["debt_to_income"] * debt_to_income
        + weights["length_of_history"] * length_of_history
        + weights["credit_mix"] * credit_mix
        + weights["recent_enquiries"] * recent_enquiries
    )
    return {"credit_score": round(min(max(score, 0.0), 1.0), 2)}


REGISTRY: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "credit_score_formula": credit_score_formula,
}

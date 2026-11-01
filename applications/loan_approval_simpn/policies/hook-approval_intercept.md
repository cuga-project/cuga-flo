# Approval Path Hook Policy — Flow_0ybszcv

## Purpose

This hook intercepts the approval path (the yes-flow from the credit decision gateway)
immediately before the loan grant task. It is the integration point for regulatory
overrides that must redirect approved applications to rejection before the loan is issued,
and for halting a case outright when the applicant's record is internally inconsistent.

Check Rule 2 before Rule 1 — a contradiction in the record is a reason to halt
regardless of applicant ID, so it must not be skipped just because Rule 1 doesn't
match.

## Rule 2 — Contradictory evidence discovered

Check the `semantic.evidence.contradictory_information` process variable. If it is
exactly `true`, the applicant's record contains an internal contradiction discovered
after the credit decision was already made — granting the loan on an inconsistent
record is itself a compliance risk, independent of credit score or applicant ID.

Use action `terminate` with `message` set to
`"halted: contradictory information discovered after approval"`. Do not grant the
loan. Do not skip to another node — this case is halted outright, not redirected to
rejection, since the inconsistency itself (not a credit judgment) is what's being
flagged. Do not evaluate Rule 1 if this rule applies.

## Rule 1 — Regulatory restriction (exact applicant ID)

Only applicant ID `4321` (the four-digit string "4321", nothing else) is subject to a regulatory restriction.

**Resolve the applicant ID from these sources in order:**
1. The `applicant_id` process variable — use it if it is a non-empty string.
2. The `_user_message` — scan for an explicit applicant ID (e.g. "applicant id 4321", "applicant_id 4321", "for applicant 4321"). Extract the numeric or alphanumeric token that directly follows such a label.

**Exact match only**: compare the resolved ID against `"4321"` as a full string. Do NOT trigger this rule for any other value, including IDs that contain the digits 4, 3, 2, or 1 in any order or subset (e.g. `23`, `321`, `4320`, `43210` must all pass through as `continue`).

If the resolved applicant ID is exactly `"4321"`, use action `skip_to` with `target_node` set to `Activity_131ar38` (the rejection task node), and set the following process variables via `state_updates`:
- `rejection_reason`: `"regulatory_policy_restriction"`
- `policy_override_active`: `true`

These variables signal to the rejection handler that the rejection is intentional and policy-compliant, not a credit-score outcome.

## Default

If `semantic.evidence.contradictory_information` is not exactly `true`, and the
applicant ID is not exactly `"4321"` — including `"23"`, `"321"`, `"4320"`, and any
other value — return `continue` with no `state_updates`, no `target_node`, and no
`message`. Do not modify any process variables.

"""
Perturbation matching/application for one simulated case.

Design-doc §6: a perturbation is `{"when": "<element_id>.<enter|complete>",
"bps_change": {...}, "semantic_change": {...}}`. A synthetic `"when": "__start__"`
entry (built by the scenario runner from a scenario's `initial_state.bps`) is applied
once, unconditionally, before the case's first token is seeded.

Stdlib only — no `cuga_flo` import here, see the package docstring.

ponytail: `bps_change` supports exactly one key shape —
`activities.<element_id>.duration_multiplier` — because that's the only BPS lever
with a real effect given engine.py's one-`DecProblem`-per-instance scope (no
cross-case resource contention to perturb). Anything else is recorded, not applied,
in `state["_unsupported_perturbations"]` — visible to the evaluator, never silently
dropped. Upgrade path: once resource contention is modeled, extend here.

`semantic_change`/`initial_state.semantic` land as `semantic.<dotted.key>` entries in
`process_variables` — the "thin semantic world" layer is deliberately just more
process variables, not a new concept this engine has to know about; see
`cuga_flo.scenarios.schema`'s docstring for the main-process-side half of this.
"""

from __future__ import annotations

import re
from typing import Any

_DURATION_MULTIPLIER_RE = re.compile(r"^activities\.(.+)\.duration_multiplier$")


def parse_trigger(when: str) -> tuple[str, str]:
    """`"Activity_0oydey5.enter"` -> `("Activity_0oydey5", "enter")`."""
    element_id, _, point = when.rpartition(".")
    return element_id, point


def _apply_bps_change(state: dict, key: str, value: Any, unsupported: list[str]) -> None:
    match = _DURATION_MULTIPLIER_RE.match(key)
    if match:
        state["_duration_overrides"][match.group(1)] = value
    else:
        unsupported.append(key)


def _apply_semantic_change(state: dict, key: str, value: Any) -> None:
    state["process_variables"][f"semantic.{key}"] = value


def apply(state: dict, perturbation: dict) -> dict:
    """
    Apply one perturbation's bps_change/semantic_change to `state`. Returns the
    payload for the PERTURBATION_APPLIED trace event — including `unsupported_bps_keys`
    for any `bps_change` key this engine's bounded BPS-lever set doesn't cover, so an
    unsupported request is visible to the evaluator rather than silently dropped.
    """
    unsupported: list[str] = []
    for key, value in (perturbation.get("bps_change") or {}).items():
        _apply_bps_change(state, key, value, unsupported)
    for key, value in (perturbation.get("semantic_change") or {}).items():
        _apply_semantic_change(state, key, value)
    if unsupported:
        state["_unsupported_perturbations"].append({"when": perturbation.get("when"), "keys": unsupported})
    return {
        "when": perturbation.get("when"),
        "bps_change": perturbation.get("bps_change") or {},
        "semantic_change": perturbation.get("semantic_change") or {},
        "unsupported_bps_keys": unsupported,
    }


def apply_start_perturbations(state: dict, perturbations: list[dict]) -> list[dict]:
    """Applied once, unconditionally, before the first token is seeded. Returns the
    trace payloads for each one actually applied."""
    applied = []
    for index, p in enumerate(perturbations):
        if p.get("when") == "__start__":
            applied.append(apply(state, p))
            state["_applied_perturbations"].add(index)
    return applied


def check_and_apply(
    state: dict, perturbations: list[dict], element_id: str, point: str
) -> list[dict]:
    """Called at every element's enter/complete point. Returns the trace payloads for
    whichever not-yet-applied perturbations matched this (element_id, point)."""
    applied = []
    for index, p in enumerate(perturbations):
        if index in state["_applied_perturbations"]:
            continue
        if parse_trigger(p.get("when", "")) == (element_id, point):
            applied.append(apply(state, p))
            state["_applied_perturbations"].add(index)
    return applied

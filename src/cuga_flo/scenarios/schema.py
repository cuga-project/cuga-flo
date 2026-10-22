"""
The scenario schema — design doc §5/§6: `S = (I, P, E)`, initial conditions,
perturbations during execution, and expected properties.

`E` (expected_properties) is `cuga_flo.evaluation.expected_properties.ExpectedProperties`
— the oracle this schema reuses rather than duplicates, consistent with the earlier
infrastructure pass.

This module is the main-process-side half of the scenario mechanism; the engine-side
half (actually applying a perturbation during a run) is
`cuga_flo.adapters.simpn.runtime.perturbations` — intentionally separate code, no
shared import, because the runtime service cannot import `cuga_flo` (see that
package's docstring). The two halves agree on the wire shape of one perturbation
(`{"when", "bps_change", "semantic_change"}`), not on code.

The "thin semantic world" (design doc §4.2) is deliberately not a new concept here
either: `InitialState.semantic` is just a dict of dotted-path facts that
`cuga_flo.scenarios.runner` flattens into `semantic.<key>` process variables before
the run starts — ordinary process variables an agent's policy can read, nothing the
engine needs special-cased handling for.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from cuga_flo.evaluation.expected_properties import ExpectedProperties


class InitialState(BaseModel):
    """`I` — conditions present before the first token is seeded.

    `process_variables` is a CUGA-FLO-specific extension beyond the design doc's bare
    `semantic:`-only example: a frozen scenario needs to say *which case* is being
    run (applicant id, loan amount, ...), and relying on free-text `_user_message`
    extraction for a reproducible benchmark artifact would reintroduce the very
    non-determinism frozen scenarios exist to remove.
    """

    process_variables: dict[str, Any] = Field(default_factory=dict)
    semantic: dict[str, Any] = Field(default_factory=dict)
    # ponytail: only `activities.<id>.duration_multiplier` has a real effect — see
    # perturbations.py's module docstring for why BPS manipulation is scoped this
    # narrowly (one DecProblem per instance, no cross-case resource contention to
    # perturb). Anything else is recorded as unsupported, not silently dropped.
    bps: dict[str, Any] = Field(default_factory=dict)


class Perturbation(BaseModel):
    """`P` — one scheduled change during execution. `when` is `"<element_id>.<enter|
    complete>"`, matched against the real BPMN element ids of the app this scenario
    is written for (scenarios are app-specific, frozen artifacts — see the design
    doc's "same BPMN model" principle)."""

    when: str
    bps_change: dict[str, Any] = Field(default_factory=dict)
    semantic_change: dict[str, Any] = Field(default_factory=dict)


class Scenario(BaseModel):
    """Root model for a frozen scenario YAML file."""

    scenario_id: str
    description: str = ""
    initial_state: InitialState = Field(default_factory=InitialState)
    perturbations: list[Perturbation] = Field(default_factory=list)
    expected_properties: ExpectedProperties = Field(default_factory=ExpectedProperties)

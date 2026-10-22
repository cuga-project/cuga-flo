"""TraceEvent — the common envelope every unified-trace line conforms to.

Matches the design doc's §10 envelope. `scenario_id`/`configuration`/`repetition`/
`seed` stay `Optional` — they are populated by a scenario-runner, which this pass does
not build; everything else is fully populated today.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

TraceSource = Literal["simpn", "flowable", "kogito", "langgraph", "cuga", "agent", "tool", "policy"]


@dataclass
class TraceEvent:
    run_id: str
    case_id: str
    source: TraceSource
    event_type: str
    bpmn_element_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    process_state: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    experiment_id: str | None = None
    application_id: str | None = None
    scenario_id: str | None = None
    configuration: str | None = None
    repetition: int | None = None
    seed: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

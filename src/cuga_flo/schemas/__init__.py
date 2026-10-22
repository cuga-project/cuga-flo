"""Pydantic schemas for CUGA FLO's two YAML config shapes.

- `app_yaml_schema.py` — a flow/app config (`flow:`, `workflow_engine:`, `tasks:`,
  `gateways:`, `hooks:`, ...). Authoritative reference for what `FlowConfig` reads.
- `supervisor_yaml_schema.py` — a supervisor config (`supervisor:`, `agents:`).

Neither is imported for runtime validation today — `FlowConfig`/supervisor loading
read the raw YAML dict directly — so these exist purely as the documented, checked
shape of each file. Kept as two separate modules, not re-exported flatly here: both
declare their own `AgentType` for unrelated concepts (an app YAML's task-agent kind
vs. a supervisor YAML's agent-binding kind), and flattening them into one namespace
would silently shadow one with the other.
"""

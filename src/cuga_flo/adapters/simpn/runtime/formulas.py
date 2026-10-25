"""
Loads an app's own `formulas.py` — the per-app registry of deterministic
computations a `mode: native` task can run entirely inside this service, with no
MCP call and no CUGA FLO process involved at all. Distinct from `tool:` (an
externally-injected Python callable the LangGraph engine supports — see
`TaskConfig.formula`'s docstring in `src/cuga_flo/schemas/app_yaml_schema.py` for
the full distinction): a formula here is business logic, selected by name from the
app's YAML (`formula: "<name>"`).

This logic belongs to one specific app, not to this generic adapter — the same
reason `simpn_parameters.yaml`, policies, and the BPMN already live under
`applications/<app>/config/` rather than under this package. A single shared
registry here would mean every app's formula gets added to code this package
owns, coupling app-specific content to the generic SimPN engine plumbing.
`scripts/build_simpn_app.sh` stages the app's own `config/formulas.py` (if one
exists — a `formula:` field is optional) into `build/simpn/<app-name>/formulas.py`
alongside its other config; `__main__.py` loads it from there, by path, at
startup.

Stdlib only — no `cuga_flo` import, same isolation rule as the rest of this
package.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Callable


def load_registry(path: Path) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    """Dynamically loads `path` as a module and returns its `REGISTRY` dict. An
    app with no formulas (or no `formulas.py` at all) gets an empty registry —
    any `formula:` reference then traces FORMULA_NOT_FOUND instead of crashing."""
    if not path.exists():
        return {}
    spec = importlib.util.spec_from_file_location("app_formulas", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "REGISTRY", {})

"""
Entry point for the Simulation Runtime Service: `python -m cuga_flo.adapters.simpn.runtime <build-dir>`.

<build-dir> is produced by scripts/build_simpn_app.sh — it contains:
    process_model.json    BPMNProcess.to_dict(), dumped at build time (main venv)
    engine_config.json    FlowConfig.to_engine_config(), dumped at build time
    simpn_parameters.yaml BPS parameters (durations, ...) — this app's own file
    formulas.py           this app's own native formulas, if it has any — optional

Needs pyyaml (unlike the rest of this package) solely to read that one YAML file;
everything else here is stdlib + simpn.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from . import engine, formulas, server


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_dir", help="Directory staged by scripts/build_simpn_app.sh")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8082)
    args = parser.parse_args(argv)

    build_dir = Path(args.build_dir)
    process_model = json.loads((build_dir / "process_model.json").read_text())
    engine_config = json.loads((build_dir / "engine_config.json").read_text())
    simpn_params_path = build_dir / "simpn_parameters.yaml"
    simpn_params = yaml.safe_load(simpn_params_path.read_text()) if simpn_params_path.exists() else {}
    formula_registry = formulas.load_registry(build_dir / "formulas.py")

    process = engine.ProcessModel(process_model, engine_config, formula_registry=formula_registry)
    server.serve(process, simpn_params or {}, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

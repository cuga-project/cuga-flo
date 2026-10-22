"""`cuga-flo evaluate` — prints the deterministic-evaluator scorecard for one trace."""

from __future__ import annotations

import json

import yaml

from cuga_flo.evaluation.expected_properties import ExpectedProperties
from cuga_flo.evaluation.metrics import evaluate


def run(trace_path: str, expected_path: str | None = None, run_id: str | None = None) -> int:
    expected = None
    if expected_path:
        with open(expected_path, encoding="utf-8") as fh:
            expected = ExpectedProperties.model_validate(yaml.safe_load(fh))

    card = evaluate(trace_path, expected=expected, run_id=run_id)
    print(json.dumps(card.to_dict(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_path")
    parser.add_argument("--expected", dest="expected_path")
    parser.add_argument("--run-id", dest="run_id")
    args = parser.parse_args()
    raise SystemExit(run(args.trace_path, args.expected_path, args.run_id))

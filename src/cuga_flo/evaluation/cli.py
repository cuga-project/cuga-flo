"""`cuga-flo evaluate` — prints the deterministic-evaluator scorecard for one trace."""

from __future__ import annotations

import json

import yaml

from cuga_flo.evaluation.expected_properties import ExpectedProperties
from cuga_flo.evaluation.metrics import evaluate


def run(
    trace_path: str,
    expected_path: str | None = None,
    run_id: str | None = None,
    simpn_parameters_path: str | None = None,
) -> int:
    expected = None
    if expected_path:
        with open(expected_path, encoding="utf-8") as fh:
            expected = ExpectedProperties.model_validate(yaml.safe_load(fh))

    rates = None
    if simpn_parameters_path:
        with open(simpn_parameters_path, encoding="utf-8") as fh:
            rates = yaml.safe_load(fh) or {}

    card = evaluate(trace_path, expected=expected, run_id=run_id, rates=rates)
    print(json.dumps(card.to_dict(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_path")
    parser.add_argument("--expected", dest="expected_path")
    parser.add_argument("--run-id", dest="run_id")
    parser.add_argument(
        "--simpn-parameters", dest="simpn_parameters_path",
        help="simpn_parameters.yaml with hourly_rate/model_token_cost — unlocks cost metrics.",
    )
    args = parser.parse_args()
    raise SystemExit(run(args.trace_path, args.expected_path, args.run_id, args.simpn_parameters_path))

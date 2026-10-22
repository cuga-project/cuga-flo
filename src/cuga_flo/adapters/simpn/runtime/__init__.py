"""
The standalone Simulation Runtime Service.

Deliberately dependency-isolated from the rest of `cuga_flo` — see
`src/cuga_flo/adapters/simpn/__init__.py`. Nothing in this subpackage imports
`cuga_flo` or `cuga`; it only imports `simpn` (the Petri-net library) plus the
standard library. It is meant to run in its own venv, built per-app by
`scripts/build_simpn_app.sh`, not inside CUGA FLO's own process.
"""

"""SimPN workflow-engine adapter — a peer to the Flowable/Kogito adapters.

Unlike Flowable/Kogito, which proxy to an existing third-party engine, the standalone
service under ``runtime/`` is something CUGA FLO builds itself on top of the `simpn`
Petri-net simulation library (https://github.com/bpogroup/simpn). It is intentionally
dependency-isolated from this package: `simpn` pulls in a full GUI/plotting stack
(PyQt6, pygame, matplotlib, python-igraph) that has no business in CUGA FLO's own
process, so the service runs in its own venv, built per-app by
scripts/build_simpn_app.sh. ``proxy.py`` (this package) is the only module here that
imports anything from `cuga_flo` — `runtime/` does not.
"""

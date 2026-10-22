#!/usr/bin/env bash
#
# Generate and build the standalone Simulation Runtime Service for a CUGA FLO app.
#
# Unlike Kogito/Flowable, this isn't a third-party engine CUGA FLO proxies to — the
# service is built from this repo's own src/cuga_flo/adapters/simpn/runtime/ code.
# What this script does:
#   1. dumps this app's BPMN (via BPMNParser) and engine config (via FlowConfig) to
#      JSON, using the MAIN venv (which has cuga_flo + its dependencies installed);
#   2. creates a FRESH, ISOLATED venv for the service itself, installing only
#      `simpn` + `pyyaml` (see runtime/requirements.txt) — `simpn` pulls in a full
#      GUI/plotting stack (PyQt6, pygame, matplotlib, python-igraph) that has no
#      business in CUGA FLO's own process;
#   3. generates a run.sh that runs the service's code (still part of the cuga_flo
#      package on disk) under that isolated venv's interpreter, via PYTHONPATH —
#      not a real `pip install` of cuga_flo, since runtime/'s own modules and their
#      parent __init__.py files import nothing beyond simpn/pyyaml/stdlib (verified
#      before writing this script) — so pointing PYTHONPATH at src/ is sufficient
#      and avoids installing cuga_flo's own (heavy) dependencies a second time.
#
# Usage:
#   scripts/build_simpn_app.sh <app-name> [--out DIR] [--port N] [--no-build] [--clean]
#
# Examples:
#   scripts/build_simpn_app.sh loan_approval_simpn
#   scripts/build_simpn_app.sh loan_approval_simpn --port 8083 --out /tmp/svc
#
# Then:
#   <out>/run.sh                       # generated; starts the service
#   source .venv/bin/activate && cuga-flo start <app-name>

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$REPO_ROOT/src/cuga_flo/adapters/simpn/runtime"
APPS_DIR="$REPO_ROOT/applications"

die() { echo "error: $*" >&2; exit 1; }

# ── arguments ────────────────────────────────────────────────────────────────

[[ $# -ge 1 ]] || die "usage: $(basename "$0") <app-name> [--config PATH] [--out DIR] [--port N] [--no-build] [--clean]"

APP_NAME="$1"; shift
CONFIG_OVERRIDE=""
OUT_DIR=""
HTTP_PORT=""
RUN_BUILD=1
CLEAN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)   CONFIG_OVERRIDE="${2:-}"; shift 2 ;;
        --out)      OUT_DIR="${2:-}"; shift 2 ;;
        --port)     HTTP_PORT="${2:-}"; shift 2 ;;
        --no-build) RUN_BUILD=0; shift ;;
        --clean)    CLEAN=1; shift ;;
        *)          die "unknown option: $1" ;;
    esac
done

APP_DIR="$APPS_DIR/$APP_NAME"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/build/simpn/$APP_NAME}"

[[ -d "$APP_DIR" ]] || die "no app '$APP_NAME' under $APPS_DIR"

# --config overrides the default <app-name>_config.yaml naming — used by the
# scenario runner to build G0-G3 autonomy-configuration variants that live
# alongside the app's default config, sharing the same BPMN model and policies
# (see applications/loan_approval_simpn/config/G0_config.yaml and siblings).
if [[ -n "$CONFIG_OVERRIDE" ]]; then
    CONFIG_YAML="$CONFIG_OVERRIDE"
else
    CONFIG_YAML="$APP_DIR/config/${APP_NAME}_config.yaml"
fi
[[ -f "$CONFIG_YAML" ]] || die "expected $CONFIG_YAML (see loan_approval_simpn for the naming convention)"

SIMPN_PARAMS="$APP_DIR/config/simpn_parameters.yaml"
[[ -f "$SIMPN_PARAMS" ]] || die "no $SIMPN_PARAMS — see loan_approval_simpn for a worked example"

# Port: --port wins, else the app's workflow_engine.url, else 8082. Same scoped-awk
# approach as build_kogito_app.sh, for the same reason (a bare `url:` grep also
# matches remote_agents[].url).
if [[ -z "$HTTP_PORT" ]]; then
    HTTP_PORT="$(awk '
        FNR==1 { in_block=0 }
        /^workflow_engine:[[:space:]]*$/ { in_block=1; next }
        in_block && /^[^[:space:]]/ { in_block=0 }
        in_block
    ' "$CONFIG_YAML" 2>/dev/null \
                 | grep -oE 'url:[[:space:]]*https?://[^:]+:[0-9]+' \
                 | grep -oE '[0-9]+$' | head -1 || true)"
    HTTP_PORT="${HTTP_PORT:-8082}"
fi

# ── main-venv python (for dumping process_model.json / engine_config.json) ──────

MAIN_PY="$REPO_ROOT/.venv/bin/python3"
[[ -x "$MAIN_PY" ]] || MAIN_PY="python3"
"$MAIN_PY" -c "import cuga_flo" 2>/dev/null \
    || die "cuga_flo is not importable via $MAIN_PY — run 'source .venv/bin/activate' first"

# ── scaffold ─────────────────────────────────────────────────────────────────

[[ $CLEAN -eq 1 ]] && rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

"$MAIN_PY" - "$CONFIG_YAML" "$OUT_DIR" <<'PY'
import json
import sys
from cuga_flo.engine.bpmn_parser import BPMNParser
from cuga_flo.engine.flow_config import FlowConfig

config_yaml, out_dir = sys.argv[1], sys.argv[2]
cfg = FlowConfig.from_yaml(config_yaml)
bpmn = BPMNParser().parse_file(cfg.get_bpmn_file())

with open(f"{out_dir}/process_model.json", "w") as f:
    json.dump(bpmn.to_dict(), f)
with open(f"{out_dir}/engine_config.json", "w") as f:
    json.dump(cfg.to_engine_config(), f)
print(f"  model   {bpmn.id} — {len(bpmn.elements)} elements, {len(bpmn.flows)} flows")
PY

cp "$SIMPN_PARAMS" "$OUT_DIR/simpn_parameters.yaml"

echo "  app     $APP_NAME"
echo "  out     $OUT_DIR"
echo "  port    $HTTP_PORT"

# ── isolated venv ────────────────────────────────────────────────────────────

if [[ $RUN_BUILD -eq 0 ]]; then
    echo "scaffolded (--no-build); install deps with:"
    echo "  python3 -m venv $OUT_DIR/.venv && $OUT_DIR/.venv/bin/pip install -r $RUNTIME_DIR/requirements.txt"
    exit 0
fi

echo "building isolated venv (this pulls in simpn's GUI/plotting deps — PyQt6, pygame, matplotlib — just for this service, not for CUGA FLO's own venv)..."
python3 -m venv "$OUT_DIR/.venv"
if ! "$OUT_DIR/.venv/bin/pip" install -q -r "$RUNTIME_DIR/requirements.txt" > "$OUT_DIR/build.log" 2>&1; then
    echo "pip install FAILED — see $OUT_DIR/build.log" >&2
    tail -20 "$OUT_DIR/build.log" >&2
    exit 1
fi

cat > "$OUT_DIR/run.sh" <<RUNSH
#!/usr/bin/env bash
# Generated by scripts/build_simpn_app.sh — starts the $APP_NAME Simulation Runtime Service.
set -euo pipefail
export PYTHONPATH="$REPO_ROOT/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$OUT_DIR/.venv/bin/python3" -m cuga_flo.adapters.simpn.runtime "$OUT_DIR" --port "$HTTP_PORT" "\$@"
RUNSH
chmod +x "$OUT_DIR/run.sh"

cat <<EOF

built. run it with:

  $OUT_DIR/run.sh

then, with the service up:

  source .venv/bin/activate && cuga-flo start $APP_NAME
EOF

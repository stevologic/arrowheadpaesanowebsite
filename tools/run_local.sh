#!/usr/bin/env bash
# Arrowhead Paesano — generate the Chiefs Narrative locally (macOS / Linux).
#
# Usage:
#   ./tools/run_local.sh                    # auto-detect provider, regenerate, rebuild site
#   ./tools/run_local.sh --provider offline
#   ./tools/run_local.sh --no-build         # regenerate data only
#   ./tools/run_local.sh --serve            # regenerate, then run the Hugo dev server
#
# Reads tools/.env if present. Requires Python 3.10+ and (for build/serve) Node + hugo-bin.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
cd "$REPO_DIR"

PROVIDER=""
DO_BUILD=1
DO_SERVE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --provider) PROVIDER="$2"; shift 2 ;;
    --no-build) DO_BUILD=0; shift ;;
    --serve) DO_SERVE=1; shift ;;
    *) echo "unknown arg: $1"; exit 1 ;;
  esac
done

# Load tools/.env if present.
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  echo "Loading environment from tools/.env"
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
  set +a
fi

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY="python"

"$PY" -m pip install -q -r "$SCRIPT_DIR/requirements.txt"

GEN_ARGS=(-m tools.chiefs_narrative.generate)
if [[ -n "$PROVIDER" ]]; then GEN_ARGS+=(--provider "$PROVIDER"); fi
echo "Generating the Chiefs Narrative..."
"$PY" "${GEN_ARGS[@]}"

HUGO="$REPO_DIR/node_modules/.bin/hugo"
[[ -x "$HUGO" ]] || HUGO="hugo"

if [[ "$DO_SERVE" == "1" ]]; then
  exec "$HUGO" server --bind 127.0.0.1 --port 1515 --baseURL http://localhost:1515/ --disableFastRender --renderToMemory
elif [[ "$DO_BUILD" == "1" ]]; then
  "$HUGO" --gc --minify
  echo "Site built to dist/."
fi

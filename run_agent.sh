#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

OUTPUT_DIR="agent/output"
LOG_DIR="$OUTPUT_DIR/logs"
VENV_PYTHON="${JOURNALMIND_PYTHON:-.venv/bin/python}"
SWARM_TIMEOUT_SECONDS="${JOURNALMIND_SWARM_TIMEOUT_SECONDS:-3600}"
RUN_ID="$(date +"%Y%m%d-%H%M%S")"
RUNNER_LOG="$LOG_DIR/run-$RUN_ID.runner.log"

fail() {
  echo "ERROR: $1"
  exit 1
}

echo "=== JournalMind — WhereToPublish Metadata Enrichment Swarm ==="
echo

mkdir -p "$LOG_DIR" "$OUTPUT_DIR/state"
: > "$RUNNER_LOG"

# --- Preflight checks -------------------------------------------------------
command -v omp >/dev/null 2>&1 || fail "'omp' (oh-my-pi) not found in PATH. Install oh-my-pi first: https://github.com/oh-my-pi/oh-my-pi"
[[ -x "$VENV_PYTHON" ]] || fail "Python virtualenv not found at $VENV_PYTHON"
"$VENV_PYTHON" -c "import polars" >/dev/null 2>&1 || fail "'$VENV_PYTHON' cannot import polars. Run: $VENV_PYTHON -m pip install -r requirements.txt"
[[ "$SWARM_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "JOURNALMIND_SWARM_TIMEOUT_SECONDS must be a positive integer (got '$SWARM_TIMEOUT_SECONDS')."

# --- Run --------------------------------------------------------------------
echo "Agent      : omp (oh-my-pi) swarm orchestrator${JOURNALMIND_MODEL:+ with model override '$JOURNALMIND_MODEL'}"
echo "Timeout    : default ${SWARM_TIMEOUT_SECONDS}s per swarm batch (override per run with --swarm-timeout-seconds)"
echo "Runner log : $RUNNER_LOG"
echo "Python     : $VENV_PYTHON"
echo
echo "Args: $*"
echo

RUNNER_ARGS=("$@" "--swarm-timeout-seconds" "$SWARM_TIMEOUT_SECONDS")
if [[ -n "${JOURNALMIND_MAX_SUGGESTIONS:-}" ]]; then
  RUNNER_ARGS+=("--max-suggestions" "$JOURNALMIND_MAX_SUGGESTIONS")
fi

"$VENV_PYTHON" agent/scripts/run_enrichment.py "${RUNNER_ARGS[@]}" 2>&1 | tee -a "$RUNNER_LOG"

echo
echo "Run finished."
echo "Runner log  : $RUNNER_LOG"
echo "Suggestions : $OUTPUT_DIR/Agent_suggestions.csv"
echo "State       : $OUTPUT_DIR/state/run_state.json"

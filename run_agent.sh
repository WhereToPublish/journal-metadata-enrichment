#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

OUTPUT_DIR="agent/output"
LOG_DIR="$OUTPUT_DIR/logs"
VENV_PYTHON="${JOURNALMIND_PYTHON:-.venv/bin/python}"
AGENT_TIMEOUT_SECONDS="${JOURNALMIND_AGENT_TIMEOUT_SECONDS:-900}"
RUN_ID="$(date +"%Y%m%d-%H%M%S")"
RUNNER_LOG="$LOG_DIR/run-$RUN_ID.runner.log"

fail() {
  echo "ERROR: $1"
  exit 1
}

require_command() {
  local command_name="$1"
  local error_message="$2"

  if ! command -v "$command_name" >/dev/null 2>&1; then
    fail "$error_message"
  fi
}

check_timeout_setting() {
  if [[ ! "$AGENT_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
    fail "JOURNALMIND_AGENT_TIMEOUT_SECONDS must be a positive integer (got '$AGENT_TIMEOUT_SECONDS')."
  fi
}

check_python_ready() {
  if [[ ! -x "$VENV_PYTHON" ]]; then
    fail "Python virtualenv not found at $VENV_PYTHON"
  fi
  if ! "$VENV_PYTHON" -c "import polars" >/dev/null 2>&1; then
    fail "'$VENV_PYTHON' cannot import polars. Run: $VENV_PYTHON -m pip install -r requirements.txt"
  fi
}

run_and_log() {
  local label="$1"
  shift

  echo "[$label]" | tee -a "$RUNNER_LOG"
  "$@" 2>&1 | tee -a "$RUNNER_LOG"
}

echo "=== JournalMind — WhereToPublish Metadata Enrichment Agent ==="
echo

mkdir -p "$LOG_DIR" "$OUTPUT_DIR/state"
: > "$RUNNER_LOG"

require_command "omp" "'omp' (oh-my-pi) not found in PATH. Install oh-my-pi first: https://github.com/oh-my-pi/oh-my-pi"
check_timeout_setting
check_python_ready

echo "Agent     : omp (oh-my-pi)${JOURNALMIND_MODEL:+ with model override '$JOURNALMIND_MODEL'}"
echo "Timeout   : ${AGENT_TIMEOUT_SECONDS}s per journal"
echo "Runner log: $RUNNER_LOG"
echo "Python    : $VENV_PYTHON"
echo

echo "Starting enrichment runner ..."
echo "Args: $*"
echo

RUNNER_ARGS=("$@" "--agent-timeout-seconds" "$AGENT_TIMEOUT_SECONDS")
if [[ -n "${JOURNALMIND_MAX_SUGGESTIONS:-}" ]]; then
  RUNNER_ARGS+=("--max-suggestions" "$JOURNALMIND_MAX_SUGGESTIONS")
fi

run_and_log "run_enrichment.py" "$VENV_PYTHON" agent/scripts/run_enrichment.py "${RUNNER_ARGS[@]}"
run_and_log "issn_alt_name_suggestions.py" "$VENV_PYTHON" agent/scripts/issn_alt_name_suggestions.py

echo
echo "Run finished."
echo "Runner log  : $RUNNER_LOG"
echo "Suggestions : $OUTPUT_DIR/Agent_suggestions.csv"
echo "State       : $OUTPUT_DIR/state/run_state.json"

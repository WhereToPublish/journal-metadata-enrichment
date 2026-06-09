#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

WORKSPACE="agent/workspace"
OUTPUT_DIR="agent/output"
LOG_DIR="$OUTPUT_DIR/logs"
VENV_PYTHON="${JOURNALMIND_PYTHON:-.venv/bin/python}"
MODEL="${JOURNALMIND_MODEL:-ollama/qwen2.5:14b-ctx128k}"
OPENCLAW_TIMEOUT_SECONDS="${JOURNALMIND_OPENCLAW_TIMEOUT_SECONDS:-900}"
JOURNALMIND_MAX_SUGGESTIONS=25
MODEL_TAG="${MODEL#ollama/}"
DOCKER_IMAGE="journalmind-openclaw"
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

require_local_model() {
  if [[ "$MODEL" != ollama/* ]]; then
    fail "JOURNALMIND_MODEL must reference a local Ollama model (expected ollama/<tag>, got '$MODEL')."
  fi
}

check_timeout_setting() {
  if [[ ! "$OPENCLAW_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
    fail "JOURNALMIND_OPENCLAW_TIMEOUT_SECONDS must be a positive integer (got '$OPENCLAW_TIMEOUT_SECONDS')."
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

check_ollama_model() {
  local ollama_models

  ollama_models="$(ollama list 2>/dev/null)" || {
    fail "Ollama is not responding. Start with: ollama serve"
  }
  if ! echo "$ollama_models" | grep -q "^${MODEL_TAG}[[:space:]]"; then
    fail "Model '$MODEL_TAG' not found. Pull with: ollama pull $MODEL_TAG"
  fi
}

check_docker_ready() {
  require_command "docker" "'docker' not found in PATH. Install Docker Desktop: https://www.docker.com/products/docker-desktop"
  if ! docker info >/dev/null 2>&1; then
    fail "Docker daemon is not running. Start Docker Desktop and retry."
  fi
}

build_docker_image_if_needed() {
  if ! docker image inspect "$DOCKER_IMAGE" >/dev/null 2>&1; then
    echo "Building Docker image '$DOCKER_IMAGE' (first run may take a few minutes) ..."
    docker build -t "$DOCKER_IMAGE" .
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

require_command "ollama" "'ollama' not found in PATH."
require_local_model
check_timeout_setting
check_docker_ready
check_ollama_model
check_python_ready
build_docker_image_if_needed

echo "Workspace : $WORKSPACE"
echo "Model     : $MODEL"
echo "Container : $DOCKER_IMAGE"
echo "Timeout   : ${OPENCLAW_TIMEOUT_SECONDS}s per journal"
echo "Runner log: $RUNNER_LOG"
echo "Python    : $VENV_PYTHON"
echo

echo "Starting enrichment runner ..."
echo "Args: $*"
echo

RUNNER_ARGS=("$@" "--openclaw-timeout-seconds" "$OPENCLAW_TIMEOUT_SECONDS" "--max-suggestions" "${JOURNALMIND_MAX_SUGGESTIONS:-5}")

run_and_log "run_enrichment.py" "$VENV_PYTHON" agent/scripts/run_enrichment.py "${RUNNER_ARGS[@]}"
run_and_log "issn_alt_name_suggestions.py" "$VENV_PYTHON" agent/scripts/issn_alt_name_suggestions.py

echo
echo "Run finished."
echo "Runner log  : $RUNNER_LOG"
echo "Suggestions : $OUTPUT_DIR/Agent_suggestions.csv"
echo "State       : $OUTPUT_DIR/state/run_state.json"

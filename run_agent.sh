#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

WORKSPACE="agent/workspace"
DEFAULT_OUTPUT_DIR="agent/output"
DEFAULT_LOG_DIR="$DEFAULT_OUTPUT_DIR/logs"
DEFAULT_STATE_DIR="$DEFAULT_OUTPUT_DIR/state"
VENV_PYTHON="${JOURNALMIND_PYTHON:-.venv/bin/python}"
MODEL="${JOURNALMIND_MODEL:-ollama/qwen3:8b}"
MODEL_TAG="${MODEL#ollama/}"
RUNNER_OUTPUT="$DEFAULT_OUTPUT_DIR/AI_Suggestions.csv"
RUNNER_STATE="$DEFAULT_STATE_DIR/run_state.json"
RUNNER_LOG_DIR="$DEFAULT_LOG_DIR"

parse_runner_paths() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --output)
        RUNNER_OUTPUT="$2"
        shift 2
        ;;
      --output=*)
        RUNNER_OUTPUT="${1#*=}"
        shift
        ;;
      --state)
        RUNNER_STATE="$2"
        shift 2
        ;;
      --state=*)
        RUNNER_STATE="${1#*=}"
        shift
        ;;
      --log-dir)
        RUNNER_LOG_DIR="$2"
        shift 2
        ;;
      --log-dir=*)
        RUNNER_LOG_DIR="${1#*=}"
        shift
        ;;
      *)
        shift
        ;;
    esac
  done
}

parse_runner_paths "$@"

RUN_ID="$(date +"%Y%m%d-%H%M%S")"
MERGED_LOG="$RUNNER_LOG_DIR/run-$RUN_ID.console.log"
OPENCLAW_JSON_LOG="$RUNNER_LOG_DIR/run-$RUN_ID.openclaw.jsonl"
RUNNER_LOG="$RUNNER_LOG_DIR/run-$RUN_ID.runner.log"
DEFAULT_ARGS=(--priorities high,medium --max-suggestions 50)
RUNNER_ARGS=("${DEFAULT_ARGS[@]}" "$@")
OPENCLAW_LOG_PID=""

cleanup() {
  if [[ -n "$OPENCLAW_LOG_PID" ]] && kill -0 "$OPENCLAW_LOG_PID" 2>/dev/null; then
    kill "$OPENCLAW_LOG_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "=== JournalMind — WhereToPublish Metadata Enrichment Agent ==="
echo

mkdir -p "$RUNNER_LOG_DIR" "$(dirname "$RUNNER_STATE")" "$(dirname "$RUNNER_OUTPUT")"

if ! command -v openclaw >/dev/null 2>&1; then
  echo "ERROR: 'openclaw' not found in PATH."
  echo "Install it with: npm install -g openclaw"
  exit 1
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "ERROR: 'ollama' not found in PATH."
  exit 1
fi

if ! ollama list >/dev/null 2>&1; then
  echo "ERROR: Ollama is not responding. Start it with: ollama serve"
  exit 1
fi

OLLAMA_MODELS="$(ollama list)"
if ! grep -q "^${MODEL_TAG}[[:space:]]" <<<"$OLLAMA_MODELS"; then
  echo "ERROR: Model '$MODEL_TAG' is not available in Ollama."
  echo "Pull it first with: ollama pull $MODEL_TAG"
  exit 1
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "ERROR: Python virtualenv not found at $VENV_PYTHON"
  exit 1
fi

if ! "$VENV_PYTHON" -c "import polars" >/dev/null 2>&1; then
  echo "ERROR: '$VENV_PYTHON' cannot import polars."
  echo "Install dependencies with: $VENV_PYTHON -m pip install -r requirements.txt"
  exit 1
fi

echo "Workspace : $WORKSPACE"
echo "Model     : $MODEL"
echo "Logs      : $MERGED_LOG"
echo "Python    : $VENV_PYTHON"
echo

echo "Configuring OpenClaw workspace and model ..."
openclaw config set agents.defaults.workspace "$SCRIPT_DIR/$WORKSPACE"
openclaw config set agents.defaults.model.primary "$MODEL"

echo "Restarting OpenClaw gateway ..."
openclaw gateway install >/dev/null 2>&1 || true
openclaw gateway restart >/dev/null 2>&1 || openclaw gateway start >/dev/null 2>&1

echo "Starting OpenClaw live log capture ..."
openclaw logs --follow --json 2>&1 \
  | tee "$OPENCLAW_JSON_LOG" \
  | sed 's/^/[openclaw] /' \
  | tee -a "$MERGED_LOG" &
OPENCLAW_LOG_PID=$!

echo "Starting enrichment runner ..."
echo "Runner args: ${RUNNER_ARGS[*]}"
echo

"$VENV_PYTHON" agent/scripts/run_enrichment.py "${RUNNER_ARGS[@]}" 2>&1 \
  | tee "$RUNNER_LOG" \
  | sed 's/^/[runner] /' \
  | tee -a "$MERGED_LOG"

echo
echo "Run finished."
echo "Merged log    : $MERGED_LOG"
echo "OpenClaw log  : $OPENCLAW_JSON_LOG"
echo "Runner log    : $RUNNER_LOG"
echo "Suggestions   : $RUNNER_OUTPUT"
echo "State         : $RUNNER_STATE"
echo
echo "To stop the gateway later: openclaw gateway stop"
echo "To revert workspace:      openclaw config unset agents.defaults.workspace"

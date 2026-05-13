#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

WORKSPACE="agent/workspace"
OUTPUT_DIR="agent/output"
LOG_DIR="$OUTPUT_DIR/logs"
VENV_PYTHON="${JOURNALMIND_PYTHON:-.venv/bin/python}"
MODEL="${JOURNALMIND_MODEL:-ollama/qwen3:8b}"
MODEL_TAG="${MODEL#ollama/}"
RUN_ID="$(date +"%Y%m%d-%H%M%S")"
OPENCLAW_LOG="$LOG_DIR/run-$RUN_ID.openclaw.jsonl"
RUNNER_LOG="$LOG_DIR/run-$RUN_ID.runner.log"
OPENCLAW_LOG_PID=""

cleanup() {
  [[ -n "$OPENCLAW_LOG_PID" ]] && kill "$OPENCLAW_LOG_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "=== JournalMind — WhereToPublish Metadata Enrichment Agent ==="
echo

mkdir -p "$LOG_DIR" "$OUTPUT_DIR/state"

if ! command -v openclaw >/dev/null 2>&1; then
  echo "ERROR: 'openclaw' not found in PATH. Install with: npm install -g openclaw"; exit 1
fi
if ! command -v ollama >/dev/null 2>&1; then
  echo "ERROR: 'ollama' not found in PATH."; exit 1
fi
OLLAMA_MODELS="$(ollama list 2>/dev/null)" || { echo "ERROR: Ollama is not responding. Start with: ollama serve"; exit 1; }
if ! echo "$OLLAMA_MODELS" | grep -q "^${MODEL_TAG}[[:space:]]"; then
  echo "ERROR: Model '$MODEL_TAG' not found. Pull with: ollama pull $MODEL_TAG"; exit 1
fi
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "ERROR: Python virtualenv not found at $VENV_PYTHON"; exit 1
fi
if ! "$VENV_PYTHON" -c "import polars" >/dev/null 2>&1; then
  echo "ERROR: '$VENV_PYTHON' cannot import polars. Run: $VENV_PYTHON -m pip install -r requirements.txt"; exit 1
fi

echo "Workspace : $WORKSPACE"
echo "Model     : $MODEL"
echo "Runner log: $RUNNER_LOG"
echo "Python    : $VENV_PYTHON"
echo

echo "Configuring OpenClaw workspace and model ..."
openclaw config set agents.defaults.workspace "$SCRIPT_DIR/$WORKSPACE"
openclaw config set agents.defaults.model.primary "$MODEL"

echo "Restarting OpenClaw gateway ..."
openclaw gateway install >/dev/null 2>&1 || true
openclaw gateway restart >/dev/null 2>&1 || openclaw gateway start >/dev/null 2>&1

echo "Capturing OpenClaw logs to: $OPENCLAW_LOG"
openclaw logs --follow --json >> "$OPENCLAW_LOG" 2>&1 &
OPENCLAW_LOG_PID=$!

echo "Starting enrichment runner ..."
echo "Args: $*"
echo

"$VENV_PYTHON" agent/scripts/run_enrichment.py "$@" 2>&1 | tee "$RUNNER_LOG"

openclaw gateway stop

echo
echo "Run finished."
echo "Runner log  : $RUNNER_LOG"
echo "OpenClaw log: $OPENCLAW_LOG"
echo "Suggestions : $OUTPUT_DIR/Agent_suggestions.csv"
echo "State       : $OUTPUT_DIR/state/run_state.json"
echo
echo "To stop the gateway: openclaw gateway stop"

#!/usr/bin/env bash
# run_agent.sh — Start the JournalMind OpenClaw agent for WhereToPublish enrichment.
#
# Usage:
#   ./run_agent.sh
#
# What it does:
#   1. Activates the Python virtualenv (for gap_analysis.py)
#   2. Points OpenClaw at the project-local workspace
#   3. Restarts (or starts) the OpenClaw gateway
#   4. Opens the WebChat dashboard in your browser
#   5. Prints the task message to paste into the WebChat
#
# To stop the agent: run `openclaw gateway stop`
# To revert to your original workspace: run `openclaw config unset agents.defaults.workspace`

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="$SCRIPT_DIR/agent/workspace"
VENV_PYTHON="/Users/tlatrille/Documents/venv/py312stats/bin/python3"

# ── Preflight checks ────────────────────────────────────────────────────────

echo "=== JournalMind — WhereToPublish Metadata Enrichment Agent ==="
echo ""

# Check OpenClaw is installed
if ! command -v openclaw &>/dev/null; then
  echo "ERROR: 'openclaw' not found in PATH."
  echo "Install it with: npm install -g openclaw"
  exit 1
fi

# Check Ollama is running
if ! ollama list &>/dev/null; then
  echo "WARNING: Ollama does not appear to be running."
  echo "Start it with: ollama serve"
  echo "Then re-run this script."
  echo ""
  echo "Recommended models for 32 GB M2 (choose one):"
  echo "  ollama pull qwen3:8b         # Best tool-calling support, ~5 GB"
  echo "  ollama pull qwen3:14b        # Better reasoning, ~9 GB"
  echo "  ollama pull qwen3:30b-a3b    # Faster MoE, ~18 GB"
  echo ""
  read -rp "Continue anyway? (y/N) " ans
  [[ "${ans,,}" == "y" ]] || exit 1
fi

# Check Python virtualenv
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "WARNING: Python virtualenv not found at $VENV_PYTHON"
  echo "Gap analysis will try system python3 instead."
  VENV_PYTHON="python3"
fi

# ── Configure OpenClaw workspace ────────────────────────────────────────────

echo "Configuring OpenClaw workspace: $WORKSPACE"
openclaw config set agents.defaults.workspace "$WORKSPACE"

# Confirm the model is set; read from openclaw.json directly (openclaw config get is not available)
OPENCLAW_JSON="$HOME/.openclaw/openclaw.json"
CURRENT_MODEL=$(python3 -c "import json,sys; d=json.load(open('$OPENCLAW_JSON')); print(d.get('agents',{}).get('defaults',{}).get('model',{}).get('primary',''))" 2>/dev/null || echo "")
if [[ -z "$CURRENT_MODEL" || "$CURRENT_MODEL" == "null" ]]; then
  echo ""
  echo "No primary model configured. Set one now:"
  echo "  openclaw config set agents.defaults.model.primary ollama/qwen2.5:14b"
  echo ""
  echo "Or for larger models (32 GB M2):"
  echo "  openclaw config set agents.defaults.model.primary ollama/qwen2.5:32b"
  echo ""
  read -rp "Enter model name (e.g. ollama/qwen2.5:14b) or press Enter to skip: " model_name
  if [[ -n "$model_name" ]]; then
    openclaw config set agents.defaults.model.primary "$model_name"
    echo "Model set to: $model_name"
  fi
else
  echo "Model: $CURRENT_MODEL"
fi

# ── Start / restart gateway ──────────────────────────────────────────────────

echo ""
echo "Starting OpenClaw gateway ..."

# Install LaunchAgent if not yet done (idempotent)
openclaw gateway install 2>/dev/null || true

# Restart so the new workspace config is picked up
# (openclaw gateway restart handles both "was running" and "was stopped" cases)
openclaw gateway restart 2>/dev/null || openclaw gateway start 2>/dev/null || true

# Give it a moment to come up
sleep 3

# ── Open dashboard ───────────────────────────────────────────────────────────

echo ""
echo "Opening WebChat dashboard at http://127.0.0.1:18789 ..."
open "http://127.0.0.1:18789" 2>/dev/null || true

# ── Print task message ───────────────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "Paste this message into the WebChat to start the enrichment:"
echo "══════════════════════════════════════════════════════════════"
echo ""
cat <<'MSG'
Start the journal enrichment task for Genetics & Genomics.

1. Run gap_analysis.py to download the latest data and identify missing metadata.
2. Research each HIGH-priority gap first, then MEDIUM-priority gaps.
3. For each journal, check DOAJ, the publisher website, and Scimago.
4. Write all suggestions (with sources and confidence scores) to:
   /Users/tlatrille/Documents/journal-metadata-enrichment/agent/output/AI_Suggestions.csv
5. Checkpoint every 10 journals.
6. Stop when you have ~50 high-confidence suggestions or all HIGH/MEDIUM gaps are processed.
7. End with a brief summary of what was found, what was skipped, and why.
MSG
echo ""
echo "══════════════════════════════════════════════════════════════"
echo ""
echo "To stop the agent later: openclaw gateway stop"
echo "To revert workspace:     openclaw config unset agents.defaults.workspace"

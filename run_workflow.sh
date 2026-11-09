#!/usr/bin/env bash
# run_workflow.sh — start the self-hosted Mistral Studio worker for the
# wheretopublish-enrichment workflow.
#
# The worker registers the workflow in your Mistral Studio workspace; you then
# start executions from the Studio website (console.mistral.ai → Workflows →
# Start Workflow) or from the terminal with trigger_workflow.py. The code runs
# here, on this machine, where the WhereToPublish repo (..) and the Google
# credentials live.
#
# Required environment:
#   MISTRAL_STUDIO_API_KEY   — Studio workspace API key (worker auth + agent calls)
#   GOOGLE_SERVICE_ACCOUNT_KEY   — Path to Google service account JSON file (default: ~/.config/wheretopublish/google_service_account.json)
# Optional:
#   DEPLOYMENT_NAME   — worker name (default: wheretopublish-enrichment);
#       all workers with the same key and DEPLOYMENT_NAME share executions

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_PYTHON="${JOURNALMIND_PYTHON:-.venv/bin/python}"
WORKFLOW_NAME="wheretopublish-enrichment"
export DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-$WORKFLOW_NAME}"

fail() {
  echo "ERROR: $1"
  exit 1
}

[[ -x "$VENV_PYTHON" ]] || fail "Python virtualenv not found at $VENV_PYTHON"
"$VENV_PYTHON" -c "import polars" >/dev/null 2>&1 || fail "'$VENV_PYTHON' cannot import polars. Run: $VENV_PYTHON -m pip install -r requirements.txt"
"$VENV_PYTHON" -c "import mistralai.workflows" >/dev/null 2>&1 || fail "'$VENV_PYTHON' cannot import mistralai.workflows. Run: $VENV_PYTHON -m pip install -r requirements.txt"
[[ -n "${MISTRAL_STUDIO_API_KEY:-}" ]] || fail "MISTRAL_STUDIO_API_KEY is not set. Create a key in the Mistral Studio console (API keys) and export it."
export MISTRAL_API_KEY="$MISTRAL_STUDIO_API_KEY"

echo "=== JournalMind — Mistral Studio workflow worker ==="
echo "Python         : $VENV_PYTHON"
echo "API key        : set (${#MISTRAL_STUDIO_API_KEY} chars) from MISTRAL_STUDIO_API_KEY"
if [[ -n "${GOOGLE_SERVICE_ACCOUNT_KEY:-}" ]]; then
  echo "Google creds   : GOOGLE_SERVICE_ACCOUNT_KEY=$GOOGLE_SERVICE_ACCOUNT_KEY"
else
  echo "Google creds   : default path ~/.config/wheretopublish/google_service_account.json"
fi
echo "Deployment     : ${DEPLOYMENT_NAME:-<default>}"
echo
echo "Keep this terminal open. In Mistral Studio (console.mistral.ai): Workflows →"
echo "'$WORKFLOW_NAME' → Start Workflow, or run: $VENV_PYTHON trigger_workflow.py --help"
echo

exec "$VENV_PYTHON" -m studio.worker

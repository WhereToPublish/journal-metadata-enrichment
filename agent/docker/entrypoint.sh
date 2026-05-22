#!/usr/bin/env bash
# OpenClaw container entrypoint for JournalMind.
#
# 1. Runs `openclaw onboard` to configure the Ollama provider at the Docker host
#    URL, discover models from Ollama, and create auth-profiles.json in the
#    state-dir agentDir (required for --local mode).
# 2. Patches the resulting openclaw.json to use /app/workspace and the correct
#    primary model.
# 3. Removes any stale agents/main/agent/models.json left by a previous local
#    install (empty models list at 127.0.0.1 that would shadow the correct config).
# 4. exec's the given command (openclaw agent --local ...).
#
# Required env vars (set by docker run in openclaw_runtime.py):
#   OPENCLAW_STATE_DIR   – where OpenClaw stores sessions and runtime state
#   JOURNALMIND_MODEL    – e.g. ollama/qwen3:8b
#   OLLAMA_API_KEY       – any non-empty value enables the Ollama provider
set -e

MODEL="${JOURNALMIND_MODEL:-ollama/qwen3:8b}"
MODEL_ID="${MODEL#ollama/}"   # strip "ollama/" prefix → e.g. "qwen3:8b"

# ── 1. Onboard ──────────────────────────────────────────────────────────────
# Connects to Ollama at host.docker.internal:11434, discovers available models,
# writes $OPENCLAW_STATE_DIR/openclaw.json and
#        $OPENCLAW_STATE_DIR/agents/main/agent/auth-profiles.json.
# --skip-health: do not wait for the gateway (we run --local, no gateway needed).
openclaw onboard --non-interactive \
  --auth-choice ollama \
  --custom-base-url "http://host.docker.internal:11434" \
  --custom-model-id "${MODEL_ID}" \
  --accept-risk \
  --skip-health

# ── 2. Patch openclaw.json ───────────────────────────────────────────────────
# Onboard defaults workspace to ~/.openclaw/workspace; override to /app/workspace.
# Also ensure the primary model matches JOURNALMIND_MODEL.
if [[ -n "${OPENCLAW_STATE_DIR:-}" ]]; then
  python3 - << PYEOF
import json, os
path = os.environ['OPENCLAW_STATE_DIR'] + '/openclaw.json'
with open(path) as fh:
    cfg = json.load(fh)
cfg.setdefault('agents', {}).setdefault('defaults', {})['workspace'] = '/app/workspace'
cfg['agents']['defaults'].setdefault('model', {})['primary'] = os.environ.get('JOURNALMIND_MODEL', 'ollama/qwen3:8b')
# Increase the per-agent run timeout (covers the entire journal enrichment session)
cfg['agents']['defaults']['timeoutSeconds'] = 900
# Increase the Ollama provider idle timeout (time without a new token from the LLM)
# The 14B model on local GPU/CPU can take 2-3 min to start generating output
for provider in cfg.get('models', {}).get('providers', {}).values():
    provider['timeoutSeconds'] = 600
with open(path, 'w') as fh:
    json.dump(cfg, fh, indent=2)
PYEOF
fi

# ── 3. Remove stale models.json ──────────────────────────────────────────────
# A previous local install may have left agents/main/agent/models.json with
# baseUrl=127.0.0.1 and models=[].  That file shadows the correct provider
# config written by onboard and causes "Unknown model" errors.
if [[ -n "${OPENCLAW_STATE_DIR:-}" ]]; then
  rm -f "${OPENCLAW_STATE_DIR}/agents/main/agent/models.json"
fi

exec "$@"

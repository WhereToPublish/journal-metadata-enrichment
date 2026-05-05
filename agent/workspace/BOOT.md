# Startup Checklist

On session start, verify:

1. `ollama list` succeeds.
2. The repo root contains `WhereToPublish.github.io/`, `agent/scripts/`, and `agent/output/`.
3. `.venv/bin/python` exists and can import `polars`.

If a required path, model, or dependency is missing, report it clearly and stop rather than guessing.

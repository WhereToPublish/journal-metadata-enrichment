# WhereToPublish Journal Metadata Enrichment

Local OpenClaw + Ollama tooling for generating reviewable journal metadata suggestions for the WhereToPublish project.

## Current Workflow

The current runtime is built around one journal per OpenClaw session.

1. `run_agent.sh` is the only entry point.
2. The launcher uses the repo-local `.venv/bin/python`, checks that `polars` is importable, configures the OpenClaw workspace, and starts live logs.
3. `agent/scripts/gap_analysis.py` refreshes the WhereToPublish pipeline and writes `agent/output/gap_report.json` unless `--skip-gap-analysis` is passed.
4. `agent/scripts/run_enrichment.py` selects journals from the gap report and opens one fresh OpenClaw session per journal.
5. OpenClaw does the journal-level research with tools and returns one JSON object.
6. Python validates that JSON and writes only accepted rows to `agent/output/AI_Suggestions.csv` plus run state and logs.

The system never writes directly to the Google Sheet. Persisted suggestions are always for human review.

## Runtime Boundaries

- Python owns orchestration, gap-report loading, journal selection, deduplication, validation, and persistence.
- OpenClaw owns journal-level browsing, source gathering, and JSON suggestion generation.
- The current agent runtime works only on journals already present in the caller-selected gap backlog.
- The model does not edit CSV or state files in the normal automation path.
- `WhereToPublish.github.io/` is treated as read-only input during enrichment runs.
- Weak, blocked, or contradictory sources become `unresolved` instead of speculative rows.

## Key Files

- `run_agent.sh`: launcher, gateway bootstrap, live log streaming, and `.venv` preflight.
- `requirements.txt`: Python dependencies for the repo-local virtual environment.
- `agent/scripts/gap_analysis.py`: refreshes the WTP pipeline and writes `agent/output/gap_report.json`.
- `agent/scripts/run_enrichment.py`: prompts OpenClaw, loops over journals, and records run state.
- `agent/scripts/openclaw_runtime.py`: runs `openclaw agent --json` and retries invalid non-JSON replies once.
- `agent/scripts/suggestions_io.py`: validates and persists only supported suggestion rows.
- `agent/workspace/`: OpenClaw workspace instructions for the per-journal tool-driven workflow.
- `WhereToPublish.github.io/`: source data and pipeline, treated as input only.

## Requirements

- macOS
- `openclaw` in `PATH`
- `ollama` installed and running
- a local Ollama model available; validated default: `ollama/qwen3:8b`
- a repo-local virtual environment at `.venv`
- Python dependencies installed from `requirements.txt`

Minimum setup:

```bash
.venv/bin/pip install -r requirements.txt
ollama serve
ollama pull qwen3:8b
```

Use `JOURNALMIND_MODEL=ollama/<tag>` to override the default model for one run.

## Quickstart

```bash
./run_agent.sh
```

The launcher:

1. checks `openclaw`, `ollama`, the selected model, and `.venv/bin/python`
2. checks that `.venv/bin/python` can import `polars`
3. configures the OpenClaw workspace and model
4. restarts the gateway and starts `openclaw logs --follow --json`
5. runs `agent/scripts/run_enrichment.py` with `--priorities high,medium --max-suggestions 50`

Live monitoring is via terminal output and the log files under `agent/output/logs/`.

## Useful Examples

```bash
# Reuse the current gap report and process one named journal
./run_agent.sh --skip-gap-analysis --journal "Human Genomics"

# Run the full pipeline refresh, then process one journal
./run_agent.sh --journal "Plant Genetic Resources"

# Process only the first three eligible journals
./run_agent.sh --journal-limit 3

# Override the model for one run
JOURNALMIND_MODEL=ollama/qwen2.5:14b ./run_agent.sh --journal-limit 1
```

All extra arguments are forwarded to `agent/scripts/run_enrichment.py`.

## Validation Rules

Only requested fields are eligible for persistence. The validator rejects rows that are structurally valid JSON but still unsupported, including:

- publisher-as-institution guesses
- institution types without a valid institution value
- `Scimago Journal Title` suggestions that are not true `alt_name` rows
- `Scimago Journal Title` values that normalize to the original journal name
- `APC Euros = 0` rows unless the reasoning explicitly states there is no APC

If no suggestion survives validation, the journal is written as `unresolved`.

## Outputs

Default result files:

- `agent/output/AI_Suggestions.csv`
- `agent/output/gap_report.json`
- `agent/output/state/run_state.json`

Run-level logs:

- `agent/output/logs/run-*.console.log`
- `agent/output/logs/run-*.openclaw.jsonl`
- `agent/output/logs/run-*.runner.log`

Per-journal artifacts:

- `agent/output/logs/enrichment-*.prompt.txt`
- `agent/output/logs/enrichment-*.stdout.json`
- `agent/output/logs/enrichment-*.stderr.log`

Checkpoint files:

- `agent/output/state/checkpoint_suggestions_*.csv`

## Current Runtime Behavior

The current implementation has been verified with real, non-dry runs.

- the full launcher refreshes `agent/output/gap_report.json` and now records `wtp_dir` as `WhereToPublish.github.io`
- the agent stays within the existing gap backlog and does not propose adding new journals
- blocked-source cases such as `Human Genomics` stay unresolved with no persisted rows
- supported cases can persist a narrow subset of requested fields, for example `Business model = Hybrid` for `Plant Genetic Resources`

Known runtime limitation:

- the current OpenClaw environment may not have a working `web_search` backend, so the prompt provides direct official-site, DOAJ, and Scimago lookup URLs and falls back to `unresolved` when those sources are blocked or insufficient

## Stop / Reset

```bash
openclaw gateway stop
openclaw config unset agents.defaults.workspace
```

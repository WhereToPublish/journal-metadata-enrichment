# WhereToPublish Journal Metadata Enrichment

Local OpenClaw + Ollama tooling for generating reviewable journal metadata suggestions for the WhereToPublish project.

## Current Workflow

The current runtime is built around one journal per OpenClaw session.

1. `run_agent.sh` is the only entry point.
2. The launcher uses the repo-local `.venv/bin/python`, checks that `polars` is importable, configures the OpenClaw workspace, and redirects OpenClaw logs directly to a timestamped file.
3. `agent/scripts/gap_analysis.py` verifies that required external data files exist in the WhereToPublish project, downloads **all 10 Google Sheets tabs** (Generalists, Anatomy & Physiology, Cancer, Development, Ecology & Evolution, Genetics & Genomics, Immunology, Molecular & Cellular Biology, Neurosciences, Plants) via the Sheets API, refreshes the WhereToPublish pipeline, and writes `agent/output/gap_report.json` unless `--skip-gap-analysis` is passed. ISSN gaps (`e-ISSN`, `p-ISSN`, `ISSN-L`) are only generated for journals that have **none** of the three ISSN fields — if any ISSN is already known, all three ISSN gaps are suppressed.
4. `agent/scripts/run_enrichment.py` loads remote suggestion keys from the `AI_suggestions` and `AI_suggestions_processed` Google Sheets tabs, then selects journals from the gap report — all journals across all tabs are processed, sorted by priority (high → medium → low) — and opens one fresh OpenClaw session per journal. It stops when `--max-suggestions` valid suggestions have been written (default: 15) or all journals are exhausted. The prompt includes `scimago_by_issn` and `doaj_by_issn` lookup URLs whenever an ISSN is known, so the agent can resolve the Alternative journal name directly from those sources. The `Alternative journal name` gap has the highest priority weight (12), ahead of Business model (10), so it is researched first. The agent is instructed that Business model requires hard evidence (explicit text on the journal page or DOAJ) and that absence of APC mention on a website is **not** evidence that APC = 0.
5. OpenClaw does the journal-level research with tools and returns one JSON object.
6. Python validates that JSON and writes only accepted rows to `agent/output/AI_suggestions.csv` plus run state and logs. Suggestions already present in either the local CSV or the remote Google Sheet tabs are deduplicated before persistence.
7. `agent/scripts/upload_suggestions.py` pushes `AI_suggestions.csv` to the `AI_suggestions` tab of the WhereToPublish Google Sheet. Each row gets a **Status** column (pending / approve / reject); new rows are uploaded as `pending`.

The system never writes directly to the data tabs of the Google Sheet. Persisted suggestions are always staged in `AI_suggestions` for human review.

## Review Workflow

After upload, team members open the `AI_suggestions` tab and set each row's **Status** to:

- `approve` — apply the suggestion to the data tab
- `reject` — discard the suggestion (archived for analysis)
- `pending` — not yet reviewed (default after upload)

Running **WhereToPublish → Apply Reviewed Suggestions** from the Apps Script menu:

- `approve` rows: the suggested value is written to the journal's field in the appropriate data tab, then the row is moved to `AI_suggestions_processed`.
- `reject` rows: the row is moved to `AI_suggestions_processed` without any data change.
- `pending` rows are left untouched.

The `AI_suggestions_processed` archive contains all reviewed suggestions (approve + reject) and can be used to analyze agent performance over time (see `NEXT_STEPS.md`).

## Runtime Boundaries

- Python owns orchestration, gap-report loading, journal selection, deduplication, validation, and persistence.
- OpenClaw owns journal-level browsing, source gathering, and JSON suggestion generation.
- Google Sheets API (service-account auth) is used for downloading sheet tabs, uploading suggestions, and loading remote deduplication keys.
- Before each run, the enrichment pipeline loads remote keys from `AI_suggestions` and `AI_suggestions_processed` to avoid re-generating suggestions that are already under review or have already been processed.
- The current agent runtime works only on journals already present in the caller-selected gap backlog.
- The model does not edit CSV or state files in the normal automation path.
- `WhereToPublish.github.io/` is treated as read-only input during enrichment runs.
- Weak, blocked, or contradictory sources become `unresolved` instead of speculative rows.

## Key Files

- `run_agent.sh`: launcher, gateway bootstrap, live log streaming, and `.venv` preflight.
- `requirements.txt`: Python dependencies for the repo-local virtual environment.
- `WhereToPublish.github.io/scripts/sheets_client.py`: shared Google Sheets API module (auth, download, upload helpers). Used by both the WTP pipeline scripts and the agent scripts.
- `agent/scripts/gap_analysis.py`: verifies required external data files are present in `WhereToPublish.github.io/data_extraction/` (fails fast with a clear error if any are missing), downloads all 10 sheet tabs via the Sheets API, refreshes the WTP pipeline, and writes `agent/output/gap_report.json`.
- `agent/scripts/run_enrichment.py`: loads remote deduplication keys from Google Sheets, prompts OpenClaw per journal, loops over journals, and records run state.
- `agent/scripts/openclaw_runtime.py`: runs `openclaw agent --json` and retries once on non-JSON replies. The retry uses a compact prompt (instructions header stripped, only the Evidence JSON retained) to avoid compounding context overflow that causes the first attempt to fail.
- `agent/scripts/suggestions_io.py`: validates and persists only supported suggestion rows; rejects `Alternative journal name` suggestions with confidence < 0.70 (a wrong alt_name would break the Scimago join for the journal).
- `agent/scripts/upload_suggestions.py`: uploads `AI_suggestions.csv` to the `AI_suggestions` tab with a Status column (pending/approve/reject); all new rows start as `pending`.
- `agent/workspace/`: OpenClaw workspace instructions for the per-journal tool-driven workflow.
- `WhereToPublish.github.io/`: source data and pipeline, treated as input only.
- `WhereToPublish.github.io/scripts/fetch_sheet.py`: standalone utility to download any spreadsheet tab by field slug via the Sheets API.
- `GOOGLE_APP_SCRIPT.md`: Apps Script setup for the human review workflow.
- `NEXT_STEPS.md`: roadmap for using the suggestion history to analyze and improve agent performance.

## Requirements

- macOS
- `openclaw` in `PATH`
- `ollama` installed and running
- a local Ollama model available; validated default: `ollama/qwen3:8b`
- a repo-local virtual environment at `.venv`
- Python dependencies installed from `requirements.txt`
- a Google service-account JSON key with read/write access to the spreadsheet, placed at `~/.config/wheretopublish/google_service_account.json` or pointed to by `GOOGLE_SERVICE_ACCOUNT_KEY`
- external data files present in `WhereToPublish.github.io/data_extraction/` — run `bash scripts/download_extraction.sh` from inside that repo to populate them

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
4. restarts the gateway and redirects `openclaw logs --follow --json` to a timestamped JSONL file
5. loads remote deduplication keys from `AI_suggestions` and `AI_suggestions_processed`
6. runs `agent/scripts/run_enrichment.py` — journals are sorted by priority and it stops at `--max-suggestions` (default 15) valid suggestions

Live monitoring is via terminal output and the runner log at `agent/output/logs/run-*.runner.log`.

On a full run, gap analysis downloads all 10 tabs (≈2400 journals) and typically identifies ~1300 journals with gaps across all field types. The enrichment pipeline processes them in priority order (high → medium → low) and stops at `--max-suggestions`.

## Upload Suggestions to Google Sheet

After a run, push `AI_suggestions.csv` to the spreadsheet for review:

```bash
.venv/bin/python agent/scripts/upload_suggestions.py
```

This replaces the content of the `AI_suggestions` tab with the current suggestions. Each row has a **Status** column (pending / approve / reject) set to `pending` by default. Team members set the status for each row, then run **WhereToPublish → Apply Reviewed Suggestions** from the Apps Script menu.

Options:

```bash
# Custom input file
.venv/bin/python agent/scripts/upload_suggestions.py --input /path/to/AI_suggestions.csv

# Custom credentials path
.venv/bin/python agent/scripts/upload_suggestions.py --credentials /path/to/key.json
```

## Download a Sheet Tab

`fetch_sheet.py` downloads any tab from the spreadsheet as a CSV:

```bash
# Download Genetics & Genomics
.venv/bin/python agent/scripts/fetch_sheet.py --field genetics_genomics --output /tmp/out.csv

# Available fields: generalist, anatomy_physiology, cancer, development,
#   ecology_evolution, genetics_genomics, immunology,
#   molecular_cellular_biology, neurosciences, plants
```

## Run Examples

```bash
# Reuse the current gap report and process one named journal
./run_agent.sh --skip-gap-analysis --journal "Human Genomics"

# Run the full pipeline refresh across all tabs, then process one journal
./run_agent.sh --journal "Plant Genetic Resources"

# Stop after writing 3 valid suggestions
./run_agent.sh --max-suggestions 3

# Override the model for one run
JOURNALMIND_MODEL=ollama/qwen2.5:14b ./run_agent.sh --max-suggestions 1
```

All extra arguments are forwarded to `agent/scripts/run_enrichment.py`.

## Validation Rules

Only requested fields are eligible for persistence. The validator rejects rows that are structurally valid JSON but still unsupported, including:

- publisher-as-institution guesses
- institution types without a valid institution value
- `Alternative journal name` suggestions that are not true `alt_name` rows
- `Alternative journal name` values that normalize to the original journal name
- `APC Euros = 0` rows unless the reasoning explicitly states there is no APC
- `e-ISSN`, `p-ISSN`, and `ISSN-L` values that are not formatted as `XXXX-XXXX`

If no suggestion survives validation, the journal is written as `unresolved`.

## Outputs

Default result files:

- `agent/output/AI_suggestions.csv`
- `agent/output/gap_report.json`
- `agent/output/state/run_state.json`

Run-level logs:

- `agent/output/logs/run-*.openclaw.jsonl` — raw archived OpenClaw JSON log stream
- `agent/output/logs/run-*.runner.log` — clean orchestrator output

Per-journal artifacts:

- `agent/output/logs/enrichment-*.prompt.txt`
- `agent/output/logs/enrichment-*.stdout.json`
- `agent/output/logs/enrichment-*.stderr.log`

Checkpoint files:

- `agent/output/state/checkpoint_suggestions_*.csv`

Known runtime limitations:

- the current OpenClaw environment may not have a working `web_search` backend, so the prompt provides direct official-site, DOAJ, and Scimago lookup URLs and falls back to `unresolved` when those sources are blocked or insufficient
- `--skip-download` skips all 10 tab downloads; use it when the `data_extracted/` CSVs are already current

## Stop / Reset

```bash
openclaw gateway stop
openclaw config unset agents.defaults.workspace
```

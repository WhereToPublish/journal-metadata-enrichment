# DONE.md — Current Project State

This file describes the current implementation state only. It is intended as a fast orientation document for LLMs working in this repository.

## Runtime Path

The current end-to-end flow is:

```text
run_agent.sh
  -> agent/scripts/gap_analysis.py (unless skipped)
    -> Google Sheets API (sheets_client.py) — downloads Genetics & Genomics tab
    -> external data sources (OpenAPC, DOAJ, Scimago, PCI, Dataverse)
    -> WhereToPublish pipeline (update_extracted.py → data_process.py)
  -> agent/scripts/run_enrichment.py
    -> agent/scripts/openclaw_runtime.py
    -> one OpenClaw session per journal
    -> agent/scripts/suggestions_io.py
  -> agent/output/*

agent/scripts/upload_suggestions.py  (run manually after enrichment)
  -> Google Sheets API (sheets_client.py) — writes AI_suggestions tab
```

Key runtime properties:

- fully automated startup from the terminal
- terminal and log files are the authoritative live monitor
- one OpenClaw session per journal to bound context
- OpenClaw does the journal-level tool use and browsing
- the agent works only on journals already present in the selected backlog
- Python owns CSV, state, checkpoint, and log persistence
- unresolved is the normal fallback when evidence is weak or blocked
- Google Sheets API (service-account auth) is used for all spreadsheet I/O: downloading tabs and uploading suggestions

## Root Files

- `README.md`: human-facing overview of the current runtime
- `DONE.md`: this file
- `NEXT_STEPS.md`: future work only
- `run_agent.sh`: launcher used for real runs
- `requirements.txt`: Python dependencies for `.venv`
- `.gitignore`: ignores generated output under `agent/output/`

## Launcher

`run_agent.sh` is the entry point and currently does all of the following:

- changes into the repo root before launching anything
- uses `.venv/bin/python` by default, with `JOURNALMIND_PYTHON` as the override
- checks that `openclaw` and `ollama` exist and that Ollama is responding
- checks that the selected Ollama model exists locally
- checks that `.venv/bin/python` can import `polars`
- sets `agents.defaults.workspace` to `agent/workspace`
- sets `agents.defaults.model.primary` to the selected model
- restarts or starts the OpenClaw gateway
- starts `openclaw logs --follow --json` and tees it into live terminal output plus log files
- launches `agent/scripts/run_enrichment.py`
- reports the effective output, state, and log paths at the end of the run
- cleans up the background OpenClaw log tail on exit

Current model behavior:

- default model: `ollama/qwen3:8b`
- override mechanism: `JOURNALMIND_MODEL=ollama/<tag>`
- default runner arguments: `--priorities high,medium --max-suggestions 50`
- extra launcher arguments are forwarded directly to `run_enrichment.py`

## Python Modules
### agent/scripts/sheets_client.py

Shared Google Sheets API module.

- defines `SPREADSHEET_ID` and `DEFAULT_CREDENTIALS_PATH` (resolved from `GOOGLE_SERVICE_ACCOUNT_KEY` env var or `~/.config/wheretopublish/google_service_account.json`)
- defines `SHEET_TAB_NAMES` mapping field slugs to actual Google Sheets tab names
- provides `get_sheets_service(credentials_path, readonly)` — returns an authenticated Sheets API v4 service
- provides `download_tab_as_csv(service, tab_name, dest_path)` — downloads a tab and writes it as CSV
- provides `get_or_create_tab`, `clear_tab`, and `write_rows` helpers used by the upload script

### agent/scripts/fetch_sheet.py

Standalone utility for downloading any spreadsheet tab via the Sheets API.

- downloads any tab by `--field` slug (e.g. `genetics_genomics`)
- uses `sheets_client.get_sheets_service()` and `sheets_client.download_tab_as_csv()`
- accepts optional `--output` and `--credentials` arguments
- replaces the former public export URL approach (no GIDs, no unauthenticated requests)

### agent/scripts/upload_suggestions.py

Upload script for staging suggestions in the Google Sheet.

- reads `AI_Suggestions.csv` (default: `agent/output/AI_Suggestions.csv`)
- creates or reuses the `AI_suggestions` tab in the spreadsheet
- clears existing content and writes a fresh header + data rows
- prepends an `Approve?` checkbox column (initially `FALSE`) for human review
- accepts optional `--input` and `--credentials` arguments



Shared constants and schemas.

- defines output, log, and state paths
- defines CSV headers and allowed values
- defines `DEFAULT_MODEL` and `DEFAULT_PRIORITIES`
- provides `slugify()` for session ids

### agent/scripts/gap_analysis.py

Pipeline refresh and gap discovery.

- downloads the Genetics & Genomics sheet via the Google Sheets API (using `sheets_client`)
- downloads external-source inputs (OpenAPC, DOAJ, Scimago, PCI, Dataverse)
- runs the WTP pipeline unless skipped
- writes `agent/output/gap_report.json`
- records `wtp_dir` as `WhereToPublish.github.io` when run from the repo root
- accepts optional `--credentials` argument forwarded to the Sheets API call

### agent/scripts/run_enrichment.py

Current orchestrator.

- can run gap analysis unless `--skip-gap-analysis` is passed
- loads the gap report and selects journals by priority
- supports `--journal` for exact single-journal targeting
- supports `--journal-limit`, `--max-suggestions`, `--output`, `--state`, and `--log-dir`
- gives each journal a unique OpenClaw session id
- passes known metadata, requested gaps, and direct lookup URLs to the model
- tells the model to use tools, return JSON only, and stay within the provided existing-journal backlog
- appends accepted rows and updates run state after each journal
- writes checkpoint CSVs every 10 processed journals

### agent/scripts/openclaw_runtime.py

Headless OpenClaw wrapper.

- runs `openclaw agent --session-id ... --message ... --thinking off --json`
- writes prompt, stdout JSON, and stderr logs per journal session
- extracts the model JSON object from the returned payload text
- retries once with a narrower repair prompt when the model returns non-JSON output

### agent/scripts/suggestions_io.py

Persistence and sanitization layer.

- initializes and normalizes `AI_Suggestions.csv`
- deduplicates on `(journal, field, suggested_value)`
- accepts only requested fields and supported schema values
- rejects publisher-as-institution guesses
- rejects institution types without a valid institution value
- rejects `Scimago Journal Title` rows unless they are true `alt_name` suggestions
- rejects `Scimago Journal Title` values that normalize to the original journal name
- rejects `APC Euros = 0` unless the reasoning explicitly states there is no APC
- writes `run_state.json` and checkpoint CSVs
- converts `status=ok` with zero valid rows into `unresolved`

### agent/scripts/fetch_sheet.py

Standalone utility for downloading any spreadsheet tab via the Sheets API. Uses `sheets_client` for authentication; accepts `--field`, `--output`, and `--credentials`. Not used in the main launcher path.

## Removed Python Surface

`agent/scripts/evidence_lookup.py` is no longer part of the project. Journal-level evidence gathering is owned by OpenClaw, not a Python-side evidence builder.

## Agent Workspace

`agent/workspace/` is the OpenClaw workspace loaded at runtime.

Current emphasis:

- one journal per session
- use tools for research
- start from caller-provided lookup URLs before relying on search
- return one JSON object when the caller asks for JSON only
- do not propose adding new journals
- do not write files in the normal automation path
- prefer unresolved over guesses when sources are blocked or ambiguous

Relevant files:

- `agent/workspace/AGENTS.md`
- `agent/workspace/TOOLS.md`
- `agent/workspace/BOOT.md`
- `agent/workspace/BOOTSTRAP.md`
- `agent/workspace/skills/journal-enrichment/SKILL.md`

## Persistence Contract

The effective contract between Python and the model is:

- one JSON object per journal
- only requested gap fields are eligible
- the model owns research and browsing, not file mutation
- Python decides what gets persisted
- if no row survives validation, the journal is treated as unresolved
- accepted suggestions are staged in `agent/output/AI_Suggestions.csv` and pushed to the `AI_suggestions` spreadsheet tab via `upload_suggestions.py`
- the spreadsheet's data tabs are only modified by a human using the Apps Script review workflow

## Outputs

Default output files:

- `agent/output/AI_Suggestions.csv`
- `agent/output/gap_report.json`
- `agent/output/state/run_state.json`

Checkpoint files:

- `agent/output/state/checkpoint_suggestions_*.csv`

Run-level logs:

- `agent/output/logs/run-*.console.log`
- `agent/output/logs/run-*.openclaw.jsonl`
- `agent/output/logs/run-*.runner.log`

Per-journal artifacts:

- `agent/output/logs/enrichment-*.prompt.txt`
- `agent/output/logs/enrichment-*.stdout.json`
- `agent/output/logs/enrichment-*.stderr.log`

## Current Validated State

The implementation has been validated with real launcher runs.

Validated behavior:

- the full launcher refreshes the gap report and downloads the Genetics & Genomics sheet via the Sheets API
- `fetch_sheet.py` downloads any tab by field slug using the Sheets API
- `upload_suggestions.py` clears and rewrites the `AI_suggestions` spreadsheet tab with current suggestions and an `Approve?` checkbox column
- blocked-source cases such as `Human Genomics` stay unresolved with no persisted rows
- supported cases can persist a narrow subset of requested fields, for example `Business model = Hybrid` for `Plant Genetic Resources`
- when the model returns structurally valid but unsupported guesses, the validator drops them instead of writing them

Known runtime constraint:

- the current OpenClaw environment may not have a working `web_search` backend, so prompts provide direct official-site, DOAJ, and Scimago lookup URLs and unresolved remains the correct fallback when those sources are insufficient

## Boundaries

- `WhereToPublish.github.io/` is treated as read-only input during enrichment work
- there is no direct Google Sheets write path in the current runtime
- human review remains required for all persisted suggestions
- blocked publisher pages and missing source evidence are normal unresolved cases, not separate failure modes

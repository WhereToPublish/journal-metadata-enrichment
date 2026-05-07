# DONE.md — Current Project State

This file describes the current implementation state only. It is intended as a fast orientation document for LLMs working in this repository.

## Runtime Path

The current end-to-end flow is:

```text
run_agent.sh
  -> agent/scripts/gap_analysis.py (unless skipped)
    -> verifies external data files exist in WhereToPublish.github.io/data_extraction/
    -> Google Sheets API (sheets_client.py) — downloads all 10 sheet tabs
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
- `GOOGLE_APP_SCRIPT.md`: future work only
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
- starts `openclaw logs --follow --json` and redirects its raw JSONL output directly to `agent/output/logs/run-<RUN_ID>.openclaw.jsonl` (no filtering)
- launches `agent/scripts/run_enrichment.py` forwarding all extra arguments
- reports the effective output, state, and log paths at the end of the run
- cleans up the background OpenClaw log tail on exit

Current model behavior:

- default model: `ollama/qwen3:8b`
- override mechanism: `JOURNALMIND_MODEL=ollama/<tag>`
- all journals across all 10 tabs are processed, sorted by priority (high → medium → low); stops when `--max-suggestions` valid suggestions are written (default: 15)
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

### agent/scripts/enrichment_common.py

Shared constants and schemas.

- defines output, log, and state paths
- defines CSV headers and allowed values
- defines `DEFAULT_MODEL` and `DEFAULT_PRIORITIES`
- provides `slugify()` for session ids

### agent/scripts/gap_analysis.py

Pipeline refresh and gap discovery across all Google Sheets tabs.

- verifies that all 5 required external data files are present in `WhereToPublish.github.io/data_extraction/` (openapc.csv.gz, DOAJ.csv.gz, scimagojr.csv.gz, PCI_friendly.csv.gz, APC_dataverse.txt.gz); exits immediately with a clear, actionable error listing missing files if any are absent
- does not download external data; those files are owned by the WhereToPublish project and populated via `bash scripts/download_extraction.sh` from inside that repo
- downloads **all 10 Google Sheets tabs** (Generalists, Anatomy & Physiology, Cancer, Development, Ecology & Evolution, Genetics & Genomics, Immunology, Molecular & Cellular Biology, Neurosciences, Plants) via the Sheets API (using `sheets_client`) and writes each to `data_extracted/<slug>.csv`
- runs the WTP pipeline (update_extracted.py → data_process.py) unless skipped
- builds a unified gap report covering all tabs: each journal entry includes a `tab` field indicating its source; journals appearing in multiple tabs are deduplicated by name (first-seen tab wins)
- writes `agent/output/gap_report.json`
- records `wtp_dir` as `WhereToPublish.github.io` when run from the repo root
- `--skip-download` skips all sheet tab downloads (external data verification always runs)
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
- rejects `APC Euros = 0` for journals whose known business model is `Subscription` (a Subscription journal never has a zero APC in this schema; that value implies OA diamond)
- rejects `APC Euros = 0` for any other journal unless the reasoning explicitly states there is no APC (e.g. "no APC", "free to publish", "does not charge")
- writes `run_state.json` and checkpoint CSVs
- converts `status=ok` with zero valid rows into `unresolved`

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

- `agent/output/logs/run-*.openclaw.jsonl` — raw archived OpenClaw JSON log stream
- `agent/output/logs/run-*.runner.log` — clean orchestrator output

Per-journal artifacts:

- `agent/output/logs/enrichment-*.prompt.txt`
- `agent/output/logs/enrichment-*.stdout.json`
- `agent/output/logs/enrichment-*.stderr.log`

## Current Validated State

The implementation has been validated with real launcher runs.

Validated behavior:

- the full launcher verifies external data files then downloads all 10 Google Sheets tabs and refreshes the gap report across all tabs
- if any required external data file is missing, gap analysis exits immediately listing missing files and the command to fix it
- `fetch_sheet.py` downloads any single tab by field slug using the Sheets API
- `upload_suggestions.py` clears and rewrites the `AI_suggestions` spreadsheet tab with current suggestions and an `Approve?` checkbox column
- the gap report covers all 10 biology field tabs (~2400 journals); each journal entry includes a `tab` field; journals in multiple tabs are deduplicated by name
- blocked-source cases stay unresolved with no persisted rows
- when the model returns structurally valid but unsupported guesses, the validator drops them instead of writing them
- `Scimago Journal Title` suggestions where the suggested value normalizes to the same name as the journal are correctly dropped
- `APC Euros = 0` is accepted only when the reasoning contains an explicit no-APC marker; it is always rejected for Subscription journals regardless of reasoning
- the model correctly returns `suggestion_type: alt_name` for `Scimago Journal Title` fields
- when the model returns prose instead of JSON, the retry prompt includes a concrete unresolved JSON example and the model recovers
- suggestions with empty `suggested_value` are dropped before persistence
- the OpenClaw live log filter eliminates WebSocket heartbeat floods; typical run produces a `run-*.openclaw.jsonl` of ~600KB instead of 3–4MB

Known runtime constraint:

- the current OpenClaw environment may not have a working `web_search` backend, so prompts provide direct official-site, DOAJ, and Scimago lookup URLs and unresolved remains the correct fallback when those sources are insufficient

## Boundaries

- `WhereToPublish.github.io/` is treated as read-only input during enrichment work
- there is no direct Google Sheets write path in the current runtime
- human review remains required for all persisted suggestions
- blocked publisher pages and missing source evidence are normal unresolved cases, not separate failure modes

# DONE.md — Current Project State

This file describes the current implementation state only. It is intended as a fast orientation document for LLMs working in this repository.

## Runtime Path

The current end-to-end flow is:

```text
run_agent.sh
  -> agent/scripts/gap_analysis.py (unless skipped)
    -> verifies external data files exist in WhereToPublish.github.io/data_extraction/
    -> WhereToPublish.github.io/scripts/sheets_client.py — downloads all 10 sheet tabs
    -> WhereToPublish pipeline (update_extracted.py → data_process.py)
  -> agent/scripts/run_enrichment.py
    -> load_suggestion_keys_from_tabs() — loads remote deduplication keys
    -> agent/scripts/openclaw_runtime.py
    -> one OpenClaw session per journal
    -> agent/scripts/suggestions_io.py
  -> agent/output/*

agent/scripts/upload_suggestions.py  (run manually after enrichment)
  -> WhereToPublish.github.io/scripts/sheets_client.py — writes AI_suggestions tab with Status dropdown
```

Key runtime properties:

- fully automated startup from the terminal
- terminal and log files are the authoritative live monitor
- one OpenClaw session per journal to bound context
- OpenClaw does the journal-level tool use and browsing
- the agent works only on journals already present in the selected backlog
- Python owns CSV, state, checkpoint, and log persistence
- unresolved is the normal fallback when evidence is weak or blocked
- before each enrichment run, remote suggestion keys are loaded from `AI_suggestions` and `AI_suggestions_processed`; if loading fails the run degrades gracefully to local-CSV deduplication only
- Google Sheets API (service-account auth) is used for all spreadsheet I/O: downloading tabs, uploading suggestions, and loading remote deduplication keys

## Root Files

- `README.md`: human-facing overview of the current runtime
- `DONE.md`: this file
- `GOOGLE_APP_SCRIPT.md`: Apps Script setup for the human review workflow
- `NEXT_STEPS.md`: roadmap for using suggestion history to improve the agent
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
### WhereToPublish.github.io/scripts/sheets_client.py

Shared Google Sheets API module — canonical implementation used by both the WTP pipeline scripts and all agent scripts.

- defines `SPREADSHEET_ID`, `VARIABLES_TAB_NAME`, and `DEFAULT_CREDENTIALS_PATH` (resolved from `GOOGLE_SERVICE_ACCOUNT_KEY` env var or `~/.config/wheretopublish/google_service_account.json`)
- defines `SHEET_TAB_NAMES` mapping field slugs to actual Google Sheets tab names
- provides `get_sheets_service(credentials_path, readonly)` — returns an authenticated Sheets API v4 service
- provides `download_tab_as_csv(service, tab_name, dest_path)` — downloads a tab and writes it as CSV
- provides `read_csv_as_rows(csv_path)` and `upload_tab_from_csv(service, csv_path, tab_name)` — round-trip CSV helpers
- provides `write_rows(service, spreadsheet_id, tab_name, rows)` — writes a list of rows to a tab

Agent scripts (`gap_analysis.py`, `run_enrichment.py`, `upload_suggestions.py`) prepend `WhereToPublish.github.io/scripts/` to `sys.path` so they all import this single copy rather than maintaining a duplicate.

### agent/scripts/upload_suggestions.py

Upload script for staging suggestions in the Google Sheet.

- reads `AI_suggestions.csv` (default: `agent/output/AI_suggestions.csv`)
- creates or reuses the `AI_suggestions` tab in the spreadsheet
- clears existing content and writes a fresh header + data rows
- prepends a `Status` column (pending / approve / reject); new rows are always uploaded as `pending`
- accepts optional `--input` and `--credentials` arguments

### agent/scripts/enrichment_common.py

Shared constants and schemas.

- defines output, log, and state paths
- defines `WTP_SCRIPTS_DIR` pointing to `WhereToPublish.github.io/scripts/` (used by agent scripts to import the canonical `sheets_client`)
- defines CSV headers and allowed values
- defines `ALLOWED_FIELDS` including `"Alternative journal name"`, `"e-ISSN"`, `"p-ISSN"`, and `"ISSN-L"` alongside the standard metadata fields
- defines `DEFAULT_MODEL` and `DEFAULT_PRIORITIES`
- provides `slugify()` for session ids

### agent/scripts/gap_analysis.py

Pipeline refresh and gap discovery across all Google Sheets tabs.

- verifies that all 5 required external data files are present in `WhereToPublish.github.io/data_extraction/` (openapc.csv.gz, DOAJ.csv.gz, scimagojr.csv.gz, PCI_friendly.csv.gz, APC_dataverse.txt.gz); exits immediately with a clear, actionable error listing missing files if any are absent
- does not download external data; those files are owned by the WhereToPublish project and populated via `bash scripts/download_extraction.sh` from inside that repo
- downloads **all 10 Google Sheets tabs** (Generalists, Anatomy & Physiology, Cancer, Development, Ecology & Evolution, Genetics & Genomics, Immunology, Molecular & Cellular Biology, Neurosciences, Plants) via the Sheets API (using `WhereToPublish.github.io/scripts/sheets_client`) and writes each to `data_extracted/<slug>.csv`
- runs the WTP pipeline (update_extracted.py → data_process.py) unless skipped
- builds a unified gap report covering all tabs: each journal entry includes a `tab` field indicating its source, as well as `e_issn`, `p_issn`, and `issn_l` fields populated from the enriched pipeline output; journals appearing in multiple tabs are deduplicated by name (first-seen tab wins)
- generates gaps for `Alternative journal name` (highest priority weight = 12, `alt_name` type) when a journal has no Scimago data
- generates low-priority `fill` gaps for `e-ISSN`, `p-ISSN`, and `ISSN-L` **only when the journal has none of the three ISSN fields** — if any ISSN is already present (`e-ISSN`, `p-ISSN`, or `ISSN-L`), all three ISSN gap entries are suppressed
- writes `agent/output/gap_report.json`
- records `wtp_dir` as `WhereToPublish.github.io` when run from the repo root
- `--skip-download` skips all sheet tab downloads (external data verification always runs)
- accepts optional `--credentials` argument forwarded to the Sheets API call

### agent/scripts/run_enrichment.py

Current orchestrator.

- can run gap analysis unless `--skip-gap-analysis` is passed
- loads remote deduplication keys from `AI_suggestions` and `AI_suggestions_processed` Google Sheets tabs before processing any journal; if remote loading fails, logs a warning and falls back to local-CSV deduplication only
- merges remote keys with local `existing_keys` so any suggestion already present in either sheet is silently dropped before persistence
- accepts optional `--credentials` argument forwarded to the remote key loader
- loads the gap report and selects journals by priority
- supports `--journal` for exact single-journal targeting
- supports `--journal-limit`, `--max-suggestions`, `--output`, `--state`, and `--log-dir`
- gives each journal a unique OpenClaw session id
- passes known metadata (including `e_issn`, `p_issn`, `issn_l`), requested gaps, and direct lookup URLs to the model; when any ISSN is known the prompt includes `scimago_by_issn` (Scimago search by ISSN) and `doaj_by_issn` as the preferred sources for resolving `Alternative journal name`
- tells the model to use tools, return JSON only, and stay within the provided existing-journal backlog
- instructs the model that **Business model requires hard evidence** (explicit text on the journal page or DOAJ) and that inference from publisher reputation is not acceptable
- instructs the model that **absence of APC mention is not evidence** that APC = 0; only suggest APC Euros = 0 when the source explicitly states no charge
- instructs the model to convert non-Euro APC values using approximate current exchange rates
- appends accepted rows and updates run state after each journal
- writes checkpoint CSVs every 10 processed journals
- provides `load_suggestion_keys_from_tabs(service, tab_names, spreadsheet_id)` — returns a set of `(journal, field, suggested_value)` triples read from the given sheet tabs; missing or unrecognised tabs are silently skipped; 

### agent/scripts/openclaw_runtime.py

Headless OpenClaw wrapper.

- runs `openclaw agent --session-id ... --message ... --thinking off --json`
- writes prompt, stdout JSON, and stderr logs per journal session
- extracts the model JSON object from the returned payload text
- retries once when the model returns non-JSON output; the retry uses a **compact prompt** (full instructions stripped, only a short JSON-only directive + the Evidence JSON block retained) to prevent context overflow from compounding across attempts

### agent/scripts/suggestions_io.py

Persistence and sanitization layer.

- initializes and normalizes `AI_suggestions.csv`
- deduplicates on `(journal, field, suggested_value)`
- accepts only requested fields and supported schema values
- rejects publisher-as-institution guesses
- rejects institution types without a valid institution value
- rejects `Alternative journal name` rows unless they are true `alt_name` suggestions
- rejects `Alternative journal name` values that normalize to the original journal name
- rejects `Alternative journal name` suggestions with confidence < 0.70 (a wrong alt_name would silently break the Scimago join)
- rejects `APC Euros = 0` for journals whose known business model is `Subscription` (a Subscription journal never has a zero APC in this schema; that value implies OA diamond)
- rejects `APC Euros = 0` for any other journal unless the reasoning explicitly states there is no APC (e.g. "no APC", "free to publish", "does not charge")
- rejects `e-ISSN`, `p-ISSN`, and `ISSN-L` values that do not match the `XXXX-XXXX` format (last character may be `X` as a check digit)
- writes `run_state.json` and checkpoint CSVs
- converts `status=ok` with zero valid rows into `unresolved`

## Agent Workspace

`agent/workspace/` is the OpenClaw workspace loaded at runtime.

Current emphasis:

- one journal per session
- use tools for research
- start from caller-provided lookup URLs (including `doaj_by_issn` when an ISSN is known) before relying on search
- return one JSON object when the caller asks for JSON only
- do not propose adding new journals
- do not write files in the normal automation path
- prefer unresolved over guesses when sources are blocked or ambiguous
- `Alternative journal name` suggestions must use `suggestion_type: "alt_name"`; ISSN suggestions must be in `XXXX-XXXX` format

Relevant files:

- `agent/workspace/AGENTS.md`
- `agent/workspace/TOOLS.md`
- `agent/workspace/BOOT.md`
- `agent/workspace/BOOTSTRAP.md`
- `agent/workspace/skills/journal-enrichment/SKILL.md`

## Review Workflow

The `AI_suggestions` Google Sheets tab is the staging area for human review.

- each row has a `Status` column (pending / approve / reject)
- new rows are always uploaded as `pending`
- team members set the status to `approve` or `reject` after reviewing evidence
- running **WhereToPublish → Apply Reviewed Suggestions** (Apps Script):
  - `approve` rows: the suggestion is written to the appropriate data tab, then the row is archived in `AI_suggestions_processed`
  - `reject` rows: the row is archived in `AI_suggestions_processed` without any data change
  - `pending` rows are left untouched
- the `AI_suggestions_processed` tab accumulates all reviewed suggestions for performance analysis (see `NEXT_STEPS.md`)

## Persistence Contract

The effective contract between Python and the model is:

- one JSON object per journal
- only requested gap fields are eligible
- the model owns research and browsing, not file mutation
- Python decides what gets persisted
- if no row survives validation, the journal is treated as unresolved

## Boundaries

- `WhereToPublish.github.io/` is treated as read-only input during enrichment work
- the spreadsheet's data tabs are written only by the Apps Script review workflow, never by Python directly
- blocked publisher pages and missing source evidence are normal unresolved cases, not separate failure modes

## Outputs

Default output files:

- `agent/output/AI_suggestions.csv`
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

## Known Runtime Constraint

- the current OpenClaw environment may not have a working `web_search` backend; prompts provide direct official-site, DOAJ, and Scimago lookup URLs and `unresolved` is the correct fallback when those sources are insufficient
- `--skip-download` skips all 10 tab downloads; use it when the `data_extracted/` CSVs are already current


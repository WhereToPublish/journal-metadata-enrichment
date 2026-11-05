# DONE.md — Current Project State

This file describes the current implementation state only. It is intended as a fast orientation document for LLMs working in this repository.

The project lives inside the WhereToPublish repository: `WhereToPublish/journal-metadata-enrichment/`.
The parent directory (`..`, i.e. the WhereToPublish repo root) is the data source and is treated as
read-only input. The AI agent is **OMP (oh-my-pi)**, installed on the host and plugged to an LLM
(`~/.omp/agent/config.yml`). Docker and Ollama are no longer used.

## Runtime Path

The current end-to-end flow is:

```text
run_agent.sh
  -> verifies omp is in PATH and .venv/bin/python can import polars
  -> passes the per-journal timeout into run_enrichment.py as --agent-timeout-seconds
  -> agent/scripts/gap_analysis.py (unless skipped)
    -> verifies external data files exist in ../data_extraction/
    -> ../scripts/sheets_client.py — downloads all 10 sheet tabs to ../data_extracted/
    -> WhereToPublish pipeline run with cwd=.. (scripts/update_extracted.py → scripts/data_process.py)
  -> agent/scripts/run_enrichment.py
    -> load_suggestion_keys_from_tabs() — loads remote deduplication keys
    -> agent/scripts/omp_runtime.py
       -> omp -p <prompt> --mode json --no-session --auto-approve --thinking off \
          --append-system-prompt @agent/prompt/system.md --max-time <seconds> [--model <model>]
          (one OMP run per journal, on the host)
    -> agent/scripts/suggestions_io.py
  -> agent/scripts/issn_alt_name_suggestions.py
    -> agent/scripts/issn_lookups.py — builds ISSN -> title lookups for Scimago, DOAJ, OpenAPC
       from ../data_extraction/*.csv.gz
    -> scans ../data_extracted/*.csv for empty Alternative journal name cells with ISSN coverage and any missing-source presence flag
    -> agent/scripts/suggestions_io.py
  -> agent/output/*

agent/scripts/upload_suggestions.py  (run manually after enrichment)
  -> agent/scripts/suggestions_io.py — collapses duplicate (journal, field) rows and keeps highest confidence
  -> ../scripts/sheets_client.py — writes Agent_suggestions tab with Status dropdown
```

Key runtime properties:

- fully automated startup from the terminal
- terminal and log files are the authoritative live monitor
- OMP runs directly on the host as a non-interactive CLI process — there is no container and no host/mount boundary
- one OMP run per journal to bound context
- OMP's per-journal instructions are appended to its system prompt from `agent/prompt/system.md` on every run
- the per-journal OMP timeout defaults to 900 seconds and can be overridden with `JOURNALMIND_AGENT_TIMEOUT_SECONDS` (launcher) or `--agent-timeout-seconds`
- the OMP model defaults to the one configured in `~/.omp/agent/config.yml`; override per run with `JOURNALMIND_MODEL=<model>` (passed as `omp --model`)
- OMP does the journal-level tool use and web browsing
- deterministic ISSN-based `Alternative journal name` suggestions are generated locally from Scimago, DOAJ, and OpenAPC without calling the LLM
- the agent works only on journals already present in the selected backlog
- Python owns CSV, state, checkpoint, and log persistence
- `Agent_suggestions.csv` is canonicalized on `(journal, field)` so only the highest-confidence row for each suggested column is kept
- unresolved is the normal fallback when evidence is weak or blocked
- before each enrichment run, remote suggestion keys are loaded from `Agent_suggestions` and `Agent_suggestions_processed`; if loading fails the run degrades gracefully to local-CSV deduplication only
- Google Sheets API (service-account auth) is used for all spreadsheet I/O: downloading tabs, uploading suggestions, and loading remote deduplication keys

## Root Files

- `README.md`: human-facing overview of the current runtime
- `DONE.md`: this file
- `GOOGLE_APP_SCRIPT.md`: Apps Script setup for the human review workflow
- `NEXT_STEPS.md`: roadmap for using suggestion history to improve the agent
- `run_agent.sh`: launcher used for real runs
- `requirements.txt`: Python dependencies for `.venv`
- `clean.sh`: removes `agent/output/` and hard-resets the parent WhereToPublish repo (main, then data branch)
- `.gitignore`: ignores credentials and generated output under `agent/output/`

## Launcher

`run_agent.sh` is the entry point and currently does all of the following:

- changes into the project root (the `journal-metadata-enrichment/` folder) before launching anything
- uses `.venv/bin/python` by default, with `JOURNALMIND_PYTHON` as the override
- checks that `omp` is available in PATH
- checks that `.venv/bin/python` can import `polars`
- validates `JOURNALMIND_AGENT_TIMEOUT_SECONDS` as a positive integer (default: 900)
- launches `agent/scripts/run_enrichment.py` forwarding all extra arguments, while always appending `--agent-timeout-seconds <value>`
- appends `--max-suggestions <n>` when `JOURNALMIND_MAX_SUGGESTIONS` is set
- appends `agent/scripts/issn_alt_name_suggestions.py` output to the same runner log instead of overwriting the enrichment log
- reports the effective output, state, and log paths at the end of the run

Current agent behavior:

- default per-journal timeout: `JOURNALMIND_AGENT_TIMEOUT_SECONDS=900`
- default suggestion cap: 15 (`--max-suggestions` on `run_enrichment.py`), overridable per run
- all journals across all 10 tabs are processed, sorted by priority (high → medium → low); stops when `--max-suggestions` valid suggestions are written
- extra launcher arguments are forwarded directly to `run_enrichment.py`

## Python Modules

### agent/scripts/enrichment_common.py

Shared paths and schemas.

- defines `PROJECT_ROOT` (this project folder), `AGENT_DIR`, and output/log/state paths
- defines `DEFAULT_WTP_DIR` as the **parent directory** (the WhereToPublish repo root) and `WTP_SCRIPTS_DIR = ../scripts`
- defines `SYSTEM_PROMPT_PATH = agent/prompt/system.md` (appended to the OMP system prompt on every run)
- defines the suggestions CSV headers and allowed values (`ALLOWED_FIELDS` including `"Alternative journal name"`, `"e-ISSN"`, `"p-ISSN"`, `"ISSN-L"`, plus priorities, suggestion types, business models)
- provides `slugify()` for session ids and `is_empty()`

All agent scripts import the canonical `sheets_client` / `libraries` from `../scripts` via `WTP_SCRIPTS_DIR` on `sys.path` — there is no duplicate copy inside this project.

### ../scripts/sheets_client.py (WhereToPublish repo)

Shared Google Sheets API module — canonical implementation used by both the WTP pipeline scripts and all agent scripts.

- defines `SPREADSHEET_ID`, `VARIABLES_TAB_NAME`, and `DEFAULT_CREDENTIALS_PATH` (resolved from `GOOGLE_SERVICE_ACCOUNT_KEY` env var or `~/.config/wheretopublish/google_service_account.json`)
- defines `SHEET_TAB_NAMES` mapping field slugs to actual Google Sheets tab names
- provides `get_sheets_service(credentials_path, readonly)` — returns an authenticated Sheets API v4 service
- provides `download_tab_as_csv(service, tab_name, dest_path)` — downloads a tab and writes it as CSV
- provides `read_csv_as_rows(csv_path)` and `upload_tab_from_csv(service, csv_path, tab_name)` — round-trip CSV helpers
- provides `write_rows(service, spreadsheet_id, tab_name, rows)` — writes a list of rows to a tab

### agent/scripts/gap_analysis.py

Pipeline refresh and gap discovery across all Google Sheets tabs.

- verifies that all 5 required external data files are present in `../data_extraction/` (openapc.csv.gz, DOAJ.csv.gz, scimagojr.csv.gz, PCI_friendly.csv.gz, scholcommlab_apc_dataset_2019_2025-2.csv.gz); exits immediately with a clear, actionable error listing missing files if any are absent
- does not download external data; those files are owned by the WhereToPublish project and populated via `bash scripts/download_extraction.sh` from the WhereToPublish repo root
- downloads **all 10 Google Sheets tabs** (Generalist, Anatomy & Physiology, Cancer, Development, Ecology & Evolution, Genetics & Genomics, Immunology, Molecular & Cellular Biology, Neurosciences, Plants) via the Sheets API (using `../scripts/sheets_client`) and writes each to `../data_extracted/<slug>.csv`
- runs the WTP pipeline with the WhereToPublish repo root as working directory (`scripts/update_extracted.py` → `scripts/data_process.py`) unless skipped
- builds a unified gap report covering all tabs: each journal entry includes a `tab` field indicating its source, as well as `e_issn`, `p_issn`, and `issn_l` fields populated from the enriched pipeline output; journals appearing in multiple tabs are deduplicated by name (first-seen tab wins)
- generates gaps for `Alternative journal name` (highest priority weight = 12, `alt_name` type) when a journal has no Scimago data
- generates low-priority `fill` gaps for `e-ISSN`, `p-ISSN`, and `ISSN-L` **only when the journal has none of the three ISSN fields** — if any ISSN is already present (`e-ISSN`, `p-ISSN`, or `ISSN-L`), all three ISSN gap entries are suppressed
- writes `agent/output/gap_report.json`
- records `wtp_dir` as the parent directory when run with defaults
- `--skip-download` skips all sheet tab downloads (external data verification always runs)
- accepts optional `--credentials` argument forwarded to the Sheets API call

### agent/scripts/run_enrichment.py

Current orchestrator.

- can run gap analysis unless `--skip-gap-analysis` is passed
- loads remote deduplication keys from `Agent_suggestions` and `Agent_suggestions_processed` Google Sheets tabs before processing any journal; if remote loading fails, logs a warning and falls back to local-CSV deduplication only
- merges remote keys with local `existing_keys` so any suggestion already present in either sheet is silently dropped before persistence
- accepts optional `--credentials` argument forwarded to the remote key loader
- loads the gap report and selects journals by priority
- supports `--journal` for exact single-journal targeting
- supports `--journal-limit`, `--max-suggestions`, `--output`, `--state`, and `--log-dir`
- supports `--agent-timeout-seconds` for the per-journal OMP budget
- requires an existing gap report when `--skip-gap-analysis` is used
- gives each journal a unique session id
- **pre-fetches DOAJ API data** for each journal via `fetch_doaj_data()` (called from the host, before the OMP run): uses `https://doaj.org/api/v3/search/journals/issn:<issn>` and extracts `found`, `doaj_title`, `publisher`, `apc_has_apc`, `apc_price`, `apc_currency`, `boai`, `license`; the pre-fetched data is included in the `prefetched_doaj_data` field of the prompt Evidence JSON and in the runner log line
- **pre-fetches OpenAlex data** for each journal via `fetch_openalex_data()`: uses `https://api.openalex.org/sources?filter=issn:<issn>&select=...` and extracts `is_oa`, `apc_usd`, `apc_prices`, `host_organization_name`; included in the prompt as `prefetched_openalex_data`; OpenAlex APC data is sourced from ESAC/OpenAPC registries and is reliable for Hybrid and OA journals
- **CrossRef ISSN bypass** via `generate_crossref_issn_suggestions()`: queries `https://api.crossref.org/journals?query=<name>&rows=5` (CrossRef journals API), with fallback to `https://api.crossref.org/works?query.bibliographic=<name>&rows=1`; when an exact or high-confidence title match is found, returns ISSN suggestion rows (`p-ISSN`, `e-ISSN`, `ISSN-L`) directly — without LLM involvement — using `confidence=0.90` (exact match); pre-registers these rows in `existing_keys` so the LLM cannot duplicate them
- **LLM-skip optimization**: after CrossRef ISSN suggestions are resolved, computes `remaining_gap_fields = {g["field"] for g in journal_gap["gaps"]} - crossref_covered`; if `remaining_gap_fields` is a subset of `{"Institution", "Institution type"}` (fields the LLM has never resolved in practice), sets `skip_llm=True` and bypasses the OMP run entirely; status is `ok` if CrossRef produced rows, `unresolved` otherwise
- **omits DOAJ API lookup URLs** from `lookup_urls` when pre-fetched data is available, to prevent the model from re-fetching DOAJ (which may be Cloudflare-blocked); DOAJ API URLs are only included when DOAJ pre-fetch returned no result
- passes known metadata (including `e_issn`, `p_issn`, `issn_l`), prefetched DOAJ/OpenAlex/CrossRef data, requested gaps, and direct lookup URLs to the model; when any ISSN is known the prompt includes `scimago_by_issn` as the preferred source for resolving `Alternative journal name`
- tells the model to use its tools, return JSON only, cite prefetch API URLs for prefetched evidence, and stay within the provided existing-journal backlog
- instructs the model to read `prefetched_doaj_data`, `prefetched_openalex_data`, and `prefetched_crossref_data` before calling any tools: `apc_has_apc=true` with a price is sufficient evidence for Business model='OA' and APC Euros; `apc_has_apc=false` is sufficient evidence for Business model='OA diamond' and APC Euros='0'
- instructs the model that **Business model requires hard evidence** and that inference from publisher reputation is not acceptable
- instructs the model that **absence of APC mention is not evidence** that APC = 0; only suggest APC Euros = 0 when the source explicitly states no charge, or when `prefetched_doaj_data.apc_has_apc` is false
- instructs the model to convert non-Euro APC values using approximate current exchange rates
- appends accepted rows and updates run state after each journal; persisted rows are canonicalized through `suggestions_io.py` so only the highest-confidence suggestion survives for each `(journal, field)` pair
- writes checkpoint CSVs every 10 processed journals
- provides `load_suggestion_keys_from_tabs(service, tab_names, spreadsheet_id)` — returns a set of `(journal, field, suggested_value)` triples read from the given sheet tabs; missing or unrecognised tabs are silently skipped

### agent/scripts/omp_runtime.py

OMP (oh-my-pi) wrapper — replaces the former Docker/OpenClaw runtime.

- builds and runs `omp -p <prompt> --mode json --no-session --auto-approve --thinking off --append-system-prompt @agent/prompt/system.md --max-time <seconds> [--model <model>]` with the project root as working directory
- the OMP binary is `omp` (override with `JOURNALMIND_OMP_COMMAND`); the model defaults to OMP's configured model and can be overridden with `JOURNALMIND_MODEL` (passed as `--model`)
- parses the JSONL event stream on stdout and keeps the text of the last completed assistant message
- extracts the model JSON object from that text
- **strips `//` line comments** from JSON-like text before parsing (via `_strip_json_line_comments()`): LLMs sometimes annotate JSON values with `// comment` which is invalid JSON; the comment stripper correctly handles `//` inside string literals
- retries once when the model returns non-JSON output; the retry uses a **compact prompt** (full instructions stripped, only a short JSON-only directive + the Evidence JSON block retained) to prevent context overflow from compounding across attempts
- enforces the per-journal timeout both via OMP's `--max-time` and a host-side process timeout with process-group kill; a timeout produces a synthetic `unresolved` payload
- writes prompt, stdout JSONL, and stderr logs per journal session

### agent/scripts/issn_lookups.py

ISSN → title lookup builders for the deterministic alt-name flow.

- `load_scimago_issn_title_lookup(wtp_dir)` — reads `../data_extraction/scimagojr.csv.gz` (`;`-separated; the `Issn` column holds a comma-separated list) and maps each formatted ISSN to its `Title` values
- `load_doaj_issn_title_lookup(wtp_dir)` — reads `DOAJ.csv.gz` and maps print ISSN / EISSN to `Journal title`
- `load_openapc_issn_title_lookup(wtp_dir)` — reads `openapc.csv.gz` and maps `issn` / `issn_print` / `issn_electronic` / `issn_l` to `journal_full_title` (skipping `NA`)
- uses only stdlib csv/gzip plus the canonical `format_issn()` from `../scripts/libraries.py`

### agent/scripts/issn_alt_name_suggestions.py

Deterministic `Alternative journal name` generator.

- scans the enriched `../data_extracted/*.csv` field files
- targets journals whose `Alternative journal name` is empty, that already have at least one ISSN, and for which at least one of `Present in Scimago`, `Present in DOAJ`, or `Present in openAPC` is `No`
- resolves the alternative title by ISSN with source priority `Scimago -> DOAJ -> OpenAPC` (lookups from `issn_lookups.py`)
- skips no-op matches whose normalized title equals the current journal name
- skips ambiguous ISSN collisions within a source when one ISSN maps to multiple genuinely different titles
- writes `confidence = 1.00`, `suggestion_type = "alt_name"`, `priority = "high"` rows into `agent/output/Agent_suggestions.csv`
- uses `agent/scripts/suggestions_io.py` so deterministic suggestions and AI suggestions share the same CSV canonicalization rules

### agent/scripts/upload_suggestions.py

Upload script for staging suggestions in the Google Sheet.

- reads `Agent_suggestions.csv` (default: `agent/output/Agent_suggestions.csv`)
- removes duplicate `(journal, field)` rows before upload and keeps the highest-confidence row for each suggested column
- rewrites the cleaned CSV back to disk before uploading when duplicates were present
- creates or reuses the `Agent_suggestions` tab in the spreadsheet
- clears existing content and writes a fresh header + data rows
- prepends a `Status` column (pending / approve / reject); new rows are always uploaded as `pending`
- accepts optional `--input` and `--credentials` arguments

### agent/scripts/suggestions_io.py

Persistence and sanitization layer.

- initializes and normalizes `Agent_suggestions.csv`
- imports canonical `norm_name()` from `../scripts/libraries.py`
- canonicalizes the persisted CSV on `(journal, field)` and keeps the highest-confidence row for each suggested column
- still loads exact existing-key triples `(journal, field, suggested_value)` for exact-match deduplication against local/remote suggestion history
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

## Agent Prompt

`agent/prompt/system.md` is the system prompt appended to every OMP run.

Current emphasis:

- one journal per run
- use tools for research; but read `prefetched_doaj_data` first before calling any tools
- start from caller-provided lookup URLs (Scimago for alternative name) after consuming pre-fetched DOAJ/OpenAlex/CrossRef data
- DOAJ/OpenAlex/CrossRef API data pre-fetched by the runner on the host avoids Cloudflare blocking
- cite prefetch API URLs in `source_urls` when the evidence comes from prefetched data; never fabricate URLs
- return one JSON object when the caller asks for JSON only
- do not propose adding new journals
- do not write files in the normal automation path
- prefer unresolved over guesses when sources are blocked or ambiguous
- `Alternative journal name` suggestions must use `suggestion_type: "alt_name"`; ISSN suggestions must be in `XXXX-XXXX` format

## Review Workflow

The `Agent_suggestions` Google Sheets tab is the staging area for human review.

- both AI-generated rows and deterministic ISSN-based `Alternative journal name` rows are staged in the same tab
- each row has a `Status` column (pending / approve / reject)
- new rows are always uploaded as `pending`
- team members set the status to `approve` or `reject` after reviewing evidence
- running **WhereToPublish → Apply Reviewed Suggestions** (Apps Script):
  - `approve` rows: the suggestion is written to the appropriate data tab, then the row is immediately deleted from `Agent_suggestions` and archived in `Agent_suggestions_processed`
  - `reject` rows: the row is immediately deleted from `Agent_suggestions` and archived in `Agent_suggestions_processed` without any data change
  - `pending` rows are left untouched
  - each row is deleted from `Agent_suggestions` as soon as it is archived; if the script times out, only unprocessed rows remain and the run can be safely retried
- the `Agent_suggestions_processed` tab accumulates all reviewed suggestions for performance analysis (see `NEXT_STEPS.md`)

## Persistence Contract

The effective contract between Python and the model is:

- one JSON object per journal
- only requested gap fields are eligible
- the model owns research and browsing, not file mutation
- Python decides what gets persisted
- if no row survives validation, the journal is treated as unresolved

## Boundaries

- the WhereToPublish repo (`..`) is treated as read-only input during enrichment work
- the spreadsheet's data tabs are written only by the Apps Script review workflow, never by Python directly
- blocked publisher pages and missing source evidence are normal unresolved cases, not separate failure modes

## Outputs

Default output files:

- `agent/output/Agent_suggestions.csv`
- `agent/output/gap_report.json`
- `agent/output/state/run_state.json`

Checkpoint files:

- `agent/output/state/checkpoint_suggestions_*.csv`

Run-level logs:

- `agent/output/logs/run-*.runner.log` — combined `run_enrichment.py` + `issn_alt_name_suggestions.py` output

Per-journal artifacts:

- `agent/output/logs/enrichment-*.prompt.txt`
- `agent/output/logs/enrichment-*.stdout.json`
- `agent/output/logs/enrichment-*.stderr.log`

## Known Runtime Constraints

- `web_search` in OMP requires a configured search provider (`webSearchOrder` in `~/.omp/agent/config.yml`); without one, the agent uses pre-fetched DOAJ/OpenAlex/CrossRef data and direct `web_fetch` calls instead
- publisher websites (e.g., Scimago, SAGE, Elsevier) are often protected by Cloudflare WAF and return 403; DOAJ, OpenAlex, and CrossRef APIs (all pre-fetched on the host) bypass this
- `--skip-download` skips all 10 tab downloads; use it when the `data_extracted/` CSVs are already current
- journals with only `Institution` / `Institution type` gaps are skipped by the LLM-skip optimization (these fields have never been resolved by the model in practice)

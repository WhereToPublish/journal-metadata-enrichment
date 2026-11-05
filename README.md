# WhereToPublish Journal Metadata Enrichment

OMP (oh-my-pi) tooling for generating reviewable journal metadata suggestions for the WhereToPublish project. This project lives **inside** the WhereToPublish repository (`WhereToPublish/journal-metadata-enrichment/`); the parent directory (`..`) is the WhereToPublish repo itself and is treated as read-only input.

The AI agent is **OMP (oh-my-pi)**, installed on the host and already plugged to an LLM (configuration in `~/.omp/agent/config.yml`). No Docker container and no local Ollama model are involved — each journal is researched by one non-interactive OMP run on the host.

## Current Workflow

The runtime is built around one journal per OMP run.

1. `run_agent.sh` is the only entry point.
2. The launcher uses the repo-local `.venv/bin/python`, checks that `polars` is importable, verifies that `omp` is available, and delegates the whole run to `agent/scripts/run_enrichment.py`, forwarding extra arguments and always appending `--agent-timeout-seconds`.
3. `agent/scripts/gap_analysis.py` verifies that the required external data files exist in the WhereToPublish repo (`../data_extraction/`), downloads **all 10 Google Sheets tabs** via the Sheets API, refreshes the WhereToPublish pipeline (`scripts/update_extracted.py` → `scripts/data_process.py`, run with the WTP repo root as working directory), and writes `agent/output/gap_report.json` unless `--skip-gap-analysis` is passed. ISSN gaps (`e-ISSN`, `p-ISSN`, `ISSN-L`) are only generated for journals that have **none** of the three ISSN fields — if any ISSN is already known, all three ISSN gaps are suppressed.
4. `agent/scripts/run_enrichment.py` loads remote suggestion keys from the `Agent_suggestions` and `Agent_suggestions_processed` Google Sheets tabs, then selects journals from the gap report — all journals across all tabs are processed, sorted by priority (high → medium → low) — and opens one OMP run per journal. It stops when `--max-suggestions` valid suggestions have been written (default: 15) or all journals are exhausted. Before each OMP run, the runner performs three host-side pre-fetch steps:
   - **DOAJ API pre-fetch**: retrieves `apc_has_apc`, `apc_price`, `apc_currency`, `boai`, and `license` without Cloudflare issues; included in the prompt as `prefetched_doaj_data`.
   - **OpenAlex pre-fetch**: retrieves `is_oa`, `apc_usd`, `apc_prices`, and `host_organization_name` from the OpenAlex sources API; included in the prompt as `prefetched_openalex_data`.
   - **CrossRef ISSN bypass** (`generate_crossref_issn_suggestions()`): queries the CrossRef journals API (`https://api.crossref.org/journals?query=<name>&rows=5`) with a works API fallback; if an exact or high-confidence title match is found, ISSN suggestion rows (`p-ISSN`, `e-ISSN`, `ISSN-L`) are generated directly — without calling the LLM — and pre-registered in `existing_keys` so the model cannot duplicate them.

   After CrossRef ISSN suggestions are resolved, the runner checks whether any actionable gaps remain. If the only remaining gap fields are `Institution` and/or `Institution type` (which the LLM has never been able to resolve in practice), the **LLM is skipped entirely** for that journal (`skip_llm` optimization). This applies to roughly three quarters of the backlog, reducing their processing time from minutes to seconds. For all other journals, OMP runs as before.

   The prompt also includes `scimago_by_issn` whenever an ISSN is known, for resolving Alternative journal name. The `Alternative journal name` gap has the highest priority weight (12), ahead of Business model (10), so it is researched first. The agent is instructed that Business model requires hard evidence (explicit text on the journal page, DOAJ, or prefetched_doaj_data) and that absence of APC mention on a website is **not** evidence that APC = 0; `prefetched_doaj_data.apc_has_apc=false` is accepted as explicit evidence for APC Euros = 0. The per-journal OMP timeout defaults to 900 seconds and is configurable. When `--skip-gap-analysis` is used, the selected gap-report file must already exist.
5. OMP does the journal-level research with its tools (web search and page fetch) and returns one JSON object.
6. Python validates that JSON and writes only accepted rows to `agent/output/Agent_suggestions.csv` plus run state and logs. `Agent_suggestions.csv` is canonicalized on `(journal, field)` so only the highest-confidence row is kept for each suggested column; exact suggestions already present in either the local CSV or the remote Google Sheet tabs are deduplicated before persistence.
7. After `run_enrichment.py` finishes, the launcher runs `agent/scripts/issn_alt_name_suggestions.py` to deterministically append `Alternative journal name` suggestions from the enriched WhereToPublish CSVs. The script scans journals whose `Alternative journal name` is empty, that already have at least one ISSN, and for which at least one of `Present in Scimago`, `Present in DOAJ`, or `Present in openAPC` is `No`. It matches by ISSN with source priority **Scimago → DOAJ → OpenAPC** (lookups built directly from `../data_extraction/*.csv.gz` by `agent/scripts/issn_lookups.py`) and writes `confidence = 1.00` rows when the matched source title is a real name change rather than a formatting-only variant.
8. `agent/scripts/upload_suggestions.py` pushes `Agent_suggestions.csv` to the `Agent_suggestions` tab of the WhereToPublish Google Sheet. Before upload it drops duplicate `(journal, field)` rows from the CSV and keeps only the highest-confidence suggestion for each column. Each row gets a **Status** column (pending / approve / reject); new rows are uploaded as `pending`.

## Review Workflow

After upload, team members open the `Agent_suggestions` tab and set each row's **Status** to:

- `approve` — apply the suggestion to the data tab
- `reject` — discard the suggestion (archived for analysis)
- `pending` — not yet reviewed (default after upload)

Running **WhereToPublish → Apply Reviewed Suggestions** from the Apps Script menu:

- `approve` rows: the suggested value is written to the journal's field in the appropriate data tab, then the row is immediately deleted from `Agent_suggestions` and archived in `Agent_suggestions_processed`.
- `reject` rows: the row is immediately deleted from `Agent_suggestions` and archived in `Agent_suggestions_processed` without any data change.
- `pending` rows are left untouched.

Each row is deleted from `Agent_suggestions` as soon as it is archived, so if the script is interrupted (e.g. Apps Script timeout), only unprocessed rows remain and the script can be safely re-run.

The `Agent_suggestions_processed` archive contains all reviewed suggestions (approve + reject) and can be used to analyze agent performance over time (see `NEXT_STEPS.md`).

## Architecture

```
Host
├── OMP (oh-my-pi)                     — installed on the host, plugged to an LLM (~/.omp/agent/config.yml)
│     └── one `omp -p <prompt>` run per journal (non-interactive, JSONL output on stdout)
├── Python .venv                       — orchestration only (gap analysis, validation, CSV I/O)
└── WhereToPublish repo (parent dir `..`)  — read-only input
      ├── scripts/sheets_client.py     — canonical Google Sheets API module (imported via sys.path)
      ├── scripts/libraries.py         — canonical norm_name() / format_issn()
      ├── data_extraction/*.csv.gz     — external data sources (Scimago, DOAJ, OpenAPC, PCI, Dataverse)
      ├── data_extracted/*.csv         — raw Google Sheets tabs (downloaded each run)
      └── data/*.csv                   — enriched output of the WTP pipeline
```

There is no container boundary anymore: OMP runs directly on the host with the project root as working directory, and its per-journal instructions are appended to the OMP system prompt from `agent/prompt/system.md` (`--append-system-prompt @agent/prompt/system.md`).

## Runtime Boundaries

- Python owns orchestration, gap-report loading, journal selection, deterministic ISSN matching, deduplication, validation, and persistence.
- OMP owns journal-level browsing, source gathering, and JSON suggestion generation. It runs on the host as a plain CLI process.
- Google Sheets API (service-account auth) is used for downloading sheet tabs, uploading suggestions, and loading remote deduplication keys.
- `run_agent.sh` owns the `omp` availability check, the `.venv` preflight, and passes the per-journal timeout to the enrichment runner.
- Before each run, the enrichment pipeline loads remote keys from `Agent_suggestions` and `Agent_suggestions_processed` to avoid re-generating suggestions that are already under review or have already been processed.
- The current agent runtime works only on journals already present in the caller-selected gap backlog.
- The model does not edit CSV or state files in the normal automation path.
- The WhereToPublish repo (`..`) is treated as read-only input during enrichment runs.
- Weak, blocked, or contradictory sources become `unresolved` instead of speculative rows.

## Key Files

- `run_agent.sh`: launcher, `omp` preflight, `.venv` preflight, timeout wiring, and combined runner logging.
- `requirements.txt`: Python dependencies for the repo-local virtual environment.
- `../scripts/sheets_client.py`: shared Google Sheets API module (auth, download, upload helpers), imported by all agent scripts through `enrichment_common.WTP_SCRIPTS_DIR`.
- `agent/scripts/enrichment_common.py`: shared paths and schemas. Defines `PROJECT_ROOT` (this project), `DEFAULT_WTP_DIR` (the parent WhereToPublish repo), `WTP_SCRIPTS_DIR` (`../scripts`), output/log/state paths, the suggestions CSV headers, allowed values, and `SYSTEM_PROMPT_PATH` (`agent/prompt/system.md`).
- `agent/scripts/gap_analysis.py`: verifies required external data files are present in `../data_extraction/` (fails fast with a clear error if any are missing), downloads all 10 sheet tabs via the Sheets API, refreshes the WTP pipeline, and writes `agent/output/gap_report.json`.
- `agent/scripts/run_enrichment.py`: loads remote deduplication keys from Google Sheets, pre-fetches DOAJ/OpenAlex/CrossRef data before each journal, applies the LLM-skip optimization for Institution-only journals, prompts OMP per journal, accepts a configurable per-journal timeout (`--agent-timeout-seconds`), requires an existing gap report when `--skip-gap-analysis` is used, loops over journals, and records run state.
- `agent/scripts/omp_runtime.py`: OMP wrapper that builds `omp -p <prompt> --mode json --no-session --auto-approve --thinking off --append-system-prompt @agent/prompt/system.md --max-time <seconds> [--model <model>]`, parses the JSONL stdout for the final assistant message, extracts the suggestion JSON object (stripping `//` comments and fenced blocks), retries once on non-JSON replies with a compact prompt, and writes prompt/stdout/stderr artifacts per journal.
- `agent/scripts/suggestions_io.py`: validates and persists supported rows, imports canonical `norm_name()` from the WhereToPublish codebase, and canonicalizes `Agent_suggestions.csv` on `(journal, field)` so the highest-confidence row wins for each suggested column.
- `agent/scripts/issn_lookups.py`: builds ISSN → title lookup maps for Scimago, DOAJ, and OpenAPC directly from the `../data_extraction/*.csv.gz` files (stdlib csv/gzip + canonical `format_issn()`).
- `agent/scripts/issn_alt_name_suggestions.py`: deterministic `Alternative journal name` generator using the lookups from `issn_lookups.py`; writes confidence-1.00 `alt_name` rows to `agent/output/Agent_suggestions.csv`.
- `agent/scripts/upload_suggestions.py`: uploads `Agent_suggestions.csv` to the `Agent_suggestions` tab with a Status column (pending/approve/reject); before upload it rewrites the CSV with duplicate `(journal, field)` rows removed and keeps the highest-confidence row.
- `agent/prompt/system.md`: the OMP system prompt appended to every per-journal run (mission, strict rules, pre-fetched data interpretation, confidence rules, output schema).
- `../`: the WhereToPublish repo — source data and pipeline, treated as input only.
- `../scripts/fetch_sheet.py`: standalone utility to download any spreadsheet tab by field slug via the Sheets API.
- `GOOGLE_APP_SCRIPT.md`: Apps Script setup for the human review workflow.
- `NEXT_STEPS.md`: roadmap for using the suggestion history to analyze and improve agent performance.

## Requirements

- macOS (or Linux) with **OMP (oh-my-pi)** installed (`omp` in PATH) and already authenticated to an LLM (run `omp` once interactively, or see `~/.omp/agent/config.yml`)
- a repo-local virtual environment at `.venv`
- Python dependencies installed from `requirements.txt`
- a Google service-account JSON key with read/write access to the spreadsheet, placed at `~/.config/wheretopublish/google_service_account.json` or pointed to by `GOOGLE_SERVICE_ACCOUNT_KEY`
- external data files present in `../data_extraction/` — run `bash scripts/download_extraction.sh` from the WhereToPublish repo root to populate them

Minimum setup:

```bash
# Python dependencies
.venv/bin/python -m pip install -r requirements.txt

# Verify OMP is ready (lists the configured default model)
omp models
```

Use `JOURNALMIND_MODEL=<model>` to override the OMP model for one run (passed as `omp --model`).
Use `JOURNALMIND_AGENT_TIMEOUT_SECONDS=<seconds>` to override the default per-journal timeout (900 seconds).
Use `JOURNALMIND_MAX_SUGGESTIONS=<n>` to override the default suggestion cap (15) from the launcher.
Use `JOURNALMIND_PYTHON=<path>` to override the Python interpreter (default: `.venv/bin/python`).

The system never writes directly to the data tabs of the Google Sheet. Persisted suggestions are always staged in `Agent_suggestions` for human review.

## Quickstart

```bash
# Run
./run_agent.sh
```

The launcher:

1. checks that `omp` is in PATH and that `.venv/bin/python` can import `polars`
2. runs `agent/scripts/run_enrichment.py`, which spawns one non-interactive `omp -p` run per journal with a per-journal timeout of 900 seconds unless overridden via `JOURNALMIND_AGENT_TIMEOUT_SECONDS`
3. loads remote deduplication keys from `Agent_suggestions` and `Agent_suggestions_processed`
4. appends `agent/scripts/issn_alt_name_suggestions.py` output to the same runner log after the AI enrichment pass

If you pass `--skip-gap-analysis`, the gap report must already exist at `agent/output/gap_report.json` unless you also override `--gap-report`.

Live monitoring is via terminal output and the runner log at `agent/output/logs/run-*.runner.log`.

On a full run, gap analysis downloads all 10 tabs (≈2400 journals) and typically identifies ~1250 journals with gaps across all field types. The enrichment pipeline processes them in priority order (high → medium → low) and stops at `--max-suggestions`.

## Upload Suggestions to Google Sheet

You can combine AI-generated suggestions and deterministic ISSN-based `Alternative journal name` suggestions in the same review CSV. Both flows write to `agent/output/Agent_suggestions.csv`, and the CSV is canonicalized on `(journal, field)` so only the highest-confidence suggestion for each column is kept.

Generate deterministic ISSN-based `Alternative journal name` suggestions:

```bash
.venv/bin/python agent/scripts/issn_alt_name_suggestions.py
```

After a run, push `Agent_suggestions.csv` to the spreadsheet for review:

```bash
.venv/bin/python agent/scripts/upload_suggestions.py
```

This replaces the content of the `Agent_suggestions` tab with the current suggestions. Before upload, duplicate `(journal, field)` rows are removed from `Agent_suggestions.csv` and only the highest-confidence row for each suggested column is kept. Each row has a **Status** column (pending / approve / reject) set to `pending` by default. Team members set the status for each row, then run **WhereToPublish → Apply Reviewed Suggestions** from the Apps Script menu.

Options:

```bash
# Custom input file
.venv/bin/python agent/scripts/upload_suggestions.py --input /path/to/Agent_suggestions.csv

# Custom credentials path
.venv/bin/python agent/scripts/upload_suggestions.py --credentials /path/to/key.json
```

## Download a Sheet Tab

`../scripts/fetch_sheet.py` downloads any tab from the spreadsheet as a CSV:

```bash
# Download Genetics & Genomics
.venv/bin/python ../scripts/fetch_sheet.py --field genetics_genomics --output /tmp/out.csv

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

# Generate deterministic ISSN-based Alternative journal name suggestions
.venv/bin/python agent/scripts/issn_alt_name_suggestions.py

# Override the OMP model for one run
JOURNALMIND_MODEL=<model> ./run_agent.sh --max-suggestions 1

# Increase the per-journal OMP timeout for one run
JOURNALMIND_AGENT_TIMEOUT_SECONDS=1200 ./run_agent.sh --max-suggestions 1
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

- `agent/output/Agent_suggestions.csv`
- `agent/output/gap_report.json`
- `agent/output/state/run_state.json`

Run-level logs:

- `agent/output/logs/run-*.runner.log` — combined `run_enrichment.py` + `issn_alt_name_suggestions.py` output

Per-journal artifacts:

- `agent/output/logs/enrichment-*.prompt.txt`
- `agent/output/logs/enrichment-*.stdout.json`
- `agent/output/logs/enrichment-*.stderr.log`

Checkpoint files:

- `agent/output/state/checkpoint_suggestions_*.csv`

Known runtime limitations:

- `web_search` in OMP requires a configured search provider (see the `webSearchOrder` list in `~/.omp/agent/config.yml`); without one, the agent relies on pre-fetched DOAJ/OpenAlex/CrossRef data and direct `web_fetch` calls
- publisher websites (e.g., Scimago, SAGE, Elsevier) are often protected by Cloudflare WAF and return 403; DOAJ, OpenAlex, and CrossRef APIs (all pre-fetched on the host before each run) bypass this for Business model, APC Euros, and ISSN fields
- journals with only `Institution` / `Institution type` gaps are skipped by the LLM-skip optimization (these fields have never been resolved by the model in practice)
- `--skip-download` skips all 10 tab downloads; use it when the `data_extracted/` CSVs are already current

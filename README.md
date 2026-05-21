# WhereToPublish Journal Metadata Enrichment

Ollama + Docker tooling for generating reviewable journal metadata suggestions for the WhereToPublish project. OpenClaw runs exclusively inside a Docker container — it is not installed on the host.

## Current Workflow

The current runtime is built around one journal per OpenClaw session, with OpenClaw running inside the `journalmind-openclaw` Docker container.

1. `run_agent.sh` is the only entry point.
2. The launcher uses the repo-local `.venv/bin/python`, checks that `polars` is importable, enforces a local `ollama/<tag>` model, verifies that Docker is available and the image exists, and delegates each per-journal OpenClaw run to a fresh Docker container.
3. `agent/scripts/gap_analysis.py` verifies that required external data files exist in the WhereToPublish project, downloads **all 10 Google Sheets tabs** (Generalist, Anatomy & Physiology, Cancer, Development, Ecology & Evolution, Genetics & Genomics, Immunology, Molecular & Cellular Biology, Neurosciences, Plants) via the Sheets API, refreshes the WhereToPublish pipeline, and writes `agent/output/gap_report.json` unless `--skip-gap-analysis` is passed. ISSN gaps (`e-ISSN`, `p-ISSN`, `ISSN-L`) are only generated for journals that have **none** of the three ISSN fields — if any ISSN is already known, all three ISSN gaps are suppressed.
4. `agent/scripts/run_enrichment.py` loads remote suggestion keys from the `Agent_suggestions` and `Agent_suggestions_processed` Google Sheets tabs, then selects journals from the gap report — all journals across all tabs are processed, sorted by priority (high → medium → low) — and opens one fresh embedded OpenClaw session per journal (inside Docker). It stops when `--max-suggestions` valid suggestions have been written (default: 15) or all journals are exhausted. Before starting each Docker container, the runner **pre-fetches DOAJ API data** for the journal from the host (no Cloudflare issues) and includes it directly in the prompt as `prefetched_doaj_data`; the agent is instructed to use this data first without re-fetching DOAJ from inside Docker. When DOAJ data is not available, `doaj_api_by_issn` and `doaj_api_by_title` lookup URLs are provided instead. The prompt also includes `scimago_by_issn` whenever an ISSN is known, for resolving Alternative journal name. The `Alternative journal name` gap has the highest priority weight (12), ahead of Business model (10), so it is researched first. The agent is instructed that Business model requires hard evidence (explicit text on the journal page, DOAJ, or prefetched_doaj_data) and that absence of APC mention on a website is **not** evidence that APC = 0; `prefetched_doaj_data.apc_has_apc=false` is accepted as explicit evidence for APC Euros = 0. The per-journal OpenClaw timeout defaults to 900 seconds and is configurable. When `--skip-gap-analysis` is used, the selected gap-report file must already exist.
5. OpenClaw does the journal-level research with tools (including web search and page fetch) and returns one JSON object.
6. Python validates that JSON and writes only accepted rows to `agent/output/Agent_suggestions.csv` plus run state and logs. `Agent_suggestions.csv` is canonicalized on `(journal, field)` so only the highest-confidence row is kept for each suggested column; exact suggestions already present in either the local CSV or the remote Google Sheet tabs are deduplicated before persistence.
7. After `run_enrichment.py` finishes, the launcher runs `agent/scripts/issn_alt_name_suggestions.py` to deterministically append `Alternative journal name` suggestions from the enriched WhereToPublish CSVs. The script scans journals whose `Alternative journal name` is empty, that already have at least one ISSN, and for which at least one of `Present in Scimago`, `Present in DOAJ`, or `Present in openAPC` is `No`. It matches by ISSN with source priority **Scimago → DOAJ → OpenAPC** and writes `confidence = 1.00` rows when the matched source title is a real name change rather than a formatting-only variant.
8. `agent/scripts/upload_suggestions.py` pushes `Agent_suggestions.csv` to the `Agent_suggestions` tab of the WhereToPublish Google Sheet. Before upload it drops duplicate `(journal, field)` rows from the CSV and keeps only the highest-confidence suggestion for each column. Each row gets a **Status** column (pending / approve / reject); new rows are uploaded as `pending`.

## Review Workflow

After upload, team members open the `Agent_suggestions` tab and set each row's **Status** to:

- `approve` — apply the suggestion to the data tab
- `reject` — discard the suggestion (archived for analysis)
- `pending` — not yet reviewed (default after upload)

Running **WhereToPublish → Apply Reviewed Suggestions** from the Apps Script menu:

- `approve` rows: the suggested value is written to the journal's field in the appropriate data tab, then the row is moved to `Agent_suggestions_processed`.
- `reject` rows: the row is moved to `Agent_suggestions_processed` without any data change.
- `pending` rows are left untouched.

The `Agent_suggestions_processed` archive contains all reviewed suggestions (approve + reject) and can be used to analyze agent performance over time (see `NEXT_STEPS.md`).

## Architecture

```
Host
├── Ollama (localhost:11434)          — serves qwen2.5:14b-ctx128k or any local model
├── Python .venv                      — orchestration only (gap analysis, validation, CSV I/O)
└── Docker Desktop
    └── journalmind-openclaw container (per journal)
        ├── OpenClaw --local          — journal research + web browsing (Chromium)
        ├── /app/workspace            — agent instructions baked into image
        └── /data/output → agent/output   — only mounted host volume
```

OpenClaw runs exclusively inside Docker and has no access to the host filesystem beyond `agent/output/`. Ollama runs on the host; the container reaches it at `http://host.docker.internal:11434`. Each journal spawns a fresh container that exits when the journal session completes.

The Docker image is built locally from `Dockerfile`. The container entrypoint runs `openclaw onboard` at startup to configure Ollama connectivity inside the container, then delegates to the requested `openclaw agent` command.

## Runtime Boundaries

- Python owns orchestration, gap-report loading, journal selection, deterministic ISSN matching, deduplication, validation, and persistence.
- OpenClaw owns journal-level browsing, source gathering, and JSON suggestion generation. It runs exclusively inside Docker.
- Google Sheets API (service-account auth) is used for downloading sheet tabs, uploading suggestions, and loading remote deduplication keys.
- `run_agent.sh` owns model validation, Docker image/daemon checks, and passes the per-journal timeout to the enrichment runner.
- Before each run, the enrichment pipeline loads remote keys from `Agent_suggestions` and `Agent_suggestions_processed` to avoid re-generating suggestions that are already under review or have already been processed.
- The current agent runtime works only on journals already present in the caller-selected gap backlog.
- The model does not edit CSV or state files in the normal automation path.
- `WhereToPublish.github.io/` is treated as read-only input during enrichment runs.
- Weak, blocked, or contradictory sources become `unresolved` instead of speculative rows.

## Key Files

- `run_agent.sh`: launcher, `.venv` preflight, Docker/Ollama checks, local-model enforcement, timeout wiring, and combined runner logging.
- `Dockerfile`: builds the `journalmind-openclaw` image from `ghcr.io/openclaw/openclaw:latest`, installs Chromium, and bakes the workspace instructions into the image.
- `agent/docker/entrypoint.sh`: container entrypoint — runs `openclaw onboard` to configure Ollama connectivity, patches the workspace path, then execs the requested command.
- `requirements.txt`: Python dependencies for the repo-local virtual environment.
- `WhereToPublish.github.io/scripts/sheets_client.py`: shared Google Sheets API module (auth, download, upload helpers). Used by both the WTP pipeline scripts and the agent scripts.
- `agent/scripts/gap_analysis.py`: verifies required external data files are present in `WhereToPublish.github.io/data_extraction/` (fails fast with a clear error if any are missing), downloads all 10 sheet tabs via the Sheets API, refreshes the WTP pipeline, and writes `agent/output/gap_report.json`.
- `agent/scripts/issn_alt_name_suggestions.py`: deterministic `Alternative journal name` generator. Reuses the canonical WhereToPublish ISSN normalization and lookup loaders, scans enriched field CSVs, and writes confidence-1.00 `alt_name` rows to `agent/output/Agent_suggestions.csv`.
- `agent/scripts/run_enrichment.py`: loads remote deduplication keys from Google Sheets, prompts OpenClaw per journal (via Docker), accepts a configurable per-journal OpenClaw timeout, requires an existing gap report when `--skip-gap-analysis` is used, loops over journals, and records run state.
- `agent/scripts/openclaw_runtime.py`: Docker-based OpenClaw wrapper that builds `docker run journalmind-openclaw openclaw agent --local ...`, monitors the session JSONL for a completed assistant response, retries once on non-JSON replies, and recovers a completed payload if the CLI parent lingers after the model has already finished.
- `agent/scripts/suggestions_io.py`: validates and persists supported rows, imports canonical `norm_name()` from the WhereToPublish codebase, and canonicalizes `Agent_suggestions.csv` on `(journal, field)` so the highest-confidence row wins for each suggested column.
- `agent/scripts/upload_suggestions.py`: uploads `Agent_suggestions.csv` to the `Agent_suggestions` tab with a Status column (pending/approve/reject); before upload it rewrites the CSV with duplicate `(journal, field)` rows removed and keeps the highest-confidence row.
- `agent/workspace/`: OpenClaw workspace instructions baked into the Docker image for the per-journal tool-driven workflow.
- `WhereToPublish.github.io/`: source data and pipeline, treated as input only.
- `WhereToPublish.github.io/scripts/fetch_sheet.py`: standalone utility to download any spreadsheet tab by field slug via the Sheets API.
- `GOOGLE_APP_SCRIPT.md`: Apps Script setup for the human review workflow.
- `NEXT_STEPS.md`: roadmap for using the suggestion history to analyze and improve agent performance.

## Requirements

- macOS (or Linux) with Docker Desktop running
- `ollama` installed on the host and running (`ollama serve`)
- at least one local Ollama model pulled; validated default: `qwen2.5:14b-ctx128k`
- a repo-local virtual environment at `.venv`
- Python dependencies installed from `requirements.txt`
- the selected model must be a local Ollama model in the form `ollama/<tag>`
- the `journalmind-openclaw` Docker image built locally (see below)
- a Google service-account JSON key with read/write access to the spreadsheet, placed at `~/.config/wheretopublish/google_service_account.json` or pointed to by `GOOGLE_SERVICE_ACCOUNT_KEY`
- external data files present in `WhereToPublish.github.io/data_extraction/` — run `bash scripts/download_extraction.sh` from inside that repo to populate them

Minimum setup:

```bash
# Build the Docker image (once, or after any change to Dockerfile or agent/workspace/)
docker build -t journalmind-openclaw .

# Python dependencies
.venv/bin/pip install -r requirements.txt

# Start Ollama and pull the model
ollama serve
# Pull qwen2.5:14b, then create the 128K-context variant used by default
ollama pull qwen2.5:14b
ollama create qwen2.5:14b-ctx128k -f agent/docker/Modelfile
```

Use `JOURNALMIND_MODEL=ollama/<tag>` to override the default model for one run.
Use `JOURNALMIND_OPENCLAW_TIMEOUT_SECONDS=<seconds>` to override the default per-journal timeout (900 seconds).

The system never writes directly to the data tabs of the Google Sheet. Persisted suggestions are always staged in `Agent_suggestions` for human review.

## Quickstart

```bash
# 1. Build the Docker image once (or after any workspace/Dockerfile change)
docker build -t journalmind-openclaw .

# 2. Run
./run_agent.sh
```

The launcher:

1. checks Docker, `ollama`, the selected model, and `.venv/bin/python`
2. checks that `.venv/bin/python` can import `polars`
3. enforces a local `ollama/<tag>` model and verifies the Docker image exists
4. runs `agent/scripts/run_enrichment.py`, which spawns one `journalmind-openclaw` container per journal (embedded `openclaw agent --local`) with a per-journal timeout of 900 seconds unless overridden via `JOURNALMIND_OPENCLAW_TIMEOUT_SECONDS`
5. loads remote deduplication keys from `Agent_suggestions` and `Agent_suggestions_processed`
6. appends `agent/scripts/issn_alt_name_suggestions.py` output to the same runner log after the AI enrichment pass

If you pass `--skip-gap-analysis`, the gap report must already exist at `agent/output/gap_report.json` unless you also override `--gap-report`.

Live monitoring is via terminal output and the runner log at `agent/output/logs/run-*.runner.log`.

On a full run, gap analysis downloads all 10 tabs (≈2400 journals) and typically identifies ~1300 journals with gaps across all field types. The enrichment pipeline processes them in priority order (high → medium → low) and stops at `--max-suggestions`.

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

# Generate deterministic ISSN-based Alternative journal name suggestions
.venv/bin/python agent/scripts/issn_alt_name_suggestions.py

# Override the model for one run
JOURNALMIND_MODEL=ollama/qwen2.5:14b-ctx128k ./run_agent.sh --max-suggestions 1

# Increase the per-journal OpenClaw timeout for one run
JOURNALMIND_OPENCLAW_TIMEOUT_SECONDS=1200 ./run_agent.sh --max-suggestions 1
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

- `web_search` inside Docker requires a Brave Search API key (`BRAVE_SEARCH_API_KEY`); without it, the agent uses pre-fetched DOAJ data and direct `web_fetch` calls instead
- publisher websites (e.g., Scimago, SAGE, Elsevier) are often protected by Cloudflare WAF and return 403 from Docker datacenter IPs; the DOAJ API (pre-fetched on the host) bypasses this for Business model and APC Euros fields
- `--skip-download` skips all 10 tab downloads; use it when the `data_extracted/` CSVs are already current

## Stop / Reset

```bash
openclaw config unset agents.defaults.workspace
```

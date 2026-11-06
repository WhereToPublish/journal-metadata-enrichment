# WhereToPublish Journal Metadata Enrichment

OMP (oh-my-pi) tooling for generating reviewable journal metadata suggestions for the WhereToPublish project. This project lives **inside** the WhereToPublish repository (`WhereToPublish/journal-metadata-enrichment/`); the parent directory (`..`) is the WhereToPublish repo itself and is treated as read-only input.

The AI agent is **OMP (oh-my-pi)**, installed on the host and already plugged to an LLM (configuration in `~/.omp/agent/config.yml`). No Docker container and no local Ollama model are involved. Journals are researched by a **swarm of OMP subagents**: each batch of journals is handled by one non-interactive OMP orchestrator run that spawns one parallel subagent per journal (up to 32 concurrent) with OMP's built-in `task` tool.

## Current Workflow

The runtime is built around one swarm of parallel agents per batch of journals.

1. `run_agent.sh` is the only entry point. It performs the preflight checks (OMP available in PATH, `.venv` Python can import `polars`) and delegates everything else to `agent/scripts/run_enrichment.py`, forwarding extra arguments plus the swarm batch timeout (`--swarm-timeout-seconds`, default 3600 s).
2. Unless `--skip-gap-analysis` is given, the runner first calls `agent/scripts/gap_analysis.py`, which verifies the external data files in `../data_extraction/`, downloads **all 10 Google Sheets tabs** via the Sheets API, refreshes the WhereToPublish pipeline (`scripts/update_extracted.py` → `scripts/data_process.py`, run with the WTP repo root as working directory), and writes `agent/output/gap_report.json`. ISSN gaps (`e-ISSN`, `p-ISSN`, `ISSN-L`) are only generated for journals that have **none** of the three ISSN fields.
3. `run_enrichment.py` loads remote deduplication keys from the `Agent_suggestions` and `Agent_suggestions_processed` Google Sheets tabs, then selects the journal backlog from the gap report:
   - journals whose only gaps are `Institution` / `Institution type` (fields the LLM has never resolved in practice) are never sent to the swarm — they are recorded in the run state with status `skipped`;
   - the remainder is sorted by gap priority (high → medium → low) and sliced into batches of `--swarm-size` journals (default 32 — the OMP maximum of concurrent subagents);
   - result files left over by earlier swarm missions (late-finishing subagents, interrupted runs) are harvested from `agent/output/swarm/*/results/` before selection, so no completed research is wasted.
4. For each batch the runner writes a **mission directory** `agent/output/swarm/<batch_id>/` containing `mission.json` (manifest with one ready-made `subagent_prompt` per journal), `inputs/<idx>-<slug>.json` (journal name, known metadata, requested gaps, pre-built public API lookup URLs), and an empty `results/` directory — then starts **one** non-interactive OMP run. That orchestrator agent:
   - reads `mission.json` and spawns one subagent per journal with the `task` tool (issued in consecutive calls of 8 journals each so every subagent runs in parallel without overly long tool calls);
   - passes each journal's `subagent_prompt` verbatim; each subagent reads its input file plus the research protocol `agent/prompt/subagent.md`, does the **entire journal research itself** — DOAJ, OpenAlex, and CrossRef API fetches (via `curl`), Scimago lookups, publisher pages, web search — and writes its result JSON (suggestions, status, notes) to its own `results/<idx>-<slug>.json` file;
   - verifies at the end that every result file exists and respawns one replacement subagent per missing file;
   - replies with a one-line completion report. It never reads or edits result contents and never performs research itself.
5. Python owns persistence: the runner reads every result file, validates each suggestion through `suggestions_io.sanitize_agent_result` (requested-fields-only, schema, format, evidence and confidence rules), deduplicates against the local CSV and the remote sheet keys, and appends accepted rows to `agent/output/Agent_suggestions.csv`, canonicalized on `(journal, field)` so only the highest-confidence row is kept. Result files that were not written before the batch timeout are swept once at the end of the run (OMP's worker daemon keeps finishing in-flight subagents in the background); journals that still produced nothing stay unprocessed and are retried by a later run.
6. After the AI pass, the runner generates deterministic `Alternative journal name` suggestions from the enriched WhereToPublish CSVs (`agent/scripts/issn_alt_name_suggestions.py`): journals with an empty `Alternative journal name`, at least one ISSN, and a missing-source presence flag are matched by ISSN with source priority **Scimago → DOAJ → OpenAPC**; matches that are a real name change (not a formatting variant) are written as `confidence = 1.00` rows into the same CSV.
7. `agent/scripts/upload_suggestions.py` pushes `Agent_suggestions.csv` to the `Agent_suggestions` tab of the WhereToPublish Google Sheet (manual step, run after reviewing the output). Each row gets a **Status** column (pending / approve / reject); new rows are uploaded as `pending`.

## Review Workflow

After upload, team members open the `Agent_suggestions` tab and set each row's **Status** to:

- `approve` — apply the suggestion to the data tab
- `reject` — discard the suggestion (archived for analysis)
- `pending` — not yet reviewed (default after upload)

Running **WhereToPublish → Apply Reviewed Suggestions** from the Apps Script menu:

- `approve` rows: the suggested value is written to the journal's field in the appropriate data tab, then the row is immediately deleted from `Agent_suggestions` and archived in `Agent_suggestions_processed`.
- `reject` rows: the row is immediately deleted from `Agent_suggestions` and archived in `Agent_suggestions_processed` without any data change.
- `pending` rows are left untouched.

Each row is deleted from `Agent_suggestions` as soon as it is archived, so if the script is interrupted, only unprocessed rows remain and the script can be safely re-run.

The `Agent_suggestions_processed` archive contains all reviewed suggestions (approve + reject) and can be used to analyze agent performance over time (see `NEXT_STEPS.md`).

## Architecture

```
Host
├── OMP (oh-my-pi)                     — installed on the host, plugged to an LLM (~/.omp/agent/config.yml)
│     ├── one `omp -p <orchestrator prompt>` run per batch (non-interactive, JSONL output on stdout)
│     │     └── OMP `task` tool: spawns up to 32 parallel subagents per batch
│     │           └── each subagent: reads its journal input + agent/prompt/subagent.md,
│     │               fetches DOAJ / OpenAlex / CrossRef APIs + web pages itself,
│     │               writes its suggestion JSON to the mission results/ directory
├── Python .venv                       — orchestration only (mission setup, validation, CSV I/O)
└── WhereToPublish repo (parent dir `..`)  — read-only input
      ├── scripts/sheets_client.py     — canonical Google Sheets API module (imported via sys.path)
      ├── scripts/libraries.py         — canonical norm_name() / format_issn()
      ├── data_extraction/*.csv.gz     — external data sources (Scimago, DOAJ, OpenAPC, PCI, Dataverse)
      ├── data_extracted/*.csv         — raw Google Sheets tabs (downloaded each run)
      └── data/*.csv                   — enriched output of the WTP pipeline
```

OMP runs directly on the host with the project root as working directory. The orchestrator's operating instructions come from `agent/prompt/system.md` (appended to the OMP system prompt with `--append-system-prompt`); each subagent reads the per-journal research protocol from `agent/prompt/subagent.md`.

## Runtime Boundaries

- Python owns orchestration, gap-report loading, backlog selection, mission construction, result validation, deterministic ISSN matching, deduplication, and persistence. Python never parses the model's reply for suggestions — the result files written by subagents are the single source of truth.
- The OMP orchestrator owns subagent spawning, completion verification, and one self-healing respawn of missing results. The subagents own the entire journal-level research: API fetches, browsing, evidence gathering, and suggestion generation.
- Google Sheets API (service-account auth) is used for downloading sheet tabs, uploading suggestions, and loading remote deduplication keys.
- `run_agent.sh` owns the `omp` availability check, the `.venv` preflight, and the swarm timeout wiring.
- The agent only works on journals already present in the gap-report backlog.
- The WhereToPublish repo (`..`) is treated as read-only input during enrichment runs; neither the orchestrator nor the subagents may execute project scripts or touch files outside their mission directory.
- Weak, blocked, or contradictory sources become `unresolved` instead of speculative rows.

## Key Files

- `run_agent.sh`: launcher — `omp` preflight, `.venv` preflight, timeout wiring, runner logging.
- `requirements.txt`: Python dependencies for the repo-local virtual environment.
- `../scripts/sheets_client.py`: shared Google Sheets API module (auth, download, upload helpers), imported by all agent scripts through `enrichment_common.WTP_SCRIPTS_DIR`.
- `../scripts/libraries.py`: canonical `norm_name()` / `format_issn()`, imported by the agent scripts.
- `agent/scripts/enrichment_common.py`: shared paths and schemas — `PROJECT_ROOT`, `DEFAULT_WTP_DIR` (the parent WhereToPublish repo), `WTP_SCRIPTS_DIR`, `SWARM_DIR`, the suggestions CSV headers, allowed values, `UNRESOLVABLE_FIELDS`, and the two prompt paths.
- `agent/scripts/gap_analysis.py`: verifies required external data files in `../data_extraction/` (fails fast when missing), downloads all 10 sheet tabs via the Sheets API, refreshes the WTP pipeline, and writes `agent/output/gap_report.json`.
- `agent/scripts/run_enrichment.py`: the swarm orchestrator — remote key loading, leftover-result harvesting, backlog selection and batching, mission construction (inputs + manifest with verbatim subagent prompts), one OMP run per batch, result collection, validation, persistence, late-result sweep, and the deterministic alt-name pass at the end of the run.
- `agent/scripts/omp_runtime.py`: OMP wrapper — builds `omp -p <prompt> --mode json --no-session --auto-approve --thinking off --append-system-prompt @agent/prompt/system.md --max-time <seconds> [--model <model>]`, enforces the batch timeout with a host-side process-group kill, extracts the final assistant message for the log, and writes prompt/stdout/stderr artifacts per batch.
- `agent/scripts/suggestions_io.py`: schema validation and persistence — canonicalizes `Agent_suggestions.csv` on `(journal, field)`, enforces all validation rules, and writes run state and checkpoints.
- `agent/scripts/issn_alt_name_suggestions.py`: deterministic `Alternative journal name` generator — builds the ISSN → title lookups for Scimago, DOAJ, and OpenAPC directly from `../data_extraction/*.csv.gz` (stdlib csv/gzip) and writes `confidence = 1.00` `alt_name` rows; called automatically at the end of every run and usable standalone.
- `agent/scripts/upload_suggestions.py`: uploads `Agent_suggestions.csv` to the `Agent_suggestions` tab with a Status column (pending/approve/reject); deduplicates the CSV before upload.
- `agent/prompt/system.md`: orchestrator system prompt appended to every OMP run (mission, spawn protocol, boundaries, completion report).
- `agent/prompt/subagent.md`: per-journal research protocol read by every subagent (workflow, API evidence interpretation rules, strict rules, confidence guidance, result JSON schema).
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

Environment overrides:

- `JOURNALMIND_MODEL=<model>` — override the OMP model for one run (passed as `omp --model`)
- `JOURNALMIND_SWARM_TIMEOUT_SECONDS=<seconds>` — override the default per-batch timeout (3600)
- `JOURNALMIND_MAX_SUGGESTIONS=<n>` — override the default suggestion cap (15)
- `JOURNALMIND_PYTHON=<path>` — override the Python interpreter (default: `.venv/bin/python`)
- `JOURNALMIND_OMP_COMMAND=<command>` — override the OMP binary (default: `omp`)

The system never writes directly to the data tabs of the Google Sheet. Persisted suggestions are always staged in `Agent_suggestions` for human review.

## Quickstart

```bash
# Run
./run_agent.sh
```

The launcher:

1. checks that `omp` is in PATH and that `.venv/bin/python` can import `polars`
2. runs `agent/scripts/run_enrichment.py`, which for every batch of up to 32 journals starts one OMP orchestrator that spawns one parallel subagent per journal — the subagents do the full research (including DOAJ/OpenAlex/CrossRef API fetches) and write their result files
3. validates and persists the collected results after each batch, stops when `--max-suggestions` valid suggestions have been written (default 15)
4. finishes with the deterministic ISSN-based `Alternative journal name` pass, appended to the same suggestions CSV

With a full gap backlog, a run processes journals roughly at 32 per batch (all researched in parallel); a 32-journal batch typically completes in about 30–60 minutes depending on the LLM backend, versus hours for one-at-a-time processing.

If you pass `--skip-gap-analysis`, the gap report must already exist at `agent/output/gap_report.json` unless you also override `--gap-report`.

Live monitoring is via terminal output and the runner log at `agent/output/logs/run-*.runner.log`; batch transcripts are in `agent/output/logs/swarm-*.{prompt.txt,stdout.json,stderr.log}` and the per-journal mission artifacts in `agent/output/swarm/*/`.

## Upload Suggestions to Google Sheet

After a run, push `Agent_suggestions.csv` to the spreadsheet for review:

```bash
.venv/bin/python agent/scripts/upload_suggestions.py
```

This replaces the content of the `Agent_suggestions` tab with the current suggestions. Before upload, duplicate `(journal, field)` rows are removed and only the highest-confidence row for each suggested column is kept. Each row has a **Status** column (pending / approve / reject) set to `pending` by default. Team members set the status for each row, then run **WhereToPublish → Apply Reviewed Suggestions** from the Apps Script menu.

Options:

```bash
# Custom input file
.venv/bin/python agent/scripts/upload_suggestions.py --input /path/to/Agent_suggestions.csv

# Custom credentials path
.venv/bin/python agent/scripts/upload_suggestions.py --credentials /path/to/key.json
```

## Run Examples

```bash
# Full default run: gap analysis, then batches of 32 journals until 15 valid suggestions
./run_agent.sh

# Reuse the current gap report and process one named journal (batch of one)
./run_agent.sh --skip-gap-analysis --journal "Human Genomics"

# Small, fast batches: 4 journals per swarm with a 15-minute budget per batch
./run_agent.sh --skip-gap-analysis --swarm-size 4 --swarm-timeout-seconds 900

# Harvest leftover results from previous missions, then work 8 journals max
./run_agent.sh --skip-gap-analysis --journal-limit 8 --max-suggestions 100

# Override the OMP model for one run
JOURNALMIND_MODEL=<model> ./run_agent.sh --max-suggestions 1
```

All extra arguments are forwarded to `agent/scripts/run_enrichment.py`.

## Validation Rules

Only requested fields are eligible for persistence. The validator (in `suggestions_io.py`) rejects rows that are structurally valid JSON but still unsupported, including:

- publisher-as-institution guesses
- institution types without a valid institution value
- `Alternative journal name` suggestions that are not true `alt_name` rows, that normalize to the original journal name, or with confidence < 0.70
- `APC Euros = 0` rows unless the reasoning explicitly states there is no APC (and never for journals whose business model is `Subscription`)
- suggestions with confidence outside 0.55–1, missing source URLs, or missing reasoning
- `e-ISSN`, `p-ISSN`, and `ISSN-L` values that are not formatted as `XXXX-XXXX`

If no suggestion survives validation, the journal is written as `unresolved`.

## Outputs

Default result files:

- `agent/output/Agent_suggestions.csv`
- `agent/output/gap_report.json`
- `agent/output/state/run_state.json`

Run-level logs:

- `agent/output/logs/run-*.runner.log`

Per-batch orchestrator artifacts:

- `agent/output/logs/swarm-*.prompt.txt`
- `agent/output/logs/swarm-*.stdout.json` — full JSONL event stream, including all subagent activity
- `agent/output/logs/swarm-*.stderr.log`

Per-mission artifacts (what the agents actually read and wrote):

- `agent/output/swarm/<batch_id>/mission.json` — orchestrator manifest
- `agent/output/swarm/<batch_id>/inputs/<idx>-<slug>.json` — per-journal research input
- `agent/output/swarm/<batch_id>/results/<idx>-<slug>.json` — per-journal suggestion result

Checkpoint files:

- `agent/output/state/checkpoint_suggestions_*.csv` — written after each completed batch

## Known Runtime Limitations

- `web_search` in OMP requires a configured search provider (see the `webSearchOrder` list in `~/.omp/agent/config.yml`); without one, subagents rely on the public APIs and direct page fetches
- publisher websites (e.g., Scimago, SAGE, Elsevier, Springer Nature) are often protected by Cloudflare WAF and return 403; DOAJ, OpenAlex, and CrossRef APIs are always fetched first by the subagents and bypass this for Business model, APC Euros, and ISSN fields
- an LLM backend with low throughput can stall when the orchestrator emits very long tool calls — the orchestrator is therefore instructed to spawn subagents in calls of 8 journals each; if the batch timeout is hit anyway, the run collects everything already finished, and a later run harvests late results and retries the rest
- journals with only `Institution` / `Institution type` gaps are never sent to the swarm (these fields have never been resolved by the model in practice)
- `--skip-download` skips all 10 tab downloads; use it when the `data_extracted/` CSVs are already current

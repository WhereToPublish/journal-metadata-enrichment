# WhereToPublish Journal Metadata Enrichment

Tooling for generating reviewable journal metadata suggestions for the WhereToPublish project. This project lives **inside** the WhereToPublish repository (`WhereToPublish/journal-metadata-enrichment/`); the parent directory (`..`) is the WhereToPublish repo itself and is treated as read-only input.

The AI layer is runtime-selectable, and all runtimes write exactly the same result files, so validation, persistence, review and upload are shared:

- **`omp` (default CLI runtime)** — [OMP (oh-my-pi)](https://github.com/oh-my-pi/oh-my-pi), installed on the host and plugged to an LLM (configuration in `~/.omp/agent/config.yml`). No Docker container and no local Ollama model are involved.
- **`mistral` (CLI runtime)** — the Mistral Agents SDK (`mistralai-agents`): Python fans out one `agents.Agent` run per journal to the Mistral API, with Pydantic-validated structured output and host-side `fetch_url` / `web_search` tools (see `MistralStudio.md`).
- **`studio` (Mistral Studio workflow)** — the whole pipeline (sheet pull → WTP process scripts → gap report → AI enrichment → suggestions upload) packaged as a Mistral **Workflow** (`studio/`) that you start from the Mistral Studio website (console.mistral.ai → Workflows → Start Workflow) while a self-hosted worker (`./run_workflow.sh`) executes the steps on your machine. It writes **only** the `Agent_suggestions` tab. See [MistralStudio.md](MistralStudio.md).

Journals are researched in batches of up to 32: the OMP runtime spawns one parallel OMP subagent per journal (max 32 concurrent) through OMP's built-in `task` tool, while the Mistral runtime runs one SDK agent per journal, bounded by `JOURNALMIND_MISTRAL_CONCURRENCY` (default 4).

## Current Workflow

The runtime is built around one swarm of parallel agents per batch of journals.

1. `run_agent.sh` is the only entry point. It resolves the runtime (`--runtime` flag or `JOURNALMIND_RUNTIME`, default `omp`) and performs the runtime-specific preflight (OMP in PATH, or `MISTRAL_API_KEY` + `mistralai.agents` importable), plus the `.venv` Python check (can import `polars`), then delegates everything else to `agent/scripts/run_enrichment.py`, forwarding extra arguments plus the swarm batch timeout (`--swarm-timeout-seconds`, default 3600 s).
2. Unless `--skip-gap-analysis` is given, the runner first calls `agent/scripts/gap_analysis.py`, which verifies the external data files in `../data_extraction/`, downloads **all 10 Google Sheets tabs** via the Sheets API, refreshes the WhereToPublish pipeline (`scripts/update_extracted.py` → `scripts/data_process.py`, run with the WTP repo root as working directory), and writes `agent/output/gap_report.json`. ISSN gaps (`e-ISSN`, `p-ISSN`, `ISSN-L`) are only generated for journals that have **none** of the three ISSN fields.
3. `run_enrichment.py` loads remote deduplication keys from the `Agent_suggestions` and `Agent_suggestions_processed` Google Sheets tabs, then selects the journal backlog from the gap report:
   - journals whose only gaps are `Institution` / `Institution type` (fields the LLM has never resolved in practice) are never sent to the swarm — they are recorded in the run state with status `skipped`;
   - the remainder is sorted by gap priority (high → medium → low) and sliced into batches of `--swarm-size` journals (default 32, the OMP concurrency limit; the Mistral runtime researches `JOURNALMIND_MISTRAL_CONCURRENCY` of a batch in parallel);
   - result files left over by earlier swarm missions (late-finishing subagents, interrupted runs) are harvested from `agent/output/swarm/*/results/` before selection, so no completed research is wasted.
4. For each batch the runner writes a **mission directory** `agent/output/swarm/<batch_id>/` containing `mission.json` (manifest with one ready-made `subagent_prompt` per journal), `inputs/<idx>-<slug>.json` (journal name, known metadata, requested gaps, pre-built public API lookup URLs), and an empty `results/` directory — then dispatches the batch to the selected runtime:
   - **OMP**: one non-interactive OMP run per batch. The orchestrator agent reads `mission.json` and spawns one subagent per journal with the `task` tool; each subagent reads its input file plus the research protocol `agent/prompt/subagent.md`, does the **entire journal research itself** — DOAJ, OpenAlex, and CrossRef API fetches (via `curl`), Scimago lookups, publisher pages, web search — and writes its result JSON to its own `results/<idx>-<slug>.json` file, with one self-healing respawn of missing result files.
   - **Mistral**: `agent/scripts/mistral_runtime.py` fans out one `agents.Agent` run per journal (asyncio, concurrency `JOURNALMIND_MISTRAL_CONCURRENCY`). Each agent gets the input JSON inlined in its user message, follows `subagent.md` plus a runtime-adaptation section (no file transport — it has no shell), fetches through the host-side `fetch_url` / `web_search` tools, and returns its result as a **Pydantic-validated structured output** (`output_type=AgentResult`); Python writes the same `results/<idx>-<slug>.json` files. One schema-failure retry per journal; failures and timeouts produce explicit `unresolved` result files.
5. Python owns persistence: the runner reads every result file and validates it against the shared Pydantic `AgentResult` model (`agent_result.parse_result_file`), then applies the business rules of `suggestions_io.sanitize_agent_result` (requested-fields-only, format, evidence and confidence rules), deduplicates against the local CSV and the remote sheet keys, and appends accepted rows to `agent/output/Agent_suggestions.csv`, canonicalized on `(journal, field)` so only the highest-confidence row is kept. Result files that were not written before the batch timeout are swept once at the end of the run (OMP's worker daemon keeps finishing in-flight subagents in the background); journals that still produced nothing stay unprocessed and are retried by a later run.
6. After the AI pass, the runner generates deterministic `Alternative journal name` suggestions from the enriched WhereToPublish CSVs (`agent/scripts/issn_alt_name_suggestions.py`): journals with an empty `Alternative journal name`, at least one ISSN, and a missing-source presence flag are matched by ISSN with source priority **Scimago → DOAJ → OpenAPC**; matches that are a real name change (not a formatting variant) are written as `confidence = 1.00` rows into the same CSV.
7. `agent/scripts/upload_suggestions.py` pushes `Agent_suggestions.csv` to the `Agent_suggestions` review tab of the WhereToPublish Google Sheet (manual step, run after reviewing the output). It shares the review-tab schema and upload helpers of the deterministic disagreement report (`scripts/review_common.py` of the parent repo): each row gets a **Status** column (pending / approve / reject, new rows `pending`) plus the pre-resolved target cell (`data_tab`, `target_row`, `target_col`) computed from the `data_extracted/` tab CSVs, so the Google Apps Script applies approvals with a single verified cell write instead of scanning whole data tabs.

## Mistral Studio Workflow

The same pipeline also runs as a **Mistral Studio workflow** named `wheretopublish-enrichment` (details in [MistralStudio.md](MistralStudio.md)):

```bash
# 1. start the self-hosted worker (registers the workflow in your Studio workspace)
export MISTRAL_API_KEY=...   # Studio workspace API key
./run_workflow.sh            # DEPLOYMENT_NAME defaults to "wheretopublish-enrichment"

# 2. start an execution from the Studio website:
#    console.mistral.ai → Workflows → wheretopublish-enrichment → Start Workflow
#    (the input form is generated from the workflow's Pydantic input model)
#    ...or from the terminal with the same API:
.venv/bin/python trigger_workflow.py --journal-limit 8 --max-suggestions 15
.venv/bin/python trigger_workflow.py --journal "Médecine et Santé Tropicales" --wait
.venv/bin/python trigger_workflow.py --status --execution-id <id>
```

Each execution runs six durable activities (in `studio/activities.py`): preflight → pull the 10
Google Sheet data tabs (**read-only**) → run the WTP process scripts (`update_extracted.py`,
`data_process.py` — local files only) → build the gap report → AI enrichment (one Mistral agent
per journal, smallest model `ministral-3b-latest` by default) → upload `Agent_suggestions.csv`
to the **`Agent_suggestions` tab only** (hard-guarded by `studio/config.ALLOWED_WRITE_TABS`).
Executions, their per-step results and traces are visible in the Studio Executions tab; the
worker log is the terminal running `run_workflow.sh`.

Google credentials are resolved from the `GOOGLE_SERVICE_ACCOUNT_KEY` environment variable (path to JSON file), or fallback to `~/.config/wheretopublish/google_service_account.json`.

NOTE: Mistral secret names must use lowercase letters, numbers, hyphens, and underscores only. Without the env var, the worker falls back to
`GOOGLE_SERVICE_ACCOUNT_KEY` / `~/.config/wheretopublish/google_service_account.json`.

## Review Workflow

The AI suggestions and the pipeline's deterministic disagreement report
(`WhereToPublish/logs/disagreements.csv` → `Disagreements` tab, regenerated on every
pipeline run) share the same review interface, upload code, and Apps Script (the
schema lives in `../scripts/review_common.py`, the Apps Script in
`../apps_script/ApplyReviewedChanges.gs`); they only live on different tabs. After upload, team members open a review tab and set
each row's **Status** to:

- `approve` — apply the suggestion to the data tab
- `reject` — discard the suggestion (archived for analysis)
- `pending` — not yet reviewed (default after upload)

Running **WhereToPublish → Apply Reviewed Changes** from the Apps Script menu (also processes the `Disagreements` tab):

- `approve` rows: the script verifies the recorded target cell (`data_tab`/`target_row`/`target_col`: column header, journal of the row, and current cell value — or the already-applied suggested value) and writes `suggested_value` into that single cell; then the row is archived in `<tab>_processed` and deleted from the review tab. Failed verifications (moved rows, changed values, missing targets) leave the row in place and are reported in the summary alert.
- `reject` rows: the row is archived in `<tab>_processed` and deleted without any data change.
- `pending` rows are left untouched.

Each processed row is archived and deleted in one batch per run; rows whose verification failed stay in the review tab. If the script is interrupted, it can be safely re-run: already-applied rows pass the idempotency check and everything else re-verifies from scratch.

The `<tab>_processed` archives (`Agent_suggestions_processed`, `Disagreements_processed`) contain all reviewed rows (approve + reject) and can be used to analyze agent performance and disagreement quality over time (see `NEXT_STEPS.md`). Note that the `Disagreements` tab is regenerated from scratch by every pipeline run — statuses set there do not persist across pipeline runs.

## Architecture

```
Host
├── Runtime A: OMP (oh-my-pi)          — installed on the host, plugged to an LLM (~/.omp/agent/config.yml)
│     ├── one `omp -p <orchestrator prompt>` run per batch (non-interactive, JSONL output on stdout)
│     │     └── OMP `task` tool: spawns up to 32 parallel subagents per batch
│     │           └── each subagent: reads its journal input + agent/prompt/subagent.md,
│     │               fetches DOAJ / OpenAlex / CrossRef APIs + web pages itself,
│     │               writes its suggestion JSON to the mission results/ directory
├── Runtime B: Mistral Agents SDK (.venv) — one agents.Agent run per journal against api.mistral.ai
│     ├── Pydantic-validated structured output (output_type=AgentResult)
│     ├── host tools run by Python: fetch_url (urllib, HTML→text, size-capped), web_search (DuckDuckGo)
│     └── Python converts every answer to the same mission results/ files
├── Runtime C: Mistral Studio workflow (studio/) — hybrid mode: Mistral Studio hosts the
│     │   orchestrator (state/history/UI); a self-hosted worker (run_workflow.sh → studio.worker)
│     │   executes the activities on this host and auto-registers the workflow
│     └── wheretopublish-enrichment: preflight → pull sheet (read-only) → WTP process scripts →
│         gap report → AI enrichment (runtime B agents) → upload Agent_suggestions tab (only write)
├── Python .venv                       — orchestration only (mission setup, validation, CSV I/O)
└── WhereToPublish repo (parent dir `..`)  — read-only input
      ├── scripts/sheets_client.py     — canonical Google Sheets API module (imported via sys.path)
      ├── scripts/libraries.py         — canonical norm_name() / format_issn()
      ├── data_extraction/*.csv.gz     — external data sources (Scimago, DOAJ, OpenAPC, PCI, Dataverse)
      ├── data_extracted/*.csv         — raw Google Sheets tabs (downloaded each run)
      └── data/*.csv                   — enriched output of the WTP pipeline
```

In the OMP runtime, `omp` runs directly on the host with the project root as working directory; the orchestrator's operating instructions come from `agent/prompt/system.md` (appended to the OMP system prompt with `--append-system-prompt`) and each subagent reads the per-journal research protocol from `agent/prompt/subagent.md`. In the Mistral runtime the instructions are `subagent.md` plus a short runtime-adaptation section built by `mistral_runtime.py` (the OMP orchestrator file is not used).

## Runtime Boundaries

- Python owns orchestration, gap-report loading, backlog selection, mission construction, result validation, deterministic ISSN matching, deduplication, and persistence. The result files written under the mission directory are the single source of truth for both runtimes, and both are validated through the same Pydantic `AgentResult` contract on collection (`agent_result.py`).
- OMP runtime: the orchestrator owns subagent spawning, completion verification, and one self-healing respawn of missing results. The subagents own the entire journal-level research: API fetches, browsing, evidence gathering, and suggestion generation.
- Mistral runtime: Python owns the fan-out (no orchestrator agent at all); each per-journal agent does the research through host-side tools and returns a Pydantic-validated `AgentResult`.
- Google Sheets API (service-account auth) is used for downloading sheet tabs, uploading suggestions, and loading remote deduplication keys — always host-side, never inside an agent.
- `run_agent.sh` owns the runtime-specific preflight (`omp` vs `MISTRAL_API_KEY` + Agents SDK import), the `.venv` preflight, and the swarm timeout wiring.
- The agent only works on journals already present in the gap-report backlog.
- The WhereToPublish repo (`..`) is treated as read-only input during enrichment runs; neither the orchestrator, the subagents nor the Mistral agents may execute project scripts or touch files inside `agent/output/` other than their own result files (Mistral agents have no file access at all).
- Weak, blocked, or contradictory sources become `unresolved` instead of speculative rows.

## Key Files

- `run_agent.sh`: launcher — runtime resolution (`--runtime` / `JOURNALMIND_RUNTIME`), runtime-specific preflight (`omp` in PATH, or `MISTRAL_API_KEY` + Agents SDK import), `.venv` preflight, timeout wiring, runner logging.
- `run_workflow.sh`: starts the self-hosted Mistral Studio worker (`python -m studio.worker`); preflight checks `MISTRAL_API_KEY`, the `.venv` (polars + `mistralai.workflows`), and defaults `DEPLOYMENT_NAME` to the workflow name. Keep it running; executions started from the Studio website execute on it.
- `trigger_workflow.py`: start/poll executions of `wheretopublish-enrichment` via the Workflows API — the same call the Studio "Start Workflow" button makes.
- `studio/`: the Mistral Studio workflow package — `config.py` (paths, credential/Studio-Secret resolution, input model, write-tab guard), `activities.py` (the six durable activities reusing `agent/scripts/`), `workflow.py` (the `@workflow.define` class), `worker.py` (worker entrypoint).
- `requirements.txt`: Python dependencies for the repo-local virtual environment (incl. `mistralai-workflows` for the Studio worker).
- `../scripts/sheets_client.py`: shared Google Sheets API module (auth, download, upload helpers), imported by all agent scripts through `enrichment_common.WTP_SCRIPTS_DIR`.
- `../scripts/libraries.py`: canonical `norm_name()` / `format_issn()`, imported by the agent scripts.
- `agent/scripts/enrichment_common.py`: shared paths and schemas — `PROJECT_ROOT`, `DEFAULT_WTP_DIR` (the parent WhereToPublish repo), `WTP_SCRIPTS_DIR`, `SWARM_DIR`, the suggestions CSV headers, allowed values, `UNRESOLVABLE_FIELDS`, and the two prompt paths.
- `agent/scripts/gap_analysis.py`: verifies required external data files in `../data_extraction/` (fails fast when missing), downloads all 10 sheet tabs via the Sheets API, refreshes the WTP pipeline, and writes `agent/output/gap_report.json`.
- `agent/scripts/run_enrichment.py`: the batch orchestrator — remote key loading, leftover-result harvesting, backlog selection and batching, mission construction (inputs + manifest with verbatim subagent prompts), runtime dispatch (`--runtime omp`: one OMP run per batch; `--runtime mistral`: one Agents SDK agent per journal), result collection, validation, persistence, late-result sweep, and the deterministic alt-name pass at the end of the run. The core is `run_enrichment_core_async(cfg)` (async, also driven by the Studio workflow activities); `run_enrichment_core(cfg)` is the sync wrapper used by the CLI, and `run_gap_analysis_step()` runs pull-sheets + process-scripts + gap report as reusable functions.
- `agent/scripts/omp_runtime.py`: OMP wrapper — builds `omp -p <prompt> --mode json --no-session --auto-approve --thinking off --append-system-prompt @agent/prompt/system.md --max-time <seconds> [--model <model>]`, enforces the batch timeout with a host-side process-group kill, extracts the final assistant message for the log, and writes prompt/stdout/stderr artifacts per batch.
- `agent/scripts/mistral_runtime.py`: Mistral Agents SDK runtime — the runtime-adaptation instructions (`subagent.md` + transport section), the host-side `fetch_url` (urllib, HTML→text, size caps) and `web_search` (DuckDuckGo lite) tools, and `MistralSwarmRunner` fanning out one SDK agent run per journal (`run_async`, awaited by the Studio workflow; sync `run()` wrapper for the CLI) with a per-journal timeout, an overall batch budget, one structured-output retry, and explicit `unresolved` result files on failure.
- `agent/scripts/agent_result.py`: the shared **Pydantic result contract** — `Suggestion` / `AgentResult` models (with the lenient coercions for untidy hand-written results), `unresolved_result()`, and `parse_result_file()` / `write_result_file()`. The Mistral runtime returns its answers as `output_type=AgentResult` structured output, and every result file (OMP-subagent-written or Mistral-runtime-written) is validated through the same models when collected; a payload without a journal name is invalid, and one broken suggestion never poisons the whole result.
- `agent/scripts/suggestions_io.py`: per-field validation and persistence — takes the Pydantic-validated `AgentResult` from either runtime, enforces all business rules (evidence wording, ISSN format, alt-name strictness, dedup keys), canonicalizes `Agent_suggestions.csv` on `(journal, field)`, and writes run state and checkpoints.
- `agent/scripts/issn_alt_name_suggestions.py`: deterministic `Alternative journal name` generator — builds the ISSN → title lookups for Scimago, DOAJ, and OpenAPC directly from `../data_extraction/*.csv.gz` (stdlib csv/gzip) and writes `confidence = 1.00` `alt_name` rows; called automatically at the end of every run and usable standalone.
- `agent/scripts/upload_suggestions.py`: uploads `Agent_suggestions.csv` to the `Agent_suggestions` review tab through the shared `../scripts/review_common.py` (Status column pending/approve/reject, resolved data_tab/target_row/target_col, Status dropdown, report formatting); deduplicates the CSV before upload.
- `agent/prompt/system.md`: orchestrator system prompt appended to every OMP run (mission, spawn protocol, boundaries, completion report).
- `agent/prompt/subagent.md`: per-journal research protocol read by every subagent (workflow, API evidence interpretation rules, strict rules, confidence guidance, result JSON schema).
- `../apps_script/GOOGLE_APP_SCRIPT.md`: Apps Script setup for the human review workflow shared by the `Agent_suggestions` and `Disagreements` tabs.
- `../apps_script/ApplyReviewedChanges.gs`: the unified Google Apps Script of the parent WhereToPublish repo processing both review tabs; each approval is a verified single-cell write to the recorded target cell (journal, column header, and current value are re-checked, so stale targets fail safely and re-runs are idempotent).
- `MistralStudio.md`: the Mistral integration — the Agents SDK runtime, the implemented Studio workflow (`wheretopublish-enrichment`, started from the Studio website), how to upload the config .json via Secrets Manager, trade-offs vs OMP, and future possibilities.
- `NEXT_STEPS.md`: roadmap for using the suggestion history to analyze and improve agent performance.

## Requirements

- macOS (or Linux) with **OMP (oh-my-pi)** installed (`omp` in PATH) and already authenticated to an LLM (run `omp` once interactively see `~/.omp/agent/config.yml`) — only needed for the default `omp` runtime
- for the `mistral` runtime and the Studio workflow: a **`MISTRAL_API_KEY`** environment variable holding a [Mistral Studio](https://console.mistral.ai) API key; no `omp` install needed
- for the Studio workflow worker: `DEPLOYMENT_NAME` is optional (defaults to `wheretopublish-enrichment`); Google credentials come from `GOOGLE_SERVICE_ACCOUNT_KEY` or the default key path
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

- `JOURNALMIND_RUNTIME={omp,mistral}` — agent runtime when `--runtime` is not passed (default: `omp`)
- `JOURNALMIND_MODEL=<model>` — override the OMP model for one run (passed as `omp --model`)
- `JOURNALMIND_MISTRAL_MODEL=<model>` — Mistral model (default: `ministral-3b-latest`, the cheapest)
- `JOURNALMIND_MISTRAL_CONCURRENCY=<n>` — parallel journal agents per batch (default: 4)
- `JOURNALMIND_MISTRAL_TIMEOUT_SECONDS=<s>` — per-journal research budget (default: 600)
- `JOURNALMIND_MISTRAL_MAX_ITERATIONS=<n>` — agent-loop iteration cap (default: 16; each iteration is an API call)
- `JOURNALMIND_SWARM_TIMEOUT_SECONDS=<seconds>` — override the default per-batch timeout (3600; also the Mistral batch budget)
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

1. resolves the runtime (`--runtime` / `JOURNALMIND_RUNTIME`, default `omp`) and runs its preflight — OMP in PATH, or `MISTRAL_API_KEY` set plus a working `mistralai.agents` import — together with the `.venv` (`polars`) check
2. runs `agent/scripts/run_enrichment.py`, which for every batch of up to 32 journals dispatches the research: one OMP orchestrator that spawns one parallel subagent per journal (OMP runtime), or concurrency-bounded per-journal Agents SDK runs (Mistral runtime); the agents do the full research (including DOAJ/OpenAlex/CrossRef API fetches) and produce one result file per journal
3. validates and persists the collected results after each batch, stops when `--max-suggestions` valid suggestions have been written (default 15)
4. finishes with the deterministic ISSN-based `Alternative journal name` pass, appended to the same suggestions CSV

With a full gap backlog, a run processes journals roughly at 32 per batch; a 32-journal OMP batch typically completes in about 30–60 minutes depending on the LLM backend, versus hours for one-at-a-time processing. The Mistral runtime processes `JOURNALMIND_MISTRAL_CONCURRENCY` journals in parallel, each taking roughly 25–60 s with the smallest model.

If you pass `--skip-gap-analysis`, the gap report must already exist at `agent/output/gap_report.json` unless you also override `--gap-report`.

Live monitoring is via terminal output and the runner log at `agent/output/logs/run-*.runner.log`; batch transcripts are in `agent/output/logs/swarm-*.{prompt.txt,stdout.json,stderr.log}` (OMP) or `agent/output/logs/swarm-*.mistral.log` plus the Studio console's run traces (Mistral), and the per-journal mission artifacts in `agent/output/swarm/*/`.

## Upload Suggestions to Google Sheet

After a run, push `Agent_suggestions.csv` to the spreadsheet for review:

```bash
.venv/bin/python agent/scripts/upload_suggestions.py
```

This replaces the content of the `Agent_suggestions` tab with the current suggestions. Before upload, duplicate `(journal, field)` rows are removed and only the highest-confidence row for each suggested column is kept. Uploaded rows are sorted for review: high → medium → low priority, then confidence (descending), with all suggestions of a journal kept as a contiguous group ordered by its best suggestion — so journals are reviewed one at a time, best suggestions first. Each row has a **Status** column (pending / approve / reject) set to `pending` by default, plus the resolved `data_tab`/`target_row`/`target_col` of the data cell to change (journals that cannot be resolved — missing or present in several tabs — get empty targets and are handled manually). Team members set the status for each row, then run **WhereToPublish → Apply Reviewed Changes** from the Apps Script menu.

Options:

```bash
# Custom input file
.venv/bin/python agent/scripts/upload_suggestions.py --input /path/to/Agent_suggestions.csv

# Custom credentials path
.venv/bin/python agent/scripts/upload_suggestions.py --credentials /path/to/key.json
```

## Run Examples

```bash
# Full default run (OMP runtime): gap analysis, then batches of 32 journals until 15 valid suggestions
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

Mistral runtime examples:

```bash
# Smoke test: one journal with the Mistral Agents SDK (needs MISTRAL_API_KEY)
./run_agent.sh --runtime mistral --skip-gap-analysis --journal "Human Genomics" --max-suggestions 3

# Full run on Mistral: gap analysis, then batches of up to 32 journals, 4 researched in parallel
./run_agent.sh --runtime mistral --max-suggestions 15

# Cheaper-parallelism / bigger-model tuning
JOURNALMIND_MISTRAL_CONCURRENCY=8 JOURNALMIND_MISTRAL_MODEL=mistral-small-latest \
  ./run_agent.sh --runtime mistral --skip-gap-analysis --journal-limit 8
```

All extra arguments are forwarded to `agent/scripts/run_enrichment.py`.

Mistral Studio workflow examples (worker running via `./run_workflow.sh`):

```bash
# Smoke test from the terminal: one journal, smallest model, at most 3 suggestions
.venv/bin/python trigger_workflow.py --journal "Médecine et Santé Tropicales" --max-suggestions 3

# Full run, started from the Studio website instead:
#   console.mistral.ai → Workflows → wheretopublish-enrichment → Start Workflow
#   with input {"journal_limit": 8, "max_suggestions": 15, "model": "ministral-3b-latest"}

# Poll an execution
.venv/bin/python trigger_workflow.py --status --execution-id <id>
```

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

Per-batch runtime artifacts (OMP runtime):

- `agent/output/logs/swarm-*.prompt.txt`
- `agent/output/logs/swarm-*.stdout.json` — full JSONL event stream, including all subagent activity
- `agent/output/logs/swarm-*.stderr.log`

Per-batch runtime artifacts (Mistral runtime):

- `agent/output/logs/swarm-*.mistral.log` — one status line per journal (status, suggestion count, duration, notes); full traces of the API sessions are visible in the Mistral Studio console

Per-mission artifacts (what the agents actually read and wrote):

- `agent/output/swarm/<batch_id>/mission.json` — orchestrator manifest
- `agent/output/swarm/<batch_id>/inputs/<idx>-<slug>.json` — per-journal research input
- `agent/output/swarm/<batch_id>/results/<idx>-<slug>.json` — per-journal suggestion result

Checkpoint files:

- `agent/output/state/checkpoint_suggestions_*.csv` — written after each completed batch

## Known Runtime Limitations

Common to both runtimes:

- publisher websites (e.g., Scimago, SAGE, Elsevier, Springer Nature) are often protected by Cloudflare WAF and return 403 — from the host as well as from Mistral; DOAJ, OpenAlex, and CrossRef APIs are always fetched first and bypass this for Business model, APC Euros, and ISSN fields
- journals with only `Institution` / `Institution type` gaps are never sent to the agents (these fields have never been resolved by the models in practice)
- `--skip-download` skips all 10 tab downloads; use it when the `data_extracted/` CSVs are already current

OMP runtime:

- `web_search` in OMP requires a configured search provider (see the `webSearchOrder` list in `~/.omp/agent/config.yml`); without one, subagents rely on the public APIs and direct page fetches
- an LLM backend with low throughput can stall when the orchestrator emits very long tool calls — the orchestrator is therefore instructed to spawn subagents in calls of 8 journals each; if the batch timeout is hit anyway, the run collects everything already finished, and a later run harvests late results and retries the rest

Mistral runtime:

- research cost is billed per token to the Studio API credits; the default `ministral-3b-latest` keeps a journal research in the sub-cent range, but its evidence judgement is weaker than a large model's — raise `JOURNALMIND_MISTRAL_MODEL` for production runs and set a budget alert in the Studio console before large backlog runs
- `web_search` is a best-effort DuckDuckGo scrape (free, no API key); if it breaks or gets rate-limited, agents fall back to the public APIs and `fetch_url`, and the affected gaps become `unresolved`
- concurrency is API-bounded (default 4 parallel journals instead of OMP's 32); throughput per batch is lower

Mistral Studio workflow:

- executions run only while a worker (`./run_workflow.sh`) is connected to the workspace; the worker must run on a host that has the WhereToPublish repo (`..`) and the Google credentials
- the workflow writes only the `Agent_suggestions` tab (guarded by `studio/config.ALLOWED_WRITE_TABS`); all sheet reads are read-only
- research cost is billed per token to the Studio API credits — keep `model` at `ministral-3b-latest` for cheap runs and set a budget alert in the Studio console before large backlogs
- activity timeouts are generous static upper bounds; the real budgets are the workflow input's `batch_timeout_seconds` / `per_journal_timeout_seconds`
- the Mistral Agents SDK has no server-side `web_search` builtin for programmatic agents; search is a host tool (see `MistralStudio.md` for the Server-native path)


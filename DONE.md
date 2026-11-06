# DONE.md — Current Project State

This file describes the current implementation state only. It is intended as a fast orientation document for LLMs working in this repository.

The project lives inside the WhereToPublish repository: `WhereToPublish/journal-metadata-enrichment/`.
The parent directory (`..`, i.e. the WhereToPublish repo root) is the data source and is treated as
read-only input. The AI layer is **runtime-selectable** with `--runtime` (env `JOURNALMIND_RUNTIME`):

- **`omp` (default)** — OMP (oh-my-pi), installed on the host and plugged to an LLM
  (`~/.omp/agent/config.yml`). Docker and Ollama are not used.
- **`mistral`** — the Mistral Agents SDK (`mistralai-agents`): Python fans out one SDK agent run
  per journal to the Mistral API, with Pydantic-validated structured output and host-side
  `fetch_url` / `web_search` tools (details in `MistralStudio.md`).

Both runtimes write the same per-journal result files, so validation, persistence, review and
upload are shared and identical.

## Runtime Path

Each runtime researches a batch of journals (up to 32 per batch):

- **OMP**: one non-interactive orchestrator process per batch, with one parallel subagent
  per journal spawned through OMP's built-in `task` tool.
- **Mistral**: `agent/scripts/mistral_runtime.py` fan-out — one `agents.Agent` run per journal
  (asyncio TaskGroup bounded by a semaphore), no orchestrator agent at all.

```text
run_agent.sh
  -> resolves the runtime (--runtime flag or JOURNALMIND_RUNTIME, default omp)
     and runs the runtime-specific preflight:
       omp:      'omp' in PATH
       mistral:  MISTRAL_API_KEY set + .venv/bin/python imports mistralai.agents
     plus, for both: .venv/bin/python can import polars
  -> checks .venv, validates JOURNALMIND_SWARM_TIMEOUT_SECONDS (default 3600)
  -> runs agent/scripts/run_enrichment.py, forwarding all extra arguments
     plus --swarm-timeout-seconds (per-batch budget for both runtimes)
  -> run_enrichment.py (--runtime {omp,mistral}, default omp)
    -> gap analysis (agent/scripts/gap_analysis.py), unless --skip-gap-analysis
       (verifies ../data_extraction/ files, downloads all 10 tabs via the Sheets API,
        runs the WTP pipeline with cwd=.., writes agent/output/gap_report.json)
    -> load_suggestion_keys_from_tabs() — remote deduplication keys
    -> harvest_previous_results() — persists leftover result files from earlier missions
    -> backlog selection (priority sort; Institution-only journals never sent to the agents)
    -> per batch of up to 32 journals:
       write_mission() -> agent/output/swarm/<batch_id>/
         mission.json (manifest incl. verbatim subagent_prompt per journal)
         inputs/<idx>-<slug>.json (journal, known_metadata, requested_gaps, api_lookup_urls, result_file)
         results/ (empty; per-journal result files land here)
       dispatch (omp):
         agent/scripts/omp_runtime.py
           -> omp -p <orchestrator prompt> --mode json --no-session --auto-approve --thinking off
              --append-system-prompt @agent/prompt/system.md --max-time <seconds> [--model <model>]
           -> the orchestrator spawns one subagent per journal (task tool, calls of 8 journals),
              each subagent reads inputs/<idx>.json + agent/prompt/subagent.md,
              does the full research itself (DOAJ/OpenAlex/CrossRef APIs via curl, web pages),
              and writes results/<idx>-<slug>.json; missing results get one respawn
       dispatch (mistral):
         agent/scripts/mistral_runtime.py -> MistralSwarmRunner.run(session_id, mission_dir, journals)
           -> one agents.Agent run per journal (asyncio, semaphore=JOURNALMIND_MISTRAL_CONCURRENCY,
              default 4), each with:
              instructions = subagent.md + runtime adaptation (input inline, no files, no shell),
              tools=[fetch_url, web_search] run host-side by this Python process,
              output_type=AgentResult (Pydantic structured output), max_iterations=16
           -> Completed(output=AgentResult) -> results/<idx>-<slug>.json written by Python
           -> InvalidOutput / no answer -> one retry turn in a fresh run, then unresolved
           -> Failed / Busy / timeout -> explicit unresolved result file
       collect results -> agent_result.parse_result_file() (Pydantic AgentResult, both runtimes)
                      -> agent/scripts/suggestions_io.py sanitize_agent_result()
                      -> append accepted rows to agent/output/Agent_suggestions.csv (canonical (journal, field))
    -> late-result sweep for journals whose result file was still missing at collection time
    -> deterministic Alternative journal name pass
       (agent/scripts/issn_alt_name_suggestions.py; Scimago -> DOAJ -> OpenAPC by ISSN,
        confidence 1.00 rows appended to the same CSV)
    -> agent/output/*

agent/scripts/upload_suggestions.py  (run manually after enrichment)
  -> agent/scripts/suggestions_io.py — collapses duplicate (journal, field) rows, keeps highest confidence
  -> ../scripts/review_common.py — shared review-tab schema + upload (see below): resolves each
     journal's target cell (data_tab/target_row/target_col) from ../data_extracted/, prepends the
     Status column (new rows 'pending'), clears + rewrites the Agent_suggestions tab, applies the
     report styling and the pending/approve/reject dropdown on the Status column
```

Both review workflows of the spreadsheet (AI suggestions here, disagreements of the WTP pipeline)
share the same schema (../scripts/review_common.py), the same upload helper, and the same Apps
Script (../apps_script/ApplyReviewedChanges.gs in the parent repo): they only live on different tabs.
- `Agent_suggestions` tab (AI suggestions, uploaded manually by upload_suggestions.py)
- `Disagreements` tab (deterministic conflicts, regenerated + uploaded by every WTP pipeline run
  of ../scripts/update_extracted.py + ../scripts/upload_sheets.py, all rows back to `pending`)

Key runtime properties (common):

- fully automated startup from the terminal; the launcher is the single entry point
- the batch size is `--swarm-size` (1–32); journals are sorted by their highest-priority gap
  (high → medium → low); the run stops when the suggestion cap (`--max-suggestions`, default 15)
  is reached, when `--journal-limit` journals have been dispatched, or when the backlog is exhausted
- per-journal inputs and results are plain JSON files under `agent/output/swarm/<batch_id>/` —
  Python never scrapes suggestions out of free-text agent replies; the result files are the source of truth, and both runtimes' files
  are validated through the same shared Pydantic `AgentResult` model (`agent_result.py`) when collected
- journals whose only gaps are `Institution` / `Institution type` are never sent to the agents
  (status `skipped` in the run state)
- leftover results from earlier missions are harvested at the start of every run, so
  late-finishing agents and interrupted runs are never wasted; journals that still produced
  nothing are retried by a later run
- unresolved is the normal fallback when evidence is weak or blocked
- deterministic ISSN-based `Alternative journal name` suggestions are generated locally from Scimago,
  DOAJ, and OpenAPC without calling the LLM, at the end of every enrichment run
- `Agent_suggestions.csv` is canonicalized on (journal, field) so only the highest-confidence
  suggestion is kept for each suggested column
- before each run, remote suggestion keys are loaded from `Agent_suggestions` and
  `Agent_suggestions_processed`; if loading fails the run degrades gracefully to
  local-CSV deduplication only
- Google Sheets API (service-account auth) is used for all spreadsheet I/O: downloading tabs,
  loading remote deduplication keys, uploading suggestions — always host-side, never inside an agent

Key runtime properties (OMP only):

- the orchestrator's instructions come from `agent/prompt/system.md` (appended to the OMP system
  prompt); each subagent reads `agent/prompt/subagent.md` from disk
- subagents carry their own API fetches (DOAJ/OpenAlex/CrossRef with `curl`), web browsing,
  evidence interpretation, and suggestion generation
- the batch timeout (default 3600 s) is enforced via OMP `--max-time` plus a host-side
  process-group kill; OMP's worker daemon keeps finishing in-flight subagents in the background,
  which the late-result sweep harvests
- the OMP model defaults to the one configured in `~/.omp/agent/config.yml`; override per run
  with `JOURNALMIND_MODEL` (passed as `omp --model`) or `--model`

Key runtime properties (Mistral only):

- each journal is one `agents.Agent.run()` call against `api.mistral.ai`; batch orchestration is
  plain Python asyncio — no orchestrator agent, no orchestrator tokens
- the answer contract is the shared Pydantic model (`AgentResult` from `agent_result.py`)
  passed as `output_type`; the API returns a validated object or an `InvalidOutput` value —
  and the same model validates OMP result files on collection, so no runtime parses loose JSON
- one structured-output reminder retry per journal; failures and timeouts produce explicit
  `unresolved` result files (nothing keeps running after the process exits)
- the agents have no shell and no file access: all fetching goes through the host tools
  `fetch_url` (urllib with browser UA, 20 s timeout, HTML stripped to text, 60k-char cap) and
  `web_search` (free DuckDuckGo "lite" scrape returning title/url/snippet JSON)
- per-journal budget: `JOURNALMIND_MISTRAL_TIMEOUT_SECONDS` (default 600), overall batch
  budget: `--swarm-timeout-seconds`; model: `JOURNALMIND_MISTRAL_MODEL` (default
  `ministral-3b-latest` — the cheapest — one journal takes ~25–60 s and sub-cent credits;
  raise the model for better evidence judgment in production runs)
- per-batch log: `agent/output/logs/swarm-*.mistral.log` (one status line per journal); full
  run traces are visible in the Mistral Studio console

## Root Files

- `README.md`: human-facing overview of the current runtime
- `DONE.md`: this file
- `../apps_script/GOOGLE_APP_SCRIPT.md`: Apps Script setup for the human review workflow (documented in the parent WhereToPublish repo)
- `NEXT_STEPS.md`: roadmap for using suggestion history to improve the agent
- `MistralStudio.md`: the Mistral Agents SDK runtime — implemented architecture, trade-offs vs the OMP runtime, and the remaining steps toward a Studio-native orchestrator (deployed agent, workflow, traces/evaluations, review app)
- `run_agent.sh`: launcher used for real runs (both runtimes)
- `requirements.txt`: Python dependencies for `.venv`, including `mistralai-agents` for the Mistral runtime
- `clean.sh`: removes `agent/output/` and hard-resets the parent WhereToPublish repo (main, then data branch)
- `.gitignore`: ignores credentials and generated output under `agent/output/`
- `Docs/`: task descriptions (TODO_9_swarms.md is the task that produced the swarm runtime; TODO_10_MistralStudio_feasability.md describes the separate Mistral Studio task)

## Launcher

`run_agent.sh` is the entry point:

- changes into the project root (the `journal-metadata-enrichment/` folder) before launching anything
- resolves the runtime from a CLI `--runtime` flag (taking precedence) or `JOURNALMIND_RUNTIME` (default `omp`)
- uses `.venv/bin/python` by default, with `JOURNALMIND_PYTHON` as the override
- runs the runtime-specific preflight:
  - `omp`: `omp` available in PATH
  - `mistral`: `MISTRAL_API_KEY` set and `.venv/bin/python` can `from mistralai import agents`
- checks that `.venv/bin/python` can import `polars`
- validates `JOURNALMIND_SWARM_TIMEOUT_SECONDS` as a positive integer (default: 3600)
- launches `agent/scripts/run_enrichment.py` forwarding all extra arguments, always appending `--swarm-timeout-seconds <value>`
- appends `--max-suggestions <n>` when `JOURNALMIND_MAX_SUGGESTIONS` is set
- tees all runner output into `agent/output/logs/run-<id>.runner.log`

Current runner defaults:

- runtime: `omp` (override per run with `--runtime mistral` or `JOURNALMIND_RUNTIME`)
- batch size: `--swarm-size 32` (both runtimes batch at most 32 journals; range 1–32)
- batch timeout: `--swarm-timeout-seconds 3600` (OMP swarm run / Mistral batch budget)
- suggestion cap: `--max-suggestions 15`, overridable per run
- Mistral runtime: model `ministral-3b-latest`, concurrency 4, per-journal budget 600 s,
  max 16 agent-loop iterations (each is an API call) — all via `JOURNALMIND_MISTRAL_*` env vars
- journals are sorted by their highest-priority gap (high → medium → low); the run stops when the suggestion cap is reached, when `--journal-limit` journals have been dispatched, or when the backlog is exhausted

## Python Modules

### agent/scripts/enrichment_common.py

Shared paths and schemas.

- defines `PROJECT_ROOT`, `AGENT_DIR`, and the output/log/state paths, plus `SWARM_DIR` (`agent/output/swarm/`)
- defines `DEFAULT_WTP_DIR` as the **parent directory** (the WhereToPublish repo root) and `WTP_SCRIPTS_DIR = ../scripts`
- defines `SYSTEM_PROMPT_PATH = agent/prompt/system.md` (OMP orchestrator; appended to the OMP system prompt) and `SUBAGENT_PROTOCOL_PATH = agent/prompt/subagent.md` (used verbatim by OMP subagents and as the base of the Mistral agent instructions)
- defines `UNRESOLVABLE_FIELDS = {"Institution", "Institution type"}` — journals with only these gaps never reach the agents
- defines the shared `SwarmRunResult` dataclass returned by both runtimes per batch (`session_id`, `stdout`, `stderr`, `final_message`, `timed_out`)
- defines the suggestions CSV headers and allowed values (`ALLOWED_FIELDS`, priorities, suggestion types, business models)
- provides `slugify()` and `is_empty()`

All agent scripts import the canonical `sheets_client` / `libraries` from `../scripts` via `WTP_SCRIPTS_DIR` on `sys.path` — there is no duplicate copy inside this project.

### ../scripts/sheets_client.py (WhereToPublish repo)

Shared Google Sheets API module — canonical implementation used by both the WTP pipeline scripts and all agent scripts.

- defines `SPREADSHEET_ID`, `VARIABLES_TAB_NAME`, `DEFAULT_CREDENTIALS_PATH` (from `GOOGLE_SERVICE_ACCOUNT_KEY` or `~/.config/wheretopublish/google_service_account.json`)
- defines `SHEET_TAB_NAMES` mapping field slugs to Google Sheets tab names
- provides `get_sheets_service()`, `download_tab_as_csv()`, `read_csv_as_rows()`, `upload_tab_from_csv()`, `write_rows()`

### agent/scripts/gap_analysis.py

Pipeline refresh and gap discovery across all Google Sheets tabs.

- verifies that all 5 required external data files are present in `../data_extraction/`; exits with a clear error listing missing files
- downloads **all 10 Google Sheets tabs** via the Sheets API and writes each to `../data_extracted/<slug>.csv`
- runs the WTP pipeline with the WhereToPublish repo root as working directory (`scripts/update_extracted.py` → `scripts/data_process.py`) unless skipped
- builds a unified gap report across all tabs (each journal entry has `tab`, `e_issn`, `p_issn`, `issn_l`; journals are deduplicated by name, first-seen tab wins)
- generates gaps for `Alternative journal name` (priority weight 12) when a journal has no Scimago data, and low-priority `fill` gaps for the three ISSN fields **only when the journal has none of them**
- writes `agent/output/gap_report.json`
- options: `--skip-download` (reuse `data_extracted/` CSVs), `--skip-pipeline`, `--output`, `--credentials`

### agent/scripts/run_enrichment.py

The batch orchestrator (runtime-agnostic).

- can run gap analysis unless `--skip-gap-analysis` is passed (which requires an existing gap report)
- loads remote deduplication keys from the `Agent_suggestions` and `Agent_suggestions_processed` tabs; on failure falls back to local-CSV deduplication with a warning
- `harvest_previous_results()`: walks `agent/output/swarm/*/results/*.json` and persists every valid journal result not already in the run state — recovers leftovers from interrupted runs and agents that finished late (works identically for OMP- and Mistral-written results)
- selects the backlog: excludes already-processed journals and Institution-only journals (recorded as `skipped`); sorts by highest gap priority
- `build_api_lookup_urls()`: pre-builds the per-journal public API URLs (DOAJ by ISSN/title, OpenAlex by ISSN, CrossRef journals by title, Scimago by ISSN/name) — the agents fetch these themselves
- `write_mission()`: per batch writes `mission.json` + one input JSON per journal + an empty `results/` directory under `agent/output/swarm/<batch_id>/`; each manifest entry carries a verbatim `subagent_prompt` so an OMP orchestrator does not have to invent prompts (the Mistral runtime reads the same input files directly)
- `build_subagent_prompt()` / `build_orchestrator_prompt()`: the OMP prompt templates. The orchestrator prompt requires spawning via the `task` tool in calls of 8 journals (parallel, short tool calls), a completion check with `ls`, one respawn per missing result, and a one-line `DONE <n>/<total>` report
- dispatches each batch to the selected runtime (`--runtime`, default `omp`): one OMP process via `omp_runtime.py`, or the Mistral fan-out via `mistral_runtime.py`; after each batch validates results via `sanitize_agent_result()`, appends accepted rows, updates run state, writes a checkpoint CSV
- late-result sweep at end of run: re-checks journals whose result file was missing at collection time; journals still without a file stay unprocessed (retried by a later run, then harvested by `harvest_previous_results`)
- appends deterministic `Alternative journal name` suggestions at the end of the run via `append_deterministic_suggestions()` from `issn_alt_name_suggestions.py`
- options: `--runtime` (`omp`/`mistral`, default `JOURNALMIND_RUNTIME` or `omp`), `--mistral-model`, `--wtp-dir`, `--gap-report`, `--skip-gap-analysis`, `--journal`, `--journal-limit`, `--max-suggestions`, `--swarm-size` (1–32, default 32), `--swarm-timeout-seconds`, `--output`, `--state`, `--log-dir`, `--credentials`

### agent/scripts/omp_runtime.py

OMP wrapper for the orchestrator process (used by the default `omp` runtime).

- builds `omp -p <prompt> --mode json --no-session --auto-approve --thinking off --append-system-prompt @agent/prompt/system.md --max-time <seconds> [--model <model>]` with the project root as working directory
- the OMP binary is `omp` (override with `JOURNALMIND_OMP_COMMAND`); the model defaults to OMP's configured model and can be overridden with `JOURNALMIND_MODEL`
- enforces the batch timeout: OMP `--max-time` plus a host-side timeout with SIGTERM/SIGKILL on the process group
- extracts the final assistant message from the JSONL stdout for the runner log; the message is informational only — the subagent result files are the source of truth
- writes prompt, stdout JSONL (including all subagent activity), and stderr artifacts per batch in `agent/output/logs/swarm-*`
- does not parse or validate suggestion JSON — result files are validated against the shared `agent_result.py` contract and sanitized in `suggestions_io.py`

### agent/scripts/mistral_runtime.py

The Mistral Agents SDK runtime (`--runtime mistral`).

- returns its answer as the shared Pydantic contract from `agent_result.py` (`Suggestion` / `AgentResult`, meaning `output_type=AgentResult`)
- `build_instructions()` = `agent/prompt/subagent.md` + a "Runtime Adaptation" section (input inlined in the user message, no file access, fetch via `fetch_url`, structured final answer) — the OMP-only file transport rules are superseded for this runtime
- host-side tools (module-level `@agents.tool` functions run by this Python process when the model calls them):
  - `fetch_url(url)` — urllib with a browser User-Agent, 20 s timeout, 600 kB raw cap; HTML pages are stripped to readable text (removes script/style/svg and tags ≈5–10× token savings); JSON APIs returned as-is; result capped at 60k characters; malformed URLs are percent-encoded; 4xx/5xx/network errors raise `agents.ToolError` (model-readable)
  - `web_search(query)` — free DuckDuckGo "lite" HTML scrape parsed into up to 8 `{title, url, snippet}` results (order-agnostic anchor parsing, `uddg` redirect unwrapping); no API key needed; failures raise `agents.ToolError` and the run continues
- `MistralSwarmRunner(log_dir, batch_timeout_seconds, model=None, concurrency=JOURNALMIND_MISTRAL_CONCURRENCY, per_journal_timeout=JOURNALMIND_MISTRAL_TIMEOUT_SECONDS)`:
  - builds a single reusable `agents.Agent(model=JOURNALMIND_MISTRAL_MODEL, instructions=build_instructions(), tools=[fetch_url, web_search], output_type=AgentResult, max_iterations=JOURNALMIND_MISTRAL_MAX_ITERATIONS)`; fails fast (RuntimeError) when `MISTRAL_API_KEY` is unset
  - `run(session_id, mission_dir, journals)` fans out one `agent.run()` per journal in an `asyncio.TaskGroup` bounded by a semaphore, reads each journal's `inputs/<idx>-<slug>.json`, and writes the validated `AgentResult` to `results/<idx>-<slug>.json` — the same file contract the OMP swarm produces
  - `Completed.output` of type `AgentResult` → result file; `InvalidOutput` or `None` output → one retry run with a schema-reminder appended to the user message, then unresolved; `Failed`/`Busy` → unresolved; per-journal and whole-batch timeouts → unresolved, resp. partial collection and `timed_out` result
  - returns the shared `SwarmRunResult` (from `enrichment_common.py`) with a `DONE <n>/<total> — ok/unresolved/error/retry` summary message
  - writes one status line per journal into `agent/output/logs/<session_id>.mistral.log`
- the structured-output validation replaces all JSONL parsing / comment stripping / compact-retry prompt logic; there is no shell and no agent-side file write in this runtime

### agent/scripts/issn_alt_name_suggestions.py

Deterministic `Alternative journal name` generator, including the ISSN → title lookup builders (formerly a separate `issn_lookups.py` module).

- lookup builders use stdlib csv/gzip plus canonical `format_issn()`:
  - `load_scimago_issn_title_lookup()` — `../data_extraction/scimagojr.csv.gz` (`;`-separated, comma-separated `Issn` list)
  - `load_doaj_issn_title_lookup()` — `DOAJ.csv.gz` (print ISSN / EISSN columns)
  - `load_openapc_issn_title_lookup()` — `openapc.csv.gz` (issn / issn_print / issn_electronic / issn_l, skipping `NA`)
- targets journals whose `Alternative journal name` is empty, that already have at least one ISSN, and for which at least one of `Present in Scimago`, `Present in DOAJ`, or `Present in openAPC` is `No`
- resolves the alternative title by ISSN with source priority `Scimago -> DOAJ -> OpenAPC`
- skips no-op matches whose normalized title equals the current journal name and ambiguous ISSN collisions
- writes `confidence = 1.00`, `suggestion_type = "alt_name"`, `priority = "high"` rows into `agent/output/Agent_suggestions.csv`
- `append_deterministic_suggestions(output, wtp_dir)` is called by `run_enrichment.py` at the end of every run; the module also works standalone as a CLI

### agent/scripts/upload_suggestions.py

Upload script for staging suggestions in the Google Sheet review tab.

- reads `Agent_suggestions.csv`; removes duplicate `(journal, field)` rows before upload, keeping the highest-confidence row per suggested column
- rewrites the cleaned CSV back to disk when duplicates were present
- sorts the rows for review before upload: priority (high → medium → low, unknown last), then confidence descending, with all suggestions of a journal kept as one contiguous group (grouped ordered by their best suggestion)
- resolves each suggestion's target cell (`data_tab`, `target_row`, `target_col`) from `../data_extracted/` through `../scripts/review_common.locate_journal_cell`; journals that are missing or present in several tabs get empty targets (the Apps Script reports them for manual handling)
- uploads through the shared `review_common.upload_review_tab`: tab created/cleared/rewritten, `Status` column prepended (new rows `pending`), report styling + Status dropdown applied
- options: `--input`, `--credentials`, `--data-extracted-dir` (target resolution)

### ../apps_script/ApplyReviewedChanges.gs (parent repo)

The unified Google Apps Script of the review workflow (both tabs; see `../apps_script/GOOGLE_APP_SCRIPT.md` in the parent repo).

- menu **WhereToPublish**: `Review Disagreements` / `Review Agent suggestions` (open the review tabs) and `Apply Reviewed Changes` (`applyReviewedSuggestions` kept as a backwards-compatible alias)
- reads each review tab, processes `approve` / `reject` rows of `Agent_suggestions` and `Disagreements`
- each approval is a verified single-cell write to the recorded target: the column header at `target_col` must match `field`, the Journal cell of `target_row` must match `journal`, and the cell must hold the recorded `current_value` (or the already-applied `suggested_value` — idempotent re-runs); `suggestion_type: remove` writes `FLAGGED_FOR_REMOVAL` onto the Journal column
- failed verifications leave the row in the review tab and are reported in the summary alert
- processed rows are batch-archived in `<tab>_processed` and batch-deleted in descending row order; tab headers are cached per run, so no whole data tab is ever scanned

### agent/scripts/agent_result.py

The shared Pydantic result contract for both runtimes.

- `Suggestion` / `AgentResult` models mirroring the result-JSON schema; the Mistral runtime passes `AgentResult` as `output_type`, so its answers are server-validated structured outputs
- the models carry the lenient coercions for untidy hand-written (OMP) results: scalar→string casts, confidence parsed from strings, pipe-joined `source_urls` strings split into lists
- `parse_result_file()` / `write_result_file()`: the mission result-file contract — every result file, whether written by an OMP subagent or by the Mistral runtime, is validated through the same models when collected
- `result_from_payload()` validates a decoded payload leniently: a payload without a journal name is invalid, one broken suggestion is skipped instead of poisoning the whole result (preserving the old per-suggestion resilience), and a missing status defaults to `unresolved`
- `unresolved_result()`: the shared fallback result
- differential regression (old dict-based sanitize vs the Pydantic pipeline) verified identical outcomes on all real result files from both runtimes

### agent/scripts/suggestions_io.py

Persistence and sanitization layer (business rules on top of the shared Pydantic contract).

- initializes and normalizes `Agent_suggestions.csv`; canonicalizes it on `(journal, field)` keeping the highest-confidence row per suggested column
- imports canonical `norm_name()` from `../scripts/libraries.py`
- `sanitize_agent_result(journal_name, journal_gap, agent_result: AgentResult | None, existing_keys)` takes the Pydantic-validated result from either runtime and applies the per-journal business rules:
  - only requested fields are eligible; suggestions without source URLs or reasoning are dropped
  - confidence must be in [0.55, 1]; `Alternative journal name` additionally needs ≥ 0.70, `suggestion_type == "alt_name"`, and must not normalize to the current journal name
  - `Business model` must be one of the allowed values
  - `APC Euros` must be an integer string; `0` is rejected for `Subscription` journals and otherwise requires explicit no-APC evidence in the reasoning
  - `e-ISSN` / `p-ISSN` / `ISSN-L` must match `XXXX-XXXX`
  - `Institution` suggestions equal to the publisher name are rejected; `Institution type` must be a valid category label and requires an institution value
  - exact `(journal, field, suggested_value)` triples present in the local CSV or the remote sheet tabs are dropped
- `status=ok` with zero valid rows is converted to `unresolved`; a garbage status becomes `error`; `None` (unparseable file) is an `error` with no rows
- writes `run_state.json` and checkpoint CSVs

## Prompts

### agent/prompt/system.md — OMP orchestrator

Appended to the OMP system prompt of every `omp` run (not used by the Mistral runtime):

- mission: coordinate the swarm; delegate all research, never perform it
- spawn one subagent per journal in consecutive `task` calls of 8 journals each, passing each journal's `subagent_prompt` verbatim
- verify result files exist; respawn each missing one once
- never read/edit result contents; never modify repository files; never execute project scripts
- finish with a one-line `DONE <n>/<total>` report

### agent/prompt/subagent.md — per-journal research protocol

The research rules for both runtimes. OMP subagents read it verbatim from disk; the Mistral
runtime uses it as the agent `instructions`, with a "Runtime Adaptation" section appended by
`mistral_runtime.py` (input JSON inlined in the user message instead of read from a file, no
shell/file access, `fetch_url` instead of `curl`, the final answer given as the structured
`AgentResult` instead of a result file). The research rules themselves are identical:

- workflow: fetch the public APIs first (DOAJ, then OpenAlex, then CrossRef — `curl -s` under OMP, `fetch_url` under Mistral), use web research only for gaps the APIs do not resolve, then produce the result JSON
- command safety (OMP): only `curl`, read-only file commands, and `python3 -m json.tool` for validating the result file; never execute project scripts, never launch agents/pipelines, never touch files outside the mission. Mistral agents have no commands at all.
- evidence rules for each API (DOAJ `has_apc`/price; OpenAlex `apc_prices`/`apc_usd`/`is_oa`; CrossRef ISSN types and exact-match confidence)
- strict rules: only requested gaps; hard-evidence Business model; absence of APC mention is not evidence of zero APC; correct EUR conversion; ISSN format; alt_name only from Scimago/DOAJ unambiguous matches; institution rules; no fabricated URLs
- confidence guidance by source authority
- result JSON schema with status `ok` / `unresolved`; agents must always produce a result (OMP: write the result file, Mistral: return the structured answer) even when everything is unresolved

## Review Workflow

The `Agent_suggestions` tab is the staging area for human review of AI suggestions; the pipeline's
deterministic `Disagreements` tab follows the exact same workflow (see `../apps_script/GOOGLE_APP_SCRIPT.md` in the parent repo).

- both AI-generated rows and deterministic ISSN-based `Alternative journal name` rows are staged in the same tab
- each row has a `Status` column (pending / approve / reject); new rows are always uploaded as `pending`
- each row records the verified target cell (`data_tab` / `target_row` / `target_col`) plus the
  verification values (`journal`, `field`, `current_value`) so the Apps Script applies approvals
  with a single verified cell write (no data-tab scans)
- team members set the status to `approve` or `reject` after reviewing evidence
- running **WhereToPublish → Apply Reviewed Changes** (Apps Script):
  - `approve` rows: the target cell is re-verified and `suggested_value` written into it, then the row is archived in `<tab>_processed` and deleted from the review tab
  - `reject` rows: the row is archived and deleted without any data change
  - `pending` rows are left untouched
  - approvals whose verification fails (moved rows, changed cells, missing targets) stay in the review tab and are reported in the summary alert; re-runs are idempotent (already-applied cells are recognized)
- the `Agent_suggestions_processed` tab accumulates all reviewed suggestions for performance analysis (see `NEXT_STEPS.md`)

## Persistence Contract

The effective contract between Python and the agents is:

- one JSON result file per journal (written by the OMP subagents; computed by Python from the structured-output `AgentResult` in the Mistral runtime), always validated through the shared Pydantic `agent_result.py` model and then the business rules of `sanitize_agent_result()`
- only requested gap fields are eligible
- the agents own research, browsing, and API access; the OMP orchestrator owns coordination only, and the Mistral runtime has no orchestrator at all (Python owns the fan-out)
- Python decides what gets persisted
- if no row survives validation, the journal is treated as unresolved

## Boundaries

- the WhereToPublish repo (`..`) is treated as read-only input during enrichment work
- neither the orchestrator, the OMP subagents, nor the Mistral agents may execute project scripts, launch agents/pipelines, or write outside their mission directory (Mistral agents have no file access)
- the Google service-account key never enters any agent session; all Sheets I/O is host-side Python
- the spreadsheet's data tabs are written only by the Apps Script review workflow, never by Python directly
- blocked publisher pages and missing source evidence are normal unresolved cases, not separate failure modes

## Outputs

Default output files:

- `agent/output/Agent_suggestions.csv`
- `agent/output/gap_report.json`
- `agent/output/state/run_state.json`

Run-level logs:

- `agent/output/logs/run-*.runner.log`

Per-batch runtime artifacts (OMP runtime):

- `agent/output/logs/swarm-*.prompt.txt`
- `agent/output/logs/swarm-*.stdout.json`
- `agent/output/logs/swarm-*.stderr.log`

Per-batch runtime artifacts (Mistral runtime):

- `agent/output/logs/swarm-*.mistral.log` — one status line per journal (status, suggestions, duration, notes); the full API traces are in the Mistral Studio console

Per-mission files (agent inputs and results, identical for both runtimes):

- `agent/output/swarm/<batch_id>/mission.json`
- `agent/output/swarm/<batch_id>/inputs/<idx>-<slug>.json`
- `agent/output/swarm/<batch_id>/results/<idx>-<slug>.json`

Checkpoint files:

- `agent/output/state/checkpoint_suggestions_*.csv` (one per completed batch)

Run-state entry statuses: `ok` (suggestions persisted), `unresolved` (result valid, nothing survived validation or no evidence), `error` (result file present but invalid), `skipped` (Institution-only gaps, never sent to the swarm), plus `session_id` values prefixed `harvest:` for journal results harvested from earlier missions.

## Known Runtime Constraints

Common:

- publisher websites are often Cloudflare-blocked and return 403 — for host fetches (Mistral runtime) and agent-side fetches alike; DOAJ, OpenAlex, and CrossRef APIs are fetched first and bypass this
- `--swarm-size` is capped at 32 journals per batch

OMP runtime:

- `web_search` in OMP requires a configured search provider (`webSearchOrder` in `~/.omp/agent/config.yml`); without one, subagents use the public APIs and direct page fetches
- an LLM backend with low throughput can stall while the orchestrator emits very long single tool calls — the orchestrator therefore spawns subagents in calls of 8 journals; a stalled or timed-out batch still yields all results that finished writing, and later runs harvest the rest
- OMP runs at most 32 concurrent subagents

Mistral runtime:

- research is billed per token against Studio API credits; the smallest default model (`ministral-3b-latest`, ~25–60 s per journal) keeps a journal in the sub-cent range but judges ambiguous evidence more weakly than a large model — set `JOURNALMIND_MISTRAL_MODEL=mistral-small-latest` (or larger) for production runs and set a budget alert in Studio before large backlog runs
- the Agents SDK has no server-side `web_search` builtin for programmatic agents; search is the free host-side DuckDuckGo tool, and if it breaks the affected gaps become `unresolved` (the run continues)
- concurrency is API-bounded: 4 journals in parallel by default (raise `JOURNALMIND_MISTRAL_CONCURRENCY` within rate limits); throughput per batch is lower than OMP's 32-wide swarm
- data leaves the host: journal names and their public bibliographic metadata go to the Mistral API (low sensitivity); the Sheets service-account key never does

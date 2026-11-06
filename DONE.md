# DONE.md — Current Project State

This file describes the current implementation state only. It is intended as a fast orientation document for LLMs working in this repository.

The project lives inside the WhereToPublish repository: `WhereToPublish/journal-metadata-enrichment/`.
The parent directory (`..`, i.e. the WhereToPublish repo root) is the data source and is treated as
read-only input. The AI agent is **OMP (oh-my-pi)**, installed on the host and plugged to an LLM
(`~/.omp/agent/config.yml`). Docker and Ollama are not used.

## Runtime Path

The enrichment runs a **swarm of OMP agents**: one non-interactive orchestrator process per batch, with one parallel subagent per journal spawned through OMP's built-in `task` tool.

```text
run_agent.sh
  -> verifies omp is in PATH and .venv/bin/python can import polars
  -> runs agent/scripts/run_enrichment.py, forwarding all extra arguments
     plus --swarm-timeout-seconds (default 3600, override: JOURNALMIND_SWARM_TIMEOUT_SECONDS)
  -> run_enrichment.py
    -> gap analysis (agent/scripts/gap_analysis.py), unless --skip-gap-analysis
       (verifies ../data_extraction/ files, downloads all 10 tabs via the Sheets API,
        runs the WTP pipeline with cwd=.., writes agent/output/gap_report.json)
    -> load_suggestion_keys_from_tabs() — remote deduplication keys
    -> harvest_previous_results() — persists leftover result files from earlier missions
    -> backlog selection (priority sort; Institution-only journals never sent to the swarm)
    -> per batch of up to 32 journals:
       write_mission() -> agent/output/swarm/<batch_id>/
         mission.json (manifest incl. verbatim subagent_prompt per journal)
         inputs/<idx>-<slug>.json (journal, known_metadata, requested_gaps, api_lookup_urls, result_file)
         results/ (empty; subagents write here)
       agent/scripts/omp_runtime.py
         -> omp -p <orchestrator prompt> --mode json --no-session --auto-approve --thinking off
            --append-system-prompt @agent/prompt/system.md --max-time <seconds> [--model <model>]
         -> the orchestrator spawns one subagent per journal (task tool, calls of 8 journals),
            each subagent reads inputs/<idx>.json + agent/prompt/subagent.md,
            does the full research itself (DOAJ/OpenAlex/CrossRef APIs via curl, web pages),
            and writes results/<idx>-<slug>.json; missing results get one respawn
       collect results -> agent/scripts/suggestions_io.py sanitize_agent_result()
                      -> append accepted rows to agent/output/Agent_suggestions.csv (canonical (journal, field))
    -> late-result sweep for journals whose result file was still missing at collection time
    -> deterministic Alternative journal name pass
       (agent/scripts/issn_alt_name_suggestions.py; Scimago -> DOAJ -> OpenAPC by ISSN,
        confidence 1.00 rows appended to the same CSV)
    -> agent/output/*

agent/scripts/upload_suggestions.py  (run manually after enrichment)
  -> agent/scripts/suggestions_io.py — collapses duplicate (journal, field) rows, keeps highest confidence
  -> ../scripts/sheets_client.py — writes Agent_suggestions tab with Status column
```

Key runtime properties:

- fully automated startup from the terminal; the launcher is the single entry point
- one OMP orchestrator run per batch of up to 32 journals; every journal is researched in parallel by its own subagent
- the subagents carry the heavy lifting: API fetches (DOAJ/OpenAlex/CrossRef with `curl`), web browsing, evidence interpretation, suggestion generation — Python performs no per-journal API pre-fetching anymore
- the orchestrator's instructions come from `agent/prompt/system.md` (appended to the OMP system prompt); each subagent reads `agent/prompt/subagent.md` from disk
- per-journal inputs and results are plain JSON files under `agent/output/swarm/<batch_id>/` — Python never parses the model's reply for suggestions; the result files are the source of truth
- journals whose only gaps are `Institution` / `Institution type` are never sent to the swarm (status `skipped` in the run state)
- the batch timeout (default 3600 s) is enforced via OMP `--max-time` plus a host-side process-group kill; after a timeout the run collects everything already written
- leftover results from earlier missions are harvested at the start of every run, so late-finishing subagents (OMP's worker daemon finishes them in the background) are never wasted; journals that still produced nothing are retried by a later run
- the OMP model defaults to the one configured in `~/.omp/agent/config.yml`; override per run with `JOURNALMIND_MODEL` (passed as `omp --model`)
- deterministic ISSN-based `Alternative journal name` suggestions are generated locally from Scimago, DOAJ, and OpenAPC without calling the LLM, at the end of every enrichment run
- `Agent_suggestions.csv` is canonicalized on (journal, field) so only the highest-confidence suggestion is kept for each suggested column
- unresolved is the normal fallback when evidence is weak or blocked
- before each run, remote suggestion keys are loaded from `Agent_suggestions` and `Agent_suggestions_processed`; if loading fails the run degrades gracefully to local-CSV deduplication only
- Google Sheets API (service-account auth) is used for all spreadsheet I/O: downloading tabs, loading remote deduplication keys, uploading suggestions

## Root Files

- `README.md`: human-facing overview of the current runtime
- `DONE.md`: this file
- `GOOGLE_APP_SCRIPT.md`: Apps Script setup for the human review workflow
- `NEXT_STEPS.md`: roadmap for using suggestion history to improve the agent
- `MistralStudio.md`: separate feasibility note about rebuilding the agent on Mistral Studio (unrelated to the swarm runtime)
- `run_agent.sh`: launcher used for real runs
- `requirements.txt`: Python dependencies for `.venv` (plus `mistralai-agents` for the MistralStudio feasibility work)
- `clean.sh`: removes `agent/output/` and hard-resets the parent WhereToPublish repo (main, then data branch)
- `.gitignore`: ignores credentials and generated output under `agent/output/`
- `Docs/`: task descriptions (TODO_9_swarms.md is the task that produced the swarm runtime; TODO_10_MistralStudio_feasability.md describes the separate Mistral Studio task)

## Launcher

`run_agent.sh` is the entry point:

- changes into the project root (the `journal-metadata-enrichment/` folder) before launching anything
- uses `.venv/bin/python` by default, with `JOURNALMIND_PYTHON` as the override
- checks that `omp` is available in PATH
- checks that `.venv/bin/python` can import `polars`
- validates `JOURNALMIND_SWARM_TIMEOUT_SECONDS` as a positive integer (default: 3600)
- launches `agent/scripts/run_enrichment.py` forwarding all extra arguments, always appending `--swarm-timeout-seconds <value>`
- appends `--max-suggestions <n>` when `JOURNALMIND_MAX_SUGGESTIONS` is set
- tees all runner output into `agent/output/logs/run-<id>.runner.log`

Current runner defaults:

- batch size: `--swarm-size 32` (OMP runs at most 32 concurrent subagents; range 1–32)
- batch timeout: `--swarm-timeout-seconds 3600`
- suggestion cap: `--max-suggestions 15`, overridable per run
- journals are sorted by their highest-priority gap (high → medium → low); the run stops when the suggestion cap is reached, when `--journal-limit` journals have been dispatched, or when the backlog is exhausted

## Python Modules

### agent/scripts/enrichment_common.py

Shared paths and schemas.

- defines `PROJECT_ROOT`, `AGENT_DIR`, and the output/log/state paths, plus `SWARM_DIR` (`agent/output/swarm/`)
- defines `DEFAULT_WTP_DIR` as the **parent directory** (the WhereToPublish repo root) and `WTP_SCRIPTS_DIR = ../scripts`
- defines `SYSTEM_PROMPT_PATH = agent/prompt/system.md` (orchestrator; appended to the OMP system prompt) and `SUBAGENT_PROTOCOL_PATH = agent/prompt/subagent.md`
- defines `UNRESOLVABLE_FIELDS = {"Institution", "Institution type"}` — journals with only these gaps never reach the swarm
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

The swarm orchestrator.

- can run gap analysis unless `--skip-gap-analysis` is passed (which requires an existing gap report)
- loads remote deduplication keys from the `Agent_suggestions` and `Agent_suggestions_processed` tabs; on failure falls back to local-CSV deduplication with a warning
- `harvest_previous_results()`: walks `agent/output/swarm/*/results/*.json` and persists every valid journal result not already in the run state — recovers leftovers from interrupted runs and subagents that finished late
- selects the backlog: excludes already-processed journals and Institution-only journals (recorded as `skipped`); sorts by highest gap priority
- `build_api_lookup_urls()`: pre-builds the per-journal public API URLs (DOAJ by ISSN/title, OpenAlex by ISSN, CrossRef journals by title, Scimago by ISSN/name) — the subagents fetch these themselves
- `write_mission()`: per batch writes `mission.json` + one input JSON per journal + an empty `results/` directory under `agent/output/swarm/<batch_id>/`; each manifest entry carries a verbatim `subagent_prompt` so the orchestrator does not have to invent prompts
- `build_subagent_prompt()` / `build_orchestrator_prompt()`: the two prompt templates. The orchestrator prompt requires spawning via the `task` tool in calls of 8 journals (parallel, short tool calls), a completion check with `ls`, one respawn per missing result, and a one-line `DONE <n>/<total>` report
- runs one OMP process per batch through `omp_runtime.py`; after each batch validates results via `sanitize_agent_result()`, appends accepted rows, updates run state, writes a checkpoint CSV
- late-result sweep at end of run: re-checks journals whose result file was missing at collection time; journals still without a file stay unprocessed (retried by a later run, then harvested by `harvest_previous_results`)
- appends deterministic `Alternative journal name` suggestions at the end of the run via `append_deterministic_suggestions()` from `issn_alt_name_suggestions.py`
- options: `--wtp-dir`, `--gap-report`, `--skip-gap-analysis`, `--journal`, `--journal-limit`, `--max-suggestions`, `--swarm-size` (1–32, default 32), `--swarm-timeout-seconds`, `--output`, `--state`, `--log-dir`, `--credentials`

### agent/scripts/omp_runtime.py

OMP wrapper for the orchestrator process.

- builds `omp -p <prompt> --mode json --no-session --auto-approve --thinking off --append-system-prompt @agent/prompt/system.md --max-time <seconds> [--model <model>]` with the project root as working directory
- the OMP binary is `omp` (override with `JOURNALMIND_OMP_COMMAND`); the model defaults to OMP's configured model and can be overridden with `JOURNALMIND_MODEL`
- enforces the batch timeout: OMP `--max-time` plus a host-side timeout with SIGTERM/SIGKILL on the process group
- extracts the final assistant message from the JSONL stdout for the runner log; the message is informational only — the subagent result files are the source of truth
- writes prompt, stdout JSONL (including all subagent activity), and stderr artifacts per batch in `agent/output/logs/swarm-*`
- does not parse or validate suggestion JSON — that lives in `suggestions_io.py`

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

Upload script for staging suggestions in the Google Sheet.

- reads `Agent_suggestions.csv`; removes duplicate `(journal, field)` rows before upload, keeping the highest-confidence row per suggested column
- rewrites the cleaned CSV back to disk when duplicates were present
- creates or reuses the `Agent_suggestions` tab, clears its content, and writes a fresh header + data rows
- prepends a `Status` column; new rows are always uploaded as `pending`
- options: `--input`, `--credentials`

### agent/scripts/suggestions_io.py

Persistence and sanitization layer.

- initializes and normalizes `Agent_suggestions.csv`; canonicalizes it on `(journal, field)` keeping the highest-confidence row per suggested column
- imports canonical `norm_name()` from `../scripts/libraries.py`
- `sanitize_agent_result()` validates each per-journal result object against the journal's requested gaps:
  - only requested fields are eligible; suggestions without source URLs or reasoning are dropped
  - confidence must be in [0.55, 1]; `Alternative journal name` additionally needs ≥ 0.70, `suggestion_type == "alt_name"`, and must not normalize to the current journal name
  - `Business model` must be one of the allowed values
  - `APC Euros` must be an integer string; `0` is rejected for `Subscription` journals and otherwise requires explicit no-APC evidence in the reasoning
  - `e-ISSN` / `p-ISSN` / `ISSN-L` must match `XXXX-XXXX`
  - `Institution` suggestions equal to the publisher name are rejected; `Institution type` must be a valid category label and requires an institution value
  - exact `(journal, field, suggested_value)` triples present in the local CSV or the remote sheet tabs are dropped
- `status=ok` with zero valid rows is converted to `unresolved`
- writes `run_state.json` and checkpoint CSVs

## Prompts

### agent/prompt/system.md — orchestrator

Appended to the OMP system prompt of every run:

- mission: coordinate the swarm; delegate all research, never perform it
- spawn one subagent per journal in consecutive `task` calls of 8 journals each, passing each journal's `subagent_prompt` verbatim
- verify result files exist; respawn each missing one once
- never read/edit result contents; never modify repository files; never execute project scripts
- finish with a one-line `DONE <n>/<total>` report

### agent/prompt/subagent.md — per-journal research protocol

Read from disk by every subagent:

- workflow: read the input file, fetch the public APIs first (`curl -s`; DOAJ, then OpenAlex, then CrossRef), use web research only for gaps the APIs do not resolve, then write the result JSON
- command safety: only `curl`, read-only file commands, and `python3 -m json.tool` for validating the result file; never execute project scripts, never launch agents/pipelines, never touch files outside the mission
- evidence rules for each API (DOAJ `has_apc`/price; OpenAlex `apc_prices`/`apc_usd`/`is_oa`; CrossRef ISSN types and exact-match confidence)
- strict rules: only requested gaps; hard-evidence Business model; absence of APC mention is not evidence of zero APC; correct EUR conversion; ISSN format; alt_name only from Scimago/DOAJ unambiguous matches; institution rules; no fabricated URLs; no file writes besides the result file
- confidence guidance by source authority
- result JSON schema with status `ok` / `unresolved`; subagents must always write the result file even when everything is unresolved

## Review Workflow

The `Agent_suggestions` Google Sheets tab is the staging area for human review.

- both AI-generated rows and deterministic ISSN-based `Alternative journal name` rows are staged in the same tab
- each row has a `Status` column (pending / approve / reject); new rows are always uploaded as `pending`
- team members set the status to `approve` or `reject` after reviewing evidence
- running **WhereToPublish → Apply Reviewed Suggestions** (Apps Script):
  - `approve` rows: the suggestion is written to the appropriate data tab, then the row is immediately deleted from `Agent_suggestions` and archived in `Agent_suggestions_processed`
  - `reject` rows: the row is immediately deleted and archived without any data change
  - `pending` rows are left untouched
  - each row is deleted as soon as it is archived; if the script times out, only unprocessed rows remain
- the `Agent_suggestions_processed` tab accumulates all reviewed suggestions for performance analysis (see `NEXT_STEPS.md`)

## Persistence Contract

The effective contract between Python and the agents is:

- one JSON result file per journal (written by the subagent; validated by Python)
- only requested gap fields are eligible
- the agents own research, browsing, and API access; the orchestrator owns coordination only
- Python decides what gets persisted
- if no row survives validation, the journal is treated as unresolved

## Boundaries

- the WhereToPublish repo (`..`) is treated as read-only input during enrichment work
- neither the orchestrator nor subagents may execute project scripts, launch agents/pipelines, or write outside their mission directory
- the spreadsheet's data tabs are written only by the Apps Script review workflow, never by Python directly
- blocked publisher pages and missing source evidence are normal unresolved cases, not separate failure modes

## Outputs

Default output files:

- `agent/output/Agent_suggestions.csv`
- `agent/output/gap_report.json`
- `agent/output/state/run_state.json`

Run-level logs:

- `agent/output/logs/run-*.runner.log`

Per-batch orchestrator artifacts:

- `agent/output/logs/swarm-*.prompt.txt`
- `agent/output/logs/swarm-*.stdout.json`
- `agent/output/logs/swarm-*.stderr.log`

Per-mission files (agent inputs and results):

- `agent/output/swarm/<batch_id>/mission.json`
- `agent/output/swarm/<batch_id>/inputs/<idx>-<slug>.json`
- `agent/output/swarm/<batch_id>/results/<idx>-<slug>.json`

Checkpoint files:

- `agent/output/state/checkpoint_suggestions_*.csv` (one per completed batch)

Run-state entry statuses: `ok` (suggestions persisted), `unresolved` (result valid, nothing survived validation or no evidence), `error` (result file present but invalid), `skipped` (Institution-only gaps, never sent to the swarm), plus `session_id` values prefixed `harvest:` for journal results harvested from earlier missions.

## Known Runtime Constraints

- `web_search` in OMP requires a configured search provider (`webSearchOrder` in `~/.omp/agent/config.yml`); without one, subagents use the public APIs and direct page fetches
- publisher websites are often Cloudflare-blocked and return 403; DOAJ, OpenAlex, and CrossRef APIs are fetched first by the subagents and bypass this
- an LLM backend with low throughput can stall while the orchestrator emits very long single tool calls — the orchestrator therefore spawns subagents in calls of 8 journals; a stalled or timed-out batch still yields all results that finished writing, and later runs harvest the rest
- OMP runs at most 32 concurrent subagents, which caps `--swarm-size`

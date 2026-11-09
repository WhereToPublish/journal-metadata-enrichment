# MistralStudio.md — Mistral Studio integration: the Agents SDK runtime and the Studio workflow

This project has three interchangeable AI layers, all writing the same result files and sharing
the same validation, persistence, review and upload path:

| Layer                             | Entry point                                                               | Where the code runs                                 | How it is started                                                 |
| --------------------------------- | ------------------------------------------------------------------------- | --------------------------------------------------- | ----------------------------------------------------------------- |
| OMP swarm (default CLI runtime)   | `./run_agent.sh`                                                        | host (`omp` CLI)                                  | terminal                                                          |
| Mistral Agents SDK runtime        | `./run_agent.sh --runtime mistral`                                      | host (`.venv`)                                    | terminal                                                          |
| **Mistral Studio workflow** | `./run_workflow.sh` (worker) + Studio website / `trigger_workflow.py` | worker on your host; orchestrated by Mistral Studio | **Start Workflow button in console.mistral.ai**, or API/CLI |

Scope note: nothing in the parent WhereToPublish repo (`..`) changed —
`../scripts/sheets_client.py`, `../scripts/libraries.py` and `../scripts/review_common.py` are
reused read-only. All edits live inside `journal-metadata-enrichment/`.

---

## 1. The Studio workflow (implemented)

`studio/` is a [Mistral Workflows](https://docs.mistral.ai/studio/workflows) package (SDK
`mistralai-workflows`, pulled in by `mistralai-agents`). Workflows run in **hybrid mode**:
Mistral Studio hosts the orchestrator (state, history, task dispatch, the Executions UI);
a **worker** process runs your code. The worker connects outbound only, auto-registers every
workflow/activity it finds, and polls for executions — so you keep the code, the
WhereToPublish repo and the Google credentials on your machine, and you **start runs from the
Mistral Studio website**.

### 1.1 Pieces

```
studio/
├── config.py       # paths; credential resolution (incl. Studio Secret); input/output models;
│                   # ALLOWED_WRITE_TABS = ("Agent_suggestions",) + assert_write_tab_allowed()
├── activities.py   # one durable @workflows.activity per pipeline step (see below)
├── workflow.py     # @workflows.workflow.define(name="wheretopublish-enrichment") entrypoint
├── deployment.py   # Future managed deployment utilities (not currently active)
└── worker.py       # run_worker(discover_all_workflows_in_package("studio"))
run_workflow.sh     # preflight (MISTRAL_API_KEY, .venv, DEPLOYMENT_NAME) then python -m studio.worker
trigger_workflow.py # start/poll an execution via the same API the Studio "Start Workflow" button uses
```

The registered workflow is **`wheretopublish-enrichment`**. Its entrypoint chains six durable
activities; each activity reuses the existing `agent/scripts/` modules, so the Studio workflow
and the CLI run exactly the same code:

1. `preflight_activity` — WTP repo present, the 5 `data_extraction/` files present,
   `MISTRAL_API_KEY` set, Google credentials resolvable.
2. `pull_google_sheet_activity` — downloads **all 10 data tabs** with a **read-only** Sheets
   scope into `../data_extracted/`.
3. `run_process_scripts_activity` — runs the WTP process scripts `scripts/update_extracted.py`
   → `scripts/data_process.py` with the WTP repo as cwd. They write **only local files**
   (`data_extracted/`, `data/`, `logs/`) — never the spreadsheet.
4. `build_gap_report_activity` — builds the unified gap report → `agent/output/gap_report.json`.
5. `ai_enrichment_activity` — the AI enrichment: backlog selection, one Mistral agent per
   journal (`MistralSwarmRunner`, model `ministral-3b-latest` by default — the cheapest),
   Pydantic-validated structured output, business-rule sanitization, persistence to
   `Agent_suggestions.csv`, then the deterministic ISSN alt-name pass. Runs fully async inside
   the worker's event loop (`run_enrichment_core_async`).
6. `upload_suggestions_activity` — pushes `Agent_suggestions.csv` to the **`Agent_suggestions`
   review tab only**, hard-guarded by `config.assert_write_tab_allowed`.

The workflow input (JSON, rendered as a form by the Studio UI from the Pydantic model):
`wtp_dir`, `journal`, `journal_limit`, `max_suggestions`, `swarm_size`, `skip_download`,
`skip_pipeline`, `skip_upload`, `model`, `concurrency`, `per_journal_timeout_seconds`,
`batch_timeout_seconds`. The output is a per-step summary plus `write_tabs` — the list of tabs
the execution actually wrote (always `["Agent_suggestions"]` or `[]`).

### 1.2 Running it

```bash
# 1. start the worker once (keep the terminal open); it registers the workflow in your workspace
export MISTRAL_API_KEY=...        # Studio workspace API key
./run_workflow.sh                 # DEPLOYMENT_NAME defaults to "wheretopublish-enrichment"

# 2a. run from the Mistral Studio website:
#     console.mistral.ai → Workflows → wheretopublish-enrichment → Start Workflow
#     (fill the input form) → watch the execution in the Executions tab
# 2b. or from the terminal (same API):
.venv/bin/python trigger_workflow.py --journal-limit 8 --max-suggestions 15
.venv/bin/python trigger_workflow.py --journal "Médecine et Santé Tropicales" --wait
.venv/bin/python trigger_workflow.py --status --execution-id <id>
```

Sheet safety: every read uses the readonly scope; the only write in the whole workflow is step 6,
guarded to `Agent_suggestions`. Verified on real runs: content hashes of all 17 other tabs were
byte-identical before/after workflow executions, while `Agent_suggestions` gained the new
`pending` rows.

### 1.3 The config .json — uploading it to Mistral (Secrets Manager)

Your Google service-account key never needs to be committed or pasted into code:

Google credentials are resolved from the `GOOGLE_SERVICE_ACCOUNT_KEY` environment variable (path to JSON file), or fallback to `~/.config/wheretopublish/google_service_account.json`.

### 1.4 Verified end-to-end (real executions, not dry runs)

- worker started with `./run_workflow.sh` → workflow `wheretopublish-enrichment` registered
  (visible via `client.workflows.get_workflow_registrations()` and in the Studio UI with its
  input form);
- full execution (input: `journal_limit=3, max_suggestions=5`): preflight → pulled all 10 tabs
  (2541 rows, read-only) → ran `update_extracted.py` + `data_process.py` → gap report (2383
  journals, 1249 with gaps) → 3 journals researched by live `ministral-3b-latest` agents
  (per-batch log `agent/output/logs/swarm-*.mistral.log`) → upload of 171 rows to
  `Agent_suggestions`; execution status `COMPLETED`, result carries per-step summaries;
- single-journal execution (`journal="Médecine et Santé Tropicales"`): the agent produced 6
  validated suggestions (ISSNs, alt name, business model, APC), upload wrote 174 rows;
  `Agent_suggestions` changed 172 → 175 rows, **all other 17 tabs byte-identical**;
- total Studio API cost of all test runs: a few cents (smallest model, ~20–50 s per journal).

Implementation notes discovered along the way:

- the worker requires `DEPLOYMENT_NAME` (defaulted in `run_workflow.sh`);
- Temporal's workflow sandbox forbids `Path.resolve()` at import time in modules the workflow
  imports — `studio/config.py` therefore resolves absolute paths lazily inside the activities;
- activities run inside the worker's event loop, so the enrichment core is async
  (`run_enrichment_core_async`; the CLI keeps the sync `run_enrichment_core` wrapper) and
  blocking steps (Sheets I/O, subprocesses) are wrapped in `asyncio.to_thread`.

---

## 2. The Agents SDK runtime (`--runtime mistral`, CLI)

```
run_agent.sh --runtime mistral            (or JOURNALMIND_RUNTIME=mistral)
  -> preflight: MISTRAL_API_KEY set + .venv can import mistralai.agents
  -> agent/scripts/run_enrichment.py  (orchestration shared with OMP)
     -> gap analysis, backlog selection, mission construction — identical to OMP
     -> agent/scripts/mistral_runtime.py  (the agent runtime)
          for each journal (asyncio, semaphore-bounded, default 4 concurrent):
            Agent(model=JOURNALMIND_MISTRAL_MODEL or "ministral-3b-latest",
                  instructions=subagent.md + "Runtime Adaptation" section,
                  tools=[fetch_url, web_search],       # host-side tools
                  output_type=AgentResult,              # Pydantic structured output
                  max_iterations=16)
            state = await agent.run(inlined input JSON, timeout=per-journal budget)
            Completed(output=AgentResult) -> write results/<idx>-<slug>.json
            InvalidOutput/None          -> one retry turn, then unresolved
            Failed/Busy/timeout         -> explicit unresolved result file
     -> result collection, sanitize_agent_result, checkpoints — shared
     -> issn_alt_name_suggestions.py    (deterministic)
  -> upload_suggestions.py -> Agent_suggestions tab -> Apps Script review
```

Key properties (unchanged from the previous implementation):

- **Python is the orchestrator** — the Agents SDK is a library harness (`Agent.run()` returns a
  turn record in-process), so no orchestrator LLM tokens are spent; the OMP-only `system.md`
  orchestrator prompt is not used.
- **Pydantic structured output at the API level** (`output_type=AgentResult` from
  `agent/scripts/agent_result.py`): no JSONL parsing, no comment stripping, no compact-retry
  prompt hacking — a non-conforming reply is a typed `InvalidOutput`; the only retry is one
  clean re-ask. Every result file, whichever runtime wrote it, is validated against the same
  `AgentResult` model on collection.
- **Host-side tools**: `fetch_url` (urllib, browser UA, 20 s timeout, 600 kB raw cap, HTML
  stripped to text, 60k-char result cap — the main credit saver) and `web_search` (free
  DuckDuckGo-lite scrape, up to 8 results; failures raise `ToolError` and the run continues).
  The Agents SDK has no server-side `web_search` builtin for programmatic agents.
- **Smallest model by default** (`ministral-3b-latest`) to conserve Studio credits
  (~$0.001–0.005 per journal research); `JOURNALMIND_MISTRAL_MODEL` overrides per run.
  Env knobs: `JOURNALMIND_MISTRAL_CONCURRENCY` (4), `JOURNALMIND_MISTRAL_TIMEOUT_SECONDS` (600),
  `JOURNALMIND_MISTRAL_MAX_ITERATIONS` (16).
- Env vars `JOURNALMIND_MISTRAL_*` are also surfaced as workflow input fields, so a Studio run
  controls model/concurrency/timeouts from the Start Workflow form.

Trade-offs vs the OMP runtime: no host agent binary, no search-provider setup, API-level
structured output, free web search, per-token cost control — but API-bounded concurrency
(default 4 vs OMP's 32), a weaker default model on ambiguous evidence (the strict validator and
the human review catch it; raise the model for production), Cloudflare 403s unchanged
(`unresolved`, never guesses), and journal metadata leaves the host to the Mistral API (the
Sheets key never does).

---

## 3. What Remains (Future Enhancements)

1. **Schedules / webhooks**: trigger executions on a cron or from a webhook instead of
   the Start Workflow button (`client.workflows.schedules`, triggers docs).
2. **Observability & evaluation**: use Studio's Trace Explorer, and feed
   `Agent_suggestions_processed` approve/reject labels into offline evaluations to
   compare models/prompts.
3. **Mistral-managed deployment**: Full cloud execution where Mistral hosts both orchestrator
   and worker (requires API key permissions not currently available).
4. **Review app** (explicitly out of scope for now): a small Studio app could replace
   the `Agent_suggestions` tab + Apps Script flow — same service-account client,
   approve/reject buttons writing the verified target cells. Until then the Google
   Sheet review workflow stays the single source of truth.

Preconditions/gotchas: Python 3.12–3.14 for the Agents/Workflows SDKs (the repo `.venv` is
3.14.9); set a budget alert in the Studio console before large backlog runs (~1250 journals
with gaps × ~$0.001–0.005 per smallest-model research).

# MistralStudio.md — Could the JournalMind agent run on Mistral Studio?

**Short answer: yes.** The current OMP (oh-my-pi) host-CLI agent can be moved to Mistral
Studio / the Mistral Agents API with a **drop-in runtime swap**: keep all the Python
orchestration (gap analysis, Google Sheets I/O, validation, upload, review) and replace only
the "LLM agent that researches one journal" part with a Mistral managed Agent session per
journal. A second, deeper option is to define and deploy the agent natively in the Studio
console. Both are feasible today; this document describes the process, the feasibility
evidence, what would need to change, step-by-step instructions, and the trade-offs vs the
current OMP implementation.

Scope note: nothing in the parent WhereToPublish repo (`..`) needs to change —
`../scripts/sheets_client.py` and `../scripts/libraries.py` are reused read-only, exactly as
today. All edits stay inside `journal-metadata-enrichment/`.

---

## 1. What "Mistral Studio" gives you (verified against current docs/SDK)

- **Mistral Studio** is Mistral's developer console + API platform
  ([docs.mistral.ai/studio](https://docs.mistral.ai/studio)): build agents, workflows and
  apps in the console, in code, or with a coding agent; run them in-process, on your own
  workers, or as **managed agents operated by Mistral**; observe **executions, traces and
  evaluations** in the console; create **API keys** in the Studio workspace.
- **Agents SDK** (`mistralai-agents` package, `from mistralai import agents`): define an
  `Agent(model, instructions, tools=[ClientTool...], connectors=[...], output_type=..., max_iterations, first_output_timeout_seconds)`
  once in Python and run it via `agent.run(content, output_type=..., on_client_tool=..., timeout=...)`
  or persistent sessions (`agent.session(session_id)`). Requires Python >=3.12,<3.15.
- **Built-in connectors** (server-side tools the agent can call): `web_search`,
  `web_search_premium`, `code_interpreter`, `image_generation`, `document_library`,
  `mistral_mcp` — plus custom MCP connectors and **client tools executed on your host**
  (`ClientTool` + the `on_client_tool` callback), which is the key escape hatch for
  Cloudflare-blocked sites.
- **Structured output**: `output_type=<Pydantic model>` makes the API return a validated
  object (invalid output comes back as `InvalidOutput` instead of unparseable text).
- **Subscription**: the Free plan already includes limited Studio access; **Pro
  ($14.99/mo, students $5.99)** adds API credits usable on the Studio/La Plateforme API —
  check [mistral.ai/pricing](https://mistral.ai/pricing) and your billing page for the exact
  credit amount, since enrichment cost scales with the number of journals researched.

### Feasibility checks already run in this session (no pipeline run, no API call)

- The project `.venv` is **Python 3.14.9** — inside the Agents SDK's supported range
  (>=3.12, <3.15).
- `mistralai-agents==1.1.0` is **already installed and importable** in `.venv`
  (`from mistralai import agents`; `Agent`, `ClientTool`, `Builtins`, `Completed`/`Failed`
  turn states all present; `Agent.run(content, output_type=..., on_client_tool=..., timeout=...)`
  confirmed by signature inspection).
- Built-in connector list confirmed in the installed SDK: `web_search`,
  `web_search_premium`, `code_interpreter`, `image_generation`, `document_library`,
  `mistral_mcp`.
- `MISTRAL_API_KEY` is **not set** in this environment yet — creating a Studio API key is
  step 1 of the instructions.
- A previous prototype of exactly this idea existed: `agent/scripts/__pycache__/mistral_runtime.cpython-314.pyc`
  (source since removed) implemented "one Managed Agent session per journal, host-side
  `fetch_url` client tool, Pydantic-validated result instead of a result file, semaphore-bounded
  concurrency, writes the same result JSON files as the OMP flow". So the approach has already
  been proven to fit this codebase once.
- Small bug found: `requirements.txt` is missing a newline, so the last line reads
  `google-api-python-clientmistralai-agents>=1.1.0` (one invalid package name). pip would
  fail on a fresh install; fix by splitting into two lines.

---

## 2. How the current OMP implementation maps to Mistral Studio

| Current OMP piece | Mistral Studio equivalent |
| --- | --- |
| `run_agent.sh` preflight: `omp` in PATH, `~/.omp/agent/config.yml` auth | `MISTRAL_API_KEY` env var (Studio workspace → API keys); preflight becomes `python -c "import mistralai.agents"` |
| One `omp -p <prompt>` **orchestrator** per batch, which spawns one `task` **subagent** per journal (max 32) | No orchestrator needed: Python fans out **one `Agent.run()` per journal** with an `asyncio.Semaphore` (e.g. 8 concurrent). Optionally a Studio managed agent/workflow for the batch |
| `agent/prompt/subagent.md` protocol read from disk by each subagent | Same file becomes the agent's `instructions` (plus a short "runtime adaptation" section, see §4) |
| Subagent reads `inputs/<idx>.json`, writes `results/<idx>.json` | Input JSON **inlined in the user message**; result returned as **structured output** (`output_type`); Python writes the same `results/<idx>.json` files so `run_enrichment.py` collection/validation is untouched |
| Subagent runs `curl` in its shell for DOAJ/OpenAlex/CrossRef APIs | Host-side **`fetch_url` ClientTool** via `on_client_tool` (plain urllib with a UA header — public JSON APIs are not Cloudflare-blocked). Publisher sites (Scimago/SAGE/Elsevier) stay 403 → same "leave unresolved" rule as today |
| OMP `web_search` tool (needs a configured `webSearchOrder` provider) | Built-in **`web_search` / `web_search_premium` connector** attached to the agent — no provider config needed |
| Parse JSONL stdout, strip `//` comments, retry once with compact prompt | `output_type=<Pydantic AgentResult>` validates server-side; on `InvalidOutput`/`Failed` do one retry turn in the same session (sessions are persistent server-side) |
| `--max-time` + host process kill | `Agent.run(..., timeout=...)` + host-side `asyncio.wait_for` as a belt-and-braces cap |
| Per-journal `enrichment-*.prompt/stdout/stderr` log files | `on_event` callback writes the same artifacts locally **and** Studio console keeps the full trace (agent/model/tool spans) |
| `suggestions_io.py` validation, `Agent_suggestions.csv`, `upload_suggestions.py`, Apps Script review | **Unchanged** — this is the point of the drop-in swap |

---

## 3. Overall process (recommended architecture)

```
run_agent.sh  (JOURNALMIND_RUNTIME=mistral)
  -> preflight: MISTRAL_API_KEY set + .venv can import mistralai.agents
  -> agent/scripts/run_enrichment.py --runtime mistral
     -> gap_analysis.py            (unchanged: Sheets download + WTP pipeline refresh)
     -> per batch: build mission inputs (unchanged)
     -> agent/scripts/mistral_runtime.py   (NEW — replaces omp_runtime.py)
          for each journal (asyncio, semaphore=JOURNALMIND_MISTRAL_CONCURRENCY, default 8):
            Agent(model=JOURNALMIND_MISTRAL_MODEL or default,
                  instructions=subagent.md + runtime adaptation,
                  tools=[ClientTool(fetch_url)],
                  connectors=[web_search],
                  output_type=AgentResult)   # Pydantic mirror of the result JSON schema
            state = agent.run(json.dumps(input_payload), output_type=AgentResult,
                              on_client_tool=host_fetch_url, timeout=...)
            write results/<idx>.json from state.output (or unresolved on Failed/timeout)
     -> result collection, sanitize_agent_result, checkpoints  (unchanged)
     -> issn_alt_name_suggestions.py                            (unchanged, deterministic)
  -> upload_suggestions.py -> Agent_suggestions tab -> Apps Script review (unchanged)
```

Two integration depths:

- **Option A — drop-in runtime swap (recommended first).** Everything above; only the
  "agent runtime" module changes. Small diff, reuses all validation/review, easy A/B against
  OMP on the same gap report.
- **Option B — Studio-native deployment.** Define the journal-researcher agent in the Studio
  console (or via the SDK) and deploy it as a **managed agent** (Mistral hosts the worker);
  trigger batches from Python via the API or a Studio workflow; review **traces, executions
  and evaluations** in the console instead of log files. Bigger ops change; do this after
  Option A proves suggestion quality, and use Studio **evaluations** fed from
  `Agent_suggestions_processed` (already envisioned in `NEXT_STEPS.md`).

---

## 4. What would need to change (all inside `journal-metadata-enrichment/`)

1. **`requirements.txt`** — fix the missing newline so the last two lines are
   `google-api-python-client` and `mistralai-agents>=1.1.0` separately (already the
   intended content, currently broken into one invalid package name).
2. **`agent/scripts/mistral_runtime.py`** (new, ~150–250 lines; the old `.pyc` shows the
   shape): config from env (`MISTRAL_API_KEY`, `JOURNALMIND_MISTRAL_MODEL` default e.g.
   `mistral-small-latest`, `JOURNALMIND_MISTRAL_TIMEOUT_SECONDS` default 600,
   `JOURNALMIND_MISTRAL_CONCURRENCY` default 8); build one `Agent` with
   `instructions = subagent.md + _RUNTIME_ADAPTATION`, `tools=[fetch_url ClientTool]`,
   `connectors=[web_search]`, `output_type=AgentResult`; `run_batch()` fans out with a
   semaphore, runs `agent.run(inline_input, output_type=AgentResult, on_client_tool=..., timeout=...)`,
   writes the same `results/<idx>.json` files plus per-journal prompt/event logs, returns a
   summary. `fetch_url` runs on the host (urllib, UA header, size cap) so DOAJ/OpenAlex/
   CrossRef stay reachable despite Cloudflare elsewhere.
3. **`agent/scripts/run_enrichment.py`** — add `--runtime {omp,mistral}` (env
   `JOURNALMIND_RUNTIME`, default `omp`) and select `OMPSwarmRunner` vs the new
   `MistralBatchRunner`. Keep the mission/results-file contract identical so mission
   building, result collection, `sanitize_agent_result`, dedup keys, and checkpoints are
   untouched.
4. **`run_agent.sh`** — branch the preflight on the runtime: for `mistral`, require
   `MISTRAL_API_KEY` and `.venv/bin/python -c "import mistralai.agents"` instead of `omp`.
5. **`agent/prompt/subagent.md`** — add a short "Mistral runtime adaptation" section that
   takes precedence over the file transport: input is inlined in the user message (do not
   read files), fetch URLs with the `fetch_url` client tool (never shell), do not write any
   file — return the result JSON as the final structured answer. All research rules
   (API-first order, evidence interpretation, strict per-field rules, confidence guidance)
   stay exactly as written.
6. **Pydantic schema** for `AgentResult` (`journal`, `suggestions[]` with
   `field/current_value/suggested_value/confidence/source_urls/reasoning/suggestion_type/priority`,
   `status`, `notes`) mirroring the current result JSON schema — this replaces the
   JSONL-parsing + comment-stripping + retry logic in `omp_runtime.py`.
7. **Nothing** in `../` (WhereToPublish repo): `sheets_client.py`, `libraries.py`,
   `data_extraction/`, `data_extracted/` are used read-only as today.

---

## 5. Step-by-step instructions

1. **Get an API key.** Sign in to the Studio console (via [console.mistral.ai](https://console.mistral.ai) → API keys, or the Studio workspace), create a key, and
   `export MISTRAL_API_KEY=...`. Confirm your Pro plan's included API credits on
   [mistral.ai/pricing](https://mistral.ai/pricing) / the billing page — the backlog is
   ~1250 journals with gaps, so set a budget alert in Studio before large runs.
2. **Fix deps.** Split the last line of `requirements.txt`, then
   `.venv/bin/python -m pip install -r requirements.txt` and verify
   `.venv/bin/python -c "from mistralai import agents; print('ok')"` (already true in the
   current `.venv`: `mistralai-agents 1.1.0` on Python 3.14.9).
3. **Add `agent/scripts/mistral_runtime.py`** per §4.2 and wire `--runtime mistral` into
   `run_enrichment.py` + the `run_agent.sh` preflight per §4.3–4.4.
4. **Adapt the prompt** per §4.5 (runtime-adaptation section in `subagent.md`) and add the
   `AgentResult` Pydantic model per §4.6.
5. **Smoke test on one journal** (reuses the existing gap report, no full pipeline run):
   ```bash
   JOURNALMIND_RUNTIME=mistral ./run_agent.sh --skip-gap-analysis \
       --journal "Human Genomics" --max-suggestions 3
   ```
   Expect the same `agent/output/Agent_suggestions.csv` rows shape as an OMP run; inspect
   the per-journal artifacts in `agent/output/logs/` **and** the run trace in the Studio
   console (Traces).
6. **A/B quality check.** Run the same small batch (e.g. 10 journals) with
   `--runtime omp` and `--runtime mistral` against the same `--gap-report`, upload both to
   `Agent_suggestions`, and compare approval rates through the existing review workflow
   (`Agent_suggestions_processed`). This mirrors the model-comparison plan in `NEXT_STEPS.md`.
7. **Batch run.** `JOURNALMIND_RUNTIME=mistral ./run_agent.sh --max-suggestions 15`
   (raise `JOURNALMIND_MISTRAL_CONCURRENCY` only within API rate limits; keep
   `JOURNALMIND_MISTRAL_TIMEOUT_SECONDS` per journal).
8. **Review & upload unchanged.** `.venv/bin/python agent/scripts/upload_suggestions.py`,
   then the Apps Script **Apply Reviewed Suggestions** flow — no change.
9. **Optional: go Studio-native (Option B).** In the Studio console, create a project,
   define the same agent (instructions + `web_search` connector + `fetch_url` tool),
   deploy it as a managed agent, and trigger batches from a thin Python caller or a Studio
   workflow. Use Studio **evaluations** on `Agent_suggestions_processed` rows to track
   approval rate over time.

---

## 6. Advantages vs the current OMP implementation

- **No host agent binary.** No `omp` install, no `~/.omp/agent/config.yml`, no search-provider
  config — just `MISTRAL_API_KEY`. Runs identically from a laptop or CI.
- **Built-in web search.** The `web_search`/`web_search_premium` connector replaces OMP's
  `webSearchOrder` provider setup.
- **Structured output at the API level.** `output_type` (Pydantic) enforces the result JSON
  contract — no more JSONL scraping, `//`-comment stripping, or compact-retry prompt hacking.
- **Observability for free.** Studio console keeps executions, traces (agent/model/tool spans)
  and usage per run — OMP only leaves local log files. This directly feeds the performance
  analyses planned in `NEXT_STEPS.md` (approval rate, calibration, reject-pattern mining) via
  Studio evaluations.
- **Managed/durable execution.** Managed agents run on Mistral-hosted workers: a long batch
  doesn't tie up your machine, and sessions are persistent server-side (resume/retry a single
  journal instead of rerunning a whole OMP process).
- **Cost visibility.** Per-token billing and usage dashboards in Studio; Pro API credits make
  small/medium runs predictable. (OMP's LLM cost depends on whatever backend `config.yml`
  points to and is invisible in the project.)
- **Extensibility.** Connectors (document library, MCP) could later hold the journal backlog
  or even the Google Sheet, moving more of the pipeline into Studio.

## 7. Drawbacks / risks vs OMP

- **Cost scales with backlog size.** ~1250 journals × web research × tokens is real money on
  the API; mitigate with `JOURNALMIND_MISTRAL_CONCURRENCY`/budget alerts, `--max-suggestions`
  caps, and the existing LLM-skip/deterministic-ISSN optimizations (keep them!). OMP may be
  cheaper if its configured LLM is local or flat-rate.
- **Rate limits & concurrency.** The 32-way OMP swarm becomes an API-bounded fan-out
  (start at 8); throughput per batch is lower and needs queueing.
- **Cloudflare.** Mistral's *server-side* browsing can hit the same 403s on publisher sites.
  Mitigation (already in the design): host-side `fetch_url` client tool / host pre-fetch for
  the public JSON APIs (DOAJ, OpenAlex, CrossRef); blocked sources still mean `unresolved`,
  never guesses.
- **Less control over the tool loop.** No shell, no arbitrary file writes inside the agent —
  by design here (Python owns persistence), but it means the prompt must carry all rules
  that OMP's tooling implicitly enforced.
- **Vendor lock-in / moving target.** `mistralai-agents` is young (1.1.0); pin the version and
  keep `omp_runtime.py` as a fallback runtime so switching back is a flag, not a rewrite.
- **Data leaves the host.** Journal names/metadata and sheet rows are sent to the Mistral
  API. The data is public bibliographic metadata (low sensitivity), but the Google Sheet
  service-account key must **never** be sent — keep all Sheets I/O host-side (as today).
- **Determinism/replay.** OMP artifacts (prompt/stdout/stderr per journal) must be replicated
  by logging `on_event` payloads ourselves if you need exact replays.

---

## 8. Verdict

Feasible **now**, with two depths: start with **Option A** (drop-in `mistral_runtime.py`,
`--runtime mistral` flag, ~4 files touched inside `journal-metadata-enrichment/`, zero changes
in the WhereToPublish repo), validate on a 10-journal A/B batch against OMP using the existing
review workflow, then optionally move to **Option B** (Studio console agent + managed workers +
traces/evaluations). The installed SDK in `.venv` already supports everything required
(`Agent.run` with `output_type`, `on_client_tool`, `timeout`; `web_search` connector), and a
previous prototype (`mistral_runtime.cpython-314.pyc`) confirms the result-file contract
plugs straight into `run_enrichment.py`.

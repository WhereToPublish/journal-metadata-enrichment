# MistralStudio.md — The Mistral Agents SDK runtime (implemented) and the road to a full Mistral Studio orchestrator

The enrichment swarm can now run on **Mistral Studio / the Agents SDK** as a drop-in
alternative runtime to the OMP (oh-my-pi) host CLI agent. This document describes
the **implemented runtime** (`--runtime mistral`), how it differs from the OMP
design, the trade-offs, and **what is still needed** to move the whole enrichment
orchestration into Mistral Studio as a native agent/workflow/app.

Scope note: nothing in the parent WhereToPublish repo (`..`) changed —
`../scripts/sheets_client.py` and `../scripts/libraries.py` are reused read-only,
exactly as in the OMP flow. All edits live inside `journal-metadata-enrichment/`.

---

## 1. What the implemented runtime is

```
run_agent.sh --runtime mistral            (or JOURNALMIND_RUNTIME=mistral)
  -> preflight: MISTRAL_API_KEY set + .venv can import mistralai.agents
  -> agent/scripts/run_enrichment.py  (orchestration unchanged)
     -> gap analysis, backlog selection, mission construction — identical to OMP
     -> agent/scripts/mistral_runtime.py  (the new agent runtime)
          for each journal (asyncio TaskGroup,Concurrency-bounded semaphore,
                           default 4 concurrent):
            Agent(model=JOURNALMIND_MISTRAL_MODEL or "ministral-3b-latest",
                  instructions=subagent.md + "Runtime Adaptation" section,
                  tools=[fetch_url, web_search],       # host-side tools
                  output_type=AgentResult,              # Pydantic structured output
                  max_iterations=16)
            state = agent.run(inlined input JSON, timeout=per-journal budget)
            Completed(output=AgentResult) -> write results/<idx>-<slug>.json
            InvalidOutput/None          -> one retry turn, then unresolved
            Failed/Busy/timeout          -> explicit unresolved result file
     -> result collection, sanitize_agent_result, checkpoints — unchanged
     -> issn_alt_name_suggestions.py    (unchanged, deterministic)
  -> upload_suggestions.py -> Agent_suggestions tab -> Apps Script review (unchanged)
```

The change is carried by three Python modules:

- **`agent/scripts/agent_result.py`** (~110 lines) — the shared Pydantic result contract
  (`Suggestion` / `AgentResult`) used by both runtimes: the Mistral runtime passes it as
  `output_type=AgentResult` to `agent.run()` (server-validated structured output), and every
  OMP-subagent-written result file is validated against the same model at collection time
  (`parse_result_file`). The models carry the old lenient coercions (scalar→string casts,
  confidence from strings, pipe-joined `source_urls`), so untidy hand-written results still
  validate; one broken suggestion is skipped instead of poisoning the whole result.
- **`agent/scripts/mistral_runtime.py`** (~430 lines):
  - `Suggestion` / `AgentResult` **Pydantic models** mirror the result-JSON schema.
    Passing `output_type=AgentResult` to `Agent.run()` makes the Mistral API return
    a *server-validated* object; a non-conforming reply comes back as
    `agents.InvalidOutput` instead of unparseable text. **This replaces the OMP
    runtime's JSONL-parsing, `//`-comment stripping and compact-prompt retry
    entirely** — the only retry left is one clean turn re-ask when the structured
    output is invalid or absent.
  - Two **host-side tools** the model calls instead of a shell:
    - `fetch_url(url)` — urllib with a browser User-Agent, 20 s timeout, 600 kB raw
      cap, HTML stripped to readable text, 60k-char result cap. This keeps the
      public JSON APIs (DOAJ / OpenAlex / CrossRef — never Cloudflare-blocked)
      reachable and keeps token usage (and therefore credits) small.
    - `web_search(query)` — DuckDuckGo "lite" HTML scraped into
      `{title, url, snippet}` results (up to 8). This is a *free* best-effort
      replacement for OMP's configured search provider; failures raise
      `agents.ToolError` and the agent simply continues.
  - `MistralSwarmRunner.run(session_id, mission_dir, journals)` fans out one
    `Agent.run()` per journal, writes the same `results/<idx>-<slug>.json` file
    contract as the OMP swarm, enforces a per-journal timeout
    (`JOURNALMIND_MISTRAL_TIMEOUT_SECONDS`, default 600 s) and an overall batch
    budget (`--swarm-timeout-seconds`, default 3600 s), and returns the same
    `SwarmRunResult` dataclass (moved to `enrichment_common.py`) as the OMP runner.
- **`agent/scripts/run_enrichment.py`** gained `--runtime {omp,mistral}` (env
  `JOURNALMIND_RUNTIME`, default `omp`) and dispatches to the matching runner;
  mission construction, validation, dedup, persistence, harvesting and the
  late-result sweep are **shared and unchanged**.
- **`run_agent.sh`** branches its preflight on the runtime: `mistral` requires
  `MISTRAL_API_KEY` and `from mistralai import agents`; `omp` requires the `omp`
  binary. Extra args and `--runtime` are forwarded, and a CLI `--runtime mistral`
  also switches the launcher's own check.

### Key design decisions

- **Python is the orchestrator.** The new Agents SDK (`mistralai-agents` 1.1.0)
  is a *library harness*: `Agent.run()` returns a turn record in-process, so no
  orchestrator LLM is needed at all — Python fans out per-journal runs directly
  with `asyncio`. The OMP flow needed an orchestrator agent only because OMP is
  a separate CLI process whose `task` tool spawns subagents; that layer simply
  disappears here, saving orchestrator tokens.
- **Structured output instead of file parsing.** The OMP flow let subagents write
  result files and Python parsed/scraped them. The result contract is now a shared
  Pydantic model (`agent_result.py`): the Mistral runtime returns its answers as
  `output_type=AgentResult` structured output validated by the API, and every
  result file — whether written by an OMP subagent or by the Mistral runtime —
  is validated against the same `AgentResult` model when collected, so
  `suggestions_io.py` applies the same business rules to both runtimes
  (a differential regression run confirmed identical outcomes on all real
  result files from both runtimes).
- **Smallest model by default.** `JOURNALMIND_MISTRAL_MODEL` defaults to
  `ministral-3b-latest` to conserve API credits. In tests it completed a journal
  in ~25–35 s and produced schema-valid results. Its research quality is weaker
  than the OMP-configured model on ambiguous cases (see §4) — for production runs
  set e.g. `JOURNALMIND_MISTRAL_MODEL=mistral-small-latest`.
- **No server-side web search.** The Agents SDK's `connectors=` are user-identity
  catalog connectors (GitHub, Linear, …) — the old Agents-API `web_search`
  builtin is *not* carried over for programmatic agents, so web search is
  implemented as the free host-side `web_search` tool above. Publisher sites that
  Cloudflare-block return 403 exactly as before: the field stays `unresolved`,
  never a guess.

### Environment variables (mistral runtime)

| Variable                               | Default             | Meaning                                              |
| -------------------------------------- | ------------------- | ---------------------------------------------------- |
| `MISTRAL_API_KEY`                      | — (required)        | Studio workspace API key                             |
| `JOURNALMIND_RUNTIME`                  | `omp`               | Default runtime when `--runtime` is not passed       |
| `JOURNALMIND_MISTRAL_MODEL`            | `ministral-3b-latest` | Model for per-journal research                      |
| `JOURNALMIND_MISTRAL_CONCURRENCY`      | `4`                 | Parallel journals per batch                          |
| `JOURNALMIND_MISTRAL_TIMEOUT_SECONDS`  | `600`               | Per-journal research budget                          |
| `JOURNALMIND_MISTRAL_MAX_ITERATIONS`   | `16`                | Agent-loop iteration cap (each is an API call)       |

### Verified end-to-end (real runs, not dry)

- remote dedup-key loading, leftover-result harvesting, mission construction;
- a live batch: 4 journals concurrently, one schema-failure retry that recovered,
  result files validated and persisted (`Agent_suggestions.csv`);
- an interrupted batch (external SIGKILL): the 3 finished result files were
  **harvested by the next run** and persisted — the existing recovery path works
  unchanged with mistral-generated results;
- a full run **with gap analysis** (10 Sheets tab downloads, WTP pipeline refresh,
  fresh gap report) followed by live research and the deterministic ISSN pass;
- the whole chain with the *smallest* model consumed only a few cents total
  (≈30 s per journal; tool results are text-stripped and size-capped).

---

## 2. Trade-offs vs the OMP swam runtime

Advantages of the Mistral runtime:

- **No host agent binary** — no `omp` install, no `~/.omp/agent/config.yml`, no
  search-provider setup; runs identically from a laptop or CI.
- **Pydantic-structured output at the API level** — no JSONL scraping, no
  comment stripping, no retry-prompt hacking; invalid outputs are a typed
  `InvalidOutput` value, not a parsing guess.
- **No orchestrator tokens** — Python's fan-out replaces the OMP orchestrator
  agent (`system.md` is a no-op here), so per-batch credit is spent only on research.
- **Free web search** through the host tool instead of a paid search provider.
- **Cost model under your control**: per-token billing, cheap small models,
  concurrency and iteration caps, and Studio usage dashboards/credit visibility.
- **Session continuity**: `Agent.run` returns `session_id`/`turn_id` handles — a
  single journal's session can be resumed or retried without re-running a batch
  (not exploited yet, see §3).

Drawbacks / risks:

- Weaker default model quality (`ministral-3b-latest`) on ambiguous evidence —
  mitigated by the unchanged strict validator (`suggestions_io.py`) and the human
  review workflow; raise the model for production runs.
- API-bounded concurrency (default 4; OMP goes to 32) — throughput per batch is
  lower; raise `JOURNALMIND_MISTRAL_CONCURRENCY` within rate limits.
- Cloudflare 403s are unchanged for publisher pages — `unresolved`, never guesses.
- Data leaves the host: journal names/metadata go to the Mistral API (public
  bibliographic metadata, low sensitivity). The Google service-account key is
  **never** sent: all Sheets I/O stays host-side.
- `web_search` is a scraped DuckDuckGo endpoint — if it breaks, agents fall back
  to the APIs and `fetch_url`, the run does not fail.
- Agent-side determinism/replay artifacts are lighter than OMP's JSONL streams:
  per-batch runs write `logs/swarm-*.mistral.log` + per-journal result files,
  not full transcripts (fixable via SDK tracing, see §3.3).

---

## 3. What is needed for a full Mistral Studio orchestrator (workflow + app)

The current state is the "Option A" runtime: Python orchestrates, the Mistral API
only runs per-journal research. A full **Studio-native** deployment — deployed
agents triggered from the console, runs and traces visible in Studio, humans
reviewing from a web app instead of a Google Sheet — needs the following steps.

### 3.1 Deploy the researcher as a managed agent (small step)

1. Put the agent in its own file (`main.py`-style module): exactly today's
   `Agent(model, instructions, tools=[fetch_url, web_search],
   output_type=AgentResult, max_iterations=16)` definition
   (`agent.to_workflow()` already converts an authoring Agent).
   Caveat: the host tools must first move to Mistral-hosted equivalents —
   `fetch_url`/`web_search` would become MCP tools or deployed-functions, since
   "managed" means Mistral runs the harness, not this Mac.
2. `agents serve` (self-hosted, tools keep running on your machine) or
   `agents deploy` (Mistral-managed worker; tools must be server-side).
3. Point `MistralSwarmRunner` at the deployed agent (sessions API) instead of
   `agent.run()`; the persistence contract (`Agent_suggestions.csv`, review tabs)
   does not change.

### 3.2 Move batch orchestration into a Studio workflow (medium step)

1. Wrap the deployed researcher in a **Workflows** project (Workflows SDK,
   `mistralai-workflows`): the workflow ingests a mission manifest (batch of
   journals), fans out one researcher agent per journal inside Mistral's durable
   execution, gathers the `AgentResult`s, and writes them back.
2. Host-side Google Sheets I/O (gap analysis, dedup keys, uploads) stays in
   Python and talks to the workflow through **triggers and webhooks**; or moves
   into the workflow once credentials are Studio *Secrets*.
3. Replace `run_agent.sh` batches by workflow **schedules/runs**; the backlog /
   `--max-suggestions` budget logic moves into the workflow code.

### 3.3 Observability and evaluation (small-medium step)

- Enable the SDK's OpenTelemetry export for local runs, and use Studio's
  **Trace Explorer** for deployed runs: today's `logs/swarm-*.mistral.log`
  becomes per-run traces with model/tool spans.
- Feed `Agent_suggestions_processed` (approve/reject labels) into Studio
  **offline evaluations** to track approval rates per model/prompt — the dataset
  the NEXT_STEPS.md analysis plan already calls for.

### 3.4 Review app (optional, larger step)

- A Studio **app** (or any small web front-end) could replace the
  `Agent_suggestions` Google Sheet tab + Apps Script "Apply Reviewed Suggestions"
  flow: show suggestions with source URLs, approve/reject buttons, write to the
  data tabs through the same service-account client. Until then the existing
  review workflow is unchanged and stays the single source of truth.

### 3.5 Preconditions / gotchas discovered during implementation

- Python **3.12–3.14** (non-free-threaded) is required by `mistralai-agents`; the
  repo `.venv` is 3.14.9 — OK.
- The new Agents SDK has **no built-in web_search** for programmatic agents
  (catalog connectors are user-identity services); use the host `web_search`
  tool or an MCP server when deploying managed.
- Structured output works per `agent.run(..., output_type=...)`; deployed agents
  parse but do not force the format — keep the one-retry logic.
- Tool results are the main cost driver: the HTML→text stripping and 60k caps in
  `fetch_url` cut tokens roughly 5–10×; keep them when moving tools server-side.
- Set a budget alert in the Studio console before large backlog runs
  (~1250 journals × gaps): the current smallest-model setup costs on the order of
  $0.001–0.005 per journal-research.

---

## 4. Model quality note (decision input, not a blocker)

Side-by-side on the same backlog slice, `ministral-3b-latest` produced
schema-valid, well-reasoned results for ISSN / Business-model / APC gaps that the
DOAJ/OpenAlex/CrossRef APIs resolve, but it sometimes over-credits weak evidence
for `Alternative journal name` (e.g. accepting a clearly different journal name
as alt_name on thin DOAJ/CrossRef grounds). The strict validator and the human
review pass catch these, but the approval rate will be lower than a
capable-model OMP run. Recommended production setting:
`JOURNALMIND_MISTRAL_MODEL=mistral-small-latest` (or `mistral-medium-latest`),
possibly running only `high`-priority gaps through the larger model and
low-priority ISSN gaps through the small one.

---

## 5. Keeping both runtimes

`omp_runtime.py` is untouched and remains the default runtime. Both runners
implement the same contract (`run() -> SwarmRunResult`, result files under
`agent/output/swarm/<batch_id>/results/`), so switching is a flag
(`--runtime`), not a rewrite, and A/B comparison on the same gap report is
directly possible.

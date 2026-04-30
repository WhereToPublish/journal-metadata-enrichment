# WhereToPublish — Journal Metadata Enrichment Agent

An AI agent that automatically researches missing journal metadata for the
[WhereToPublish](https://wheretopublish.github.io/) project and produces
structured suggestions for human review.

---

## What This Is

WhereToPublish is a curated database of biology journals that helps researchers choose
where to publish based on open-access status, APCs, publisher type, and other criteria.
The database is maintained manually in Google Sheets and enriched by an automated pipeline
that joins against Scimago, OpenAPC, and DOAJ.

**The problem**: many journals are not matched by the pipeline because their name in the
sheet differs from their name in external databases, or they are too niche/new to appear
there at all. This leaves many cells empty.

**This project** implements an AI agent (running locally via OpenClaw + Ollama) that:
1. Runs the enrichment pipeline to detect what is still missing.
2. Browses DOAJ, publisher websites, Scimago, and CrossRef to find missing values.
3. Writes structured suggestions (with sources and confidence scores) to a CSV file.
4. Human reviewers inspect the CSV, check each suggestion, and accept/reject it.
5. Accepted suggestions flow back into the Google Sheet, then through the pipeline.

The agent **never writes directly to the Google Sheet** — all changes require human approval.

---

## Repository Layout

```
journal-metadata-enrichment/
├── README.md                     ← this file
├── DONE.md                       ← LLM-targeted project state description
├── NEXT_STEPS.md                 ← How to wire Google Sheets write access + review UI
├── run_agent.sh                  ← Single entry point — start the agent here
│
├── agent/
│   ├── workspace/                ← OpenClaw agent workspace (config, skills, identity)
│   │   ├── AGENTS.md             ← Operating rules
│   │   ├── SOUL.md               ← Agent personality and confidence discipline
│   │   ├── IDENTITY.md           ← Name: JournalMind
│   │   ├── USER.md               ← User profile (Thomas Latrille)
│   │   ├── TOOLS.md              ← Tool conventions (paths, limits, checkpointing)
│   │   ├── BOOT.md               ← Startup checklist
│   │   ├── HEARTBEAT.md          ← Minimal (no scheduling; manual start only)
│   │   └── skills/
│   │       └── journal-enrichment/
│   │           └── SKILL.md      ← Core skill: pipeline knowledge + workflow + output format
│   │
│   ├── scripts/
│   │   ├── fetch_sheet.py        ← Download a Google Sheet tab as CSV (no auth, public URL)
│   │   └── gap_analysis.py       ← Run pipeline, identify gaps, write gap_report.json
│   │
│   └── output/                   ← Agent-generated files (gitignored)
│       ├── AI_Suggestions.csv    ← Final agent output for human review
│       ├── gap_report.json       ← Pipeline gap analysis results
│       └── checkpoint_*.csv      ← Periodic checkpoints during long runs
│
├── Docs/                         ← Background reports (Report-1..3, TODO_kickstart)
│
├── WhereToPublish.github.io/     ← The WTP website + data pipeline (separate repo, cloned)
│   └── scripts/
│       ├── download_csv.sh       ← Downloads all Google Sheet tabs as CSVs
│       ├── update_extracted.py   ← Enriches CSVs with external sources
│       ├── data_process.py       ← Normalises, deduplicates, outputs final CSVs
│       └── libraries.py         ← Shared utilities (norm_name, norm_url, etc.)
│
└── openclaw/                     ← OpenClaw source repo (cloned for reference)
```

---

## Requirements

### Software
- **macOS** (tested on M2, 32 GB RAM)
- **Node.js** ≥ 18 (for OpenClaw)
- **OpenClaw** globally installed: `npm install -g openclaw`
- **Ollama** installed and running: https://ollama.com/download
- **Python 3.12** with `polars` (the WTP pipeline dependency)

### Recommended Python environment
```bash
python3 -m venv /Users/tlatrille/Documents/venv/py312stats
source /Users/tlatrille/Documents/venv/py312stats/bin/activate
pip install polars
```

### LLM model (choose one based on your hardware)
```bash
ollama pull qwen2.5:32b      # Recommended: best reasoning, ~19 GB on disk
ollama pull qwen3:30b-a3b    # Alternative: MoE architecture, very fast, ~18 GB
ollama pull gemma3:27b       # Alternative: Google Gemma 3, ~17 GB
```

Then configure OpenClaw to use the model:
```bash
openclaw config set agents.defaults.model.primary ollama/qwen2.5:32b
```

---

## Quickstart

```bash
# 1. Clone required repos (if not already done)
git clone https://github.com/WhereToPublish/WhereToPublish.github.io.git
git clone https://github.com/openclaw/openclaw.git

# 2. Start the agent
./run_agent.sh

# 3. In the WebChat that opens, paste and send:
#    "Start the journal enrichment task for Genetics & Genomics."

# 4. The agent will:
#    - Download the latest sheet data
#    - Run the enrichment pipeline
#    - Research each journal with missing metadata
#    - Write suggestions to agent/output/AI_Suggestions.csv

# 5. Review suggestions:
cat agent/output/AI_Suggestions.csv
```

---

## How the Agent Works

```
run_agent.sh
    │
    ├─ configures OpenClaw workspace → agent/workspace/
    ├─ starts OpenClaw gateway
    └─ opens WebChat at localhost:18789

WebChat (user sends task message)
    │
    JournalMind agent (powered by local LLM via Ollama):
    │
    ├─ Step 1: runs gap_analysis.py
    │          ├─ downloads Genetics & Genomics CSV (public Google Sheets export)
    │          ├─ downloads Scimago, DOAJ, OpenAPC, Dataverse (if not cached)
    │          ├─ runs update_extracted.py + data_process.py
    │          └─ produces gap_report.json (per-journal list of missing fields)
    │
    ├─ Step 2: reads gap_report.json
    │          → sorted list of journals, HIGH-priority gaps first
    │
    └─ Step 3: for each journal with gaps:
               ├─ searches DOAJ for OA status, publisher, APCs
               ├─ visits official publisher page
               ├─ checks Scimago for the journal's exact title (critical for pipeline joins)
               └─ writes suggestions to AI_Suggestions.csv with:
                  - exact column to update
                  - suggested value
                  - confidence score (0–1)
                  - source URLs
                  - reasoning
```

---

## Output: AI_Suggestions.csv

The agent writes one row per suggestion. Columns:

| Column | Description |
|--------|-------------|
| `journal` | Exact journal name (matches Google Sheet) |
| `field` | Column to update (e.g. "Business model", "Scimago Journal Title") |
| `current_value` | Current value in the sheet (empty if null) |
| `suggested_value` | Proposed new value |
| `confidence` | Float 0.0–1.0 (≥ 0.85 = high confidence) |
| `source_urls` | Pipe-separated URLs of pages visited |
| `reasoning` | 1–2 sentence explanation of the evidence |
| `suggestion_type` | `fill` / `alt_name` / `correct` / `add` / `remove` |
| `priority` | `high` / `medium` / `low` |

**`suggestion_type` meanings:**
- `fill` — adding a value to an empty field
- `alt_name` — providing the `Scimago Journal Title` that fixes a failed pipeline join
- `correct` — correcting an existing wrong value
- `add` — adding a new journal row (missing from database)
- `remove` — flagging a non-existent journal for removal

---

## Uploading to Google Sheets & Review

See [NEXT_STEPS.md](NEXT_STEPS.md) for full instructions on:
1. Creating a Google Cloud service account and enabling the Sheets API.
2. Running `upload_suggestions.py` to push the CSV to the `AI_Suggestions` tab.
3. Installing the Google Apps Script that lets team members check/uncheck suggestions and apply them with one click.

---

## Known Limitations

- **Model hallucinations**: The LLM may misread a web page or infer an incorrect value.
  All suggestions require human review before entering the database.
- **Anti-bot measures**: Some publisher websites (Nature, ScienceDirect) use aggressive
  bot detection. The browser tool may fail on these; the agent will skip them.
- **Name-only matching**: The pipeline matches journals by name (no ISSN). Finding the
  correct `Scimago Journal Title` is the highest-value action for many journals.
- **Context window**: Very long browsing sessions may approach the model's context limit.
  The agent checkpoints every 10 journals to limit data loss.
- **Memory usage**: Running a 27B+ model + browser sessions on a 32 GB machine will use
  ~25–30 GB RAM. Close other heavy applications before starting an overnight run.

---

## Running gap_analysis.py Standalone

You can run the gap analysis independently to inspect current data quality:

```bash
source /Users/tlatrille/Documents/venv/py312stats/bin/activate
python3 agent/scripts/gap_analysis.py

# Skip downloads if you already have fresh data:
python3 agent/scripts/gap_analysis.py --skip-download

# Skip pipeline run too (use existing output files):
python3 agent/scripts/gap_analysis.py --skip-download --skip-pipeline

# View the report:
python3 -m json.tool agent/output/gap_report.json | head -60
```

---

## Stopping the Agent

```bash
openclaw gateway stop
```

To restore your previous OpenClaw workspace (if you had one):
```bash
openclaw config unset agents.defaults.workspace
```

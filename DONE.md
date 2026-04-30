# DONE.md — Project State (for LLMs)

This file describes the current implementation state of the journal-metadata-enrichment project.
It is written for LLMs that need to understand the codebase quickly.
It does not contain change history — only the current state.

---

## Project Root: /Users/tlatrille/Documents/journal-metadata-enrichment/

### Files at Root Level

| File | Purpose |
|------|---------|
| `README.md` | Human-facing overview. Contains quickstart, architecture diagram, output format explanation, known limitations, and how to run gap_analysis.py standalone. |
| `DONE.md` | This file. LLM-targeted state description. |
| `NEXT_STEPS.md` | Step-by-step instructions for: (A) Google Cloud service account + Sheets API, (B) `upload_suggestions.py` (code included inline), (C) Google Apps Script for accept/reject review workflow, (D) Ollama model configuration. |
| `run_agent.sh` | Executable bash script. Entry point for the entire system. Checks OpenClaw + Ollama are available, sets `agents.defaults.workspace` to `agent/workspace/`, starts the OpenClaw gateway, opens WebChat at localhost:18789, and prints the task message to paste. |
| `.gitignore` | Standard ignore file. Must be extended to ignore `agent/output/*.csv`, `agent/output/*.json` (sensitive/generated). See below. |

---

## agent/ Directory

### agent/workspace/ — OpenClaw Workspace

This directory is the OpenClaw workspace. It is pointed to by `run_agent.sh` via:
```bash
openclaw config set agents.defaults.workspace "agent/workspace"
```
OpenClaw injects all `.md` files in this directory into the agent's system prompt at session start.

| File | Content Summary |
|------|-----------------|
| `IDENTITY.md` | Agent name: JournalMind. Emoji: 🔬. Role: academic journal metadata specialist. |
| `SOUL.md` | Personality: skeptical, evidence-based, precise. Confidence discipline: ≥0.85 suggest directly, 0.65–0.84 flag for careful review, <0.50 skip. Never fabricate URLs. Never write directly to Google Sheet. |
| `USER.md` | Thomas Latrille. Role, preferences, all relevant file paths hardcoded. Spreadsheet ID, GID for Genetics & Genomics tab. |
| `AGENTS.md` | Operating rules. Always start with gap_analysis.py. Research HIGH gaps first. Per-journal workflow (DOAJ → publisher → Scimago). The 4 goals. Output format. Strict rules. |
| `TOOLS.md` | Tool conventions. Python path: `/Users/tlatrille/Documents/venv/py312stats/bin/python3`. Max 2 browser sessions. Write only to `agent/output/`. Checkpointing every 10 journals. |
| `BOOT.md` | Startup checklist: verify Ollama, WTP repo path, agent scripts, Python+polars. |
| `HEARTBEAT.md` | Minimal. States that this agent is manual-start only, no scheduling. |

### agent/workspace/skills/journal-enrichment/SKILL.md

The core knowledge file. ~8 KB. Contains:
- Complete column schema for the Google Sheets database (all 16 columns including `Scimago Journal Title`)
- Exact Business model values: `OA diamond` / `OA` / `Hybrid` / `Subscription`
- APC ↔ Business model coupling rules
- Full data pipeline explanation (download_csv.sh → update_extracted.py → data_process.py)
- `norm_name()` logic explained (why some journal names fail to match)
- Two-pass join strategy (Journal name first, Scimago Journal Title fallback)
- The 4 agent goals with threshold conditions
- Source authority hierarchy (L1–L5) with confidence score ranges
- Complete `AI_Suggestions.csv` column schema with types, valid values, and example row
- `suggestion_type` values: `fill` / `alt_name` / `correct` / `add` / `remove`
- Step-by-step workflow
- Useful research URLs (DOAJ, Scimago, CrossRef, NLM)
- Key do-nots (no ISSN, no direct file modification, no hallucinated URLs)

---

### agent/scripts/

#### fetch_sheet.py
Downloads a single Google Sheet tab as CSV using the public export URL (no auth required).
Defaults to Genetics & Genomics (gid=1379563174) → `agent/output/raw_genetics_genomics.csv`.
Supports `--gid`, `--field` (named alternative to --gid), `--output` arguments.
All 10 tab GIDs are hardcoded. Uses `urllib.request` (stdlib only, no extra deps).

#### gap_analysis.py
The primary analysis script. Dependencies: stdlib + `polars` (from WTP virtualenv).

**What it does**:
1. Downloads the Genetics & Genomics sheet to `WhereToPublish.github.io/data_extracted/genetics_genomics.csv`
2. Downloads external sources (Scimago, DOAJ, OpenAPC, Dataverse) to `data_extraction/` if not already present. Uses gzip compression matching WTP pipeline expectations.
3. Runs `update_extracted.py` then `data_process.py` via `subprocess.run()` with `cwd=wtp_dir`. Uses the same Python executable that ran this script (`sys.executable`).
4. Reads post-enrichment `data/genetics_genomics.csv` and compares against raw input.
5. Per journal: identifies missing fields using `FIELD_PRIORITIES` dict (field → priority, weight).
6. Special detection: journals with no Scimago data (Rank AND Quartile both empty) → `gap_type="alt_name"` with priority=high, weight=8.
7. Sorts journals: HIGH gaps first, then MEDIUM, then LOW, alphabetically within tier.
8. Writes `agent/output/gap_report.json`.

**gap_report.json structure**:
```json
{
  "run_date": "2026-04-30",
  "wtp_dir": "/path/to/WhereToPublish.github.io",
  "tab": "Genetics & Genomics",
  "total_journals": N,
  "journals_with_gaps": N,
  "total_gap_instances": N,
  "priority_summary": {"high": N, "medium": N, "low": N},
  "field_gap_counts": {"Business model": N, "Scimago Journal Title": N, ...},
  "journals": [
    {
      "name": "Journal of ...",
      "website": "https://...",
      "current_publisher": "...",
      "current_business_model": "...",
      "gaps": [
        {"field": "Business model", "current_value": "", "gap_type": "fill", "priority": "high"},
        {"field": "Scimago Journal Title", "current_value": "", "gap_type": "alt_name", "priority": "high", "note": "..."}
      ]
    }
  ]
}
```

**CLI arguments**:
- `--wtp-dir PATH` — path to WhereToPublish.github.io (default: auto-detected relative to script)
- `--skip-download` — skip fetching Google Sheet + external sources
- `--skip-pipeline` — skip running update_extracted.py + data_process.py
- `--output PATH` — override output path for gap_report.json

---

### agent/output/

Generated files, should be gitignored:
- `AI_Suggestions.csv` — agent output. 9 columns: `journal,field,current_value,suggested_value,confidence,source_urls,reasoning,suggestion_type,priority`
- `gap_report.json` — pipeline gap analysis
- `checkpoint_suggestions_N.csv` — periodic checkpoints (same format as AI_Suggestions.csv)
- `raw_genetics_genomics.csv` — downloaded by fetch_sheet.py

---

## WhereToPublish.github.io/ Directory (read-only reference)

The actual WTP website + pipeline repo. **Do not modify directly**.

| Path | Purpose |
|------|---------|
| `scripts/download_csv.sh` | Downloads all 10 Google Sheet tabs as CSVs to `data_extracted/` |
| `scripts/download_extraction.sh` | Downloads Scimago, DOAJ, OpenAPC, Dataverse, PCI to `data_extraction/` |
| `scripts/update_extracted.py` | Enriches CSVs with external sources. Two-pass name-matching. |
| `scripts/data_process.py` | Normalises, deduplicates (by URL then name), outputs `data/*.csv` |
| `scripts/libraries.py` | Shared: `norm_name()`, `norm_url()`, `load_csv()`, publisher/country normalisation |
| `scripts/run.sh` | Orchestrates full pipeline (all 4 steps) |
| `scripts/APC_process.py` | Generates per-publisher APC trend CSVs (not used by agent) |
| `data_extracted/` | Raw CSV downloads (created by pipeline, not committed) |
| `data_extraction/` | External source data (Scimago/DOAJ/etc., not committed) |
| `data/` | Final processed CSVs (website source) |

**Key facts for the agent**:
- Spreadsheet ID: `1PRXViyQlo5ZMjpCJ_XpcHfsnZEJmmdCiXjnkazMyua8`
- Public export URL: `https://docs.google.com/spreadsheets/d/e/2PACX-1vTw97FS3eOFbYlqY8j7wWrBd3yrDaG6hqPclYJdPrnvd7t9U2DNz5xXNK4F0iesyHIKEkx9weLz-69a/pub`
- Genetics & Genomics GID: `1379563174`
- Name-matching is the only join key — no ISSN column exists.
- `Scimago Journal Title` enables the fallback second-pass join in update_extracted.py.
- `norm_name()` strips: articles (the/la/le/a), connectors (of/and/&), parentheses, non-alphanumeric.

---

## What Is NOT Yet Done

1. **`agent/scripts/upload_suggestions.py`** — not created yet. Code is provided inline in `NEXT_STEPS.md`. Requires Google Cloud service account + `google-api-python-client` installed. Create this file only after completing Phase A+B of NEXT_STEPS.md.

2. **Google Apps Script** — not installed in the Google Sheet yet. Code provided in `NEXT_STEPS.md` section C2. Requires manual installation via Extensions → Apps Script in the spreadsheet.

3. **`.gitignore` update** — `agent/output/` should be added to `.gitignore` to avoid committing AI-generated files and downloaded data. Currently no `.gitignore` exists at the project root.

4. **Ollama model configuration** — the user must pull a model and configure `openclaw config set agents.defaults.model.primary` before the agent can research journals. Instructions in `NEXT_STEPS.md` Phase D and `run_agent.sh`.

5. **Dataverse download** — the Dataverse URL in gap_analysis.py is a best-guess direct file URL. The actual Dataverse API URL for the APC dataset (DOI: 10.7910/DVN/CR1MMV) may need updating. If the download fails, the agent will warn and continue (Dataverse is lowest priority source).

---

## Current State: What Works Right Now

- `python3 agent/scripts/gap_analysis.py` — works if polars is installed and WhereToPublish.github.io/ is at the expected path. Downloads data, runs pipeline, produces gap_report.json.
- `python3 agent/scripts/fetch_sheet.py` — works standalone with no dependencies beyond stdlib.
- `./run_agent.sh` — works if OpenClaw is globally installed. Configures workspace, starts gateway, opens WebChat.
- All workspace files (AGENTS.md, SOUL.md, SKILL.md, etc.) — written and ready for OpenClaw to inject.
- The agent can research journals and write AI_Suggestions.csv as soon as a suitable Ollama model is pulled and configured.

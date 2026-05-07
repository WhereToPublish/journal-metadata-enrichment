---
name: journal-enrichment
description: Enrich WhereToPublish journal metadata by researching missing fields and suggesting corrections via DOAJ, Scimago, publisher pages, and other authoritative sources.
user-invocable: true
disable-model-invocation: false
---

# Journal Enrichment Skill

## When to Use
- User says "start journal enrichment", "run the enrichment task", or similar.
- User asks you to research missing journal metadata.
- User asks you to find the Scimago name for a journal.
- User asks you to check if a journal is open access.

## When NOT to Use
- User is asking a general question about publishing (use your LLM knowledge).
- User is asking you to modify the website code.
- User is asking you to run the full pipeline (that is gap_analysis.py's job).

---

## 1. WhereToPublish Database — Column Reference

The database lives in Google Sheets. The Genetics & Genomics tab (gid=1379563174) is the
initial experiment target. Each row is one journal. Columns:

| Column | Type | Filled by | Notes |
|--------|------|-----------|-------|
| `Journal` | string | Manual | Primary identifier. Name-only matching — NO ISSN. |
| `Website` | URL | Manual + DOAJ | Official journal homepage. |
| `Field` | string | Manual | Biology subfield (e.g. "Genetics & Genomics"). |
| `Publisher type` | string | Manual + inference | "For-profit" / "University Press" / "Non-profit" / "Society" |
| `Publisher` | string | Manual + enrichment | Publisher name (normalized). |
| `Institution` | string | Manual + DOAJ | Sponsoring society/organization if any. |
| `Institution type` | string | Manual + inference | "Society" if Institution contains "Society". |
| `Country` | string | Manual + DOAJ/Scimago | Country of publisher. |
| `Business model` | string | Manual + enrichment | **Exact values**: `OA diamond` / `OA` / `Hybrid` / `Subscription` |
| `APC Euros` | integer | Manual + enrichment | Article processing charge in EUR (0 for diamond OA). |
| `Scimago Rank` | float | Scimago | SJR metric. |
| `Scimago Quartile` | string | Scimago | Format: "Q1 (Genetics; Molecular Biology)" |
| `H index` | integer | Scimago | Scimago H-index. |
| `PCI partner` | string | PCI list | "PCI friendly" or empty. Do not suggest changes to this field. |
| `Scimago Journal Title` | string | **Manual (AI fills)** | **KEY FIELD.** The name under which this journal appears in Scimago. When empty AND the journal has no Scimago data, finding this name is the highest-impact action. |

### Business Model Exact Values (copy precisely)
- `OA diamond` — fully open access, zero APC (APC Euros must be 0 or empty)
- `OA` — fully open access with author charges (APC Euros > 0)
- `Hybrid` — subscription journal offering OA option at a fee
- `Subscription` — traditional subscription, no OA option

### APC ↔ Business Model Coupling (enforced by pipeline)
- If Business model = `OA diamond` → pipeline forces APC Euros = 0
- If APC Euros > 0 AND Business model is empty → pipeline infers `Hybrid`
- To suggest a journal is OA diamond: set Business model = `OA diamond` AND APC Euros = `0`

---

## 2. Data Pipeline — How It Works

```
Google Sheets (manual curation)
    ↓  [download_csv.sh]
data_extracted/genetics_genomics.csv          ← raw input
    ↓  [update_extracted.py]
data_extracted/genetics_genomics.csv          ← enriched with Scimago/DOAJ/OpenAPC/Dataverse
    ↓  [data_process.py]
data/genetics_genomics.csv                    ← final: normalized, deduplicated, formatted
```

### Name-Matching Logic (update_extracted.py → norm_name())
The pipeline matches journal names by normalizing them:
1. Lowercase everything
2. Remove diacritics (é→e, ñ→n, etc.)
3. Remove leading articles: "the ", "la ", "le ", "les ", "el ", "a "
4. Remove connector words: " of ", " an ", " and ", " & "
5. Remove parenthesized text: `(...)` removed
6. Remove all non-alphanumeric characters
7. Result: "The Journal of Applied Ecology" → "journalappliedecology"

**Why this matters for you**: When you search Scimago and find a journal under a slightly
different name (e.g. "Journal of Genetics & Genomics" vs "Journal of Genetics and Genomics"),
check if `norm_name()` would make them equal. If yes, the pipeline already handles it and no
alt-name suggestion is needed. If no, the alt name IS needed in `Scimago Journal Title`.

### Two-Pass Join Strategy
1. **First pass**: Match `Journal` (normalized) against Scimago/DOAJ/OpenAPC/Dataverse.
2. **Second pass**: For unmatched journals, match `Scimago Journal Title` (normalized) against Scimago.

Only the second pass uses `Scimago Journal Title`. If `Scimago Journal Title` is empty AND
the first pass fails (no Scimago rank), the journal gets no Scimago data at all.

### What external sources fill
| Source | Fills |
|--------|-------|
| Scimago | Rank, Quartile, H index, Publisher (if empty), Business model (if empty) |
| OpenAPC | APC Euros, Publisher (if empty), Business model (if empty) |
| DOAJ | Publisher (if empty), Country, Website, Institution, APC Euros (if empty) |
| Dataverse | APC Euros (if empty), Publisher (if empty), Business model (if empty) |

Fields the pipeline NEVER overwrites (always defer to manual curation):
- `Journal`, `Field`, `Publisher type`, `Institution type`, `PCI partner`

---

## 3. The Active Agent Goals

### Goal 1: Fill Empty Cells (Most Common)
Journals still have empty cells after enrichment because:
- The journal is not in Scimago/DOAJ under the same name → find the `Scimago Journal Title`
- The journal is genuinely new or niche → find the value directly from the publisher page

For each missing field, attempt in this order:
1. Check DOAJ (fastest, covers OA status + publisher + country + APC)
2. Check the journal's own website (most authoritative for APCs)
3. Check Scimago (for rank/quartile, and to discover the alt name)
4. Check CrossRef (for publisher info)

### Goal 2: Correct Errors
Signs of an error:
- Business model says "Subscription" but DOAJ lists the journal as fully OA
- Publisher name is obviously wrong (e.g. a former publisher)
- APC Euros = 0 but business model = "OA" (should probably be "OA diamond")

Only correct if you have strong evidence (confidence ≥ 0.75).

### Goal 3: Flag Non-Existent Journals
If after checking DOAJ + Scimago + CrossRef + Google, you find no trace of a journal:
- Set `suggestion_type = "remove"`, `field = "Journal"`, `suggested_value = "REMOVE"`
- Confidence threshold for removal: ≥ 0.90 (very high — do not remove on ambiguity)

Out of scope for this runtime:
- Do not propose adding new journals.
- Work only on journals already present in the caller-provided backlog.

---

## 4. Source Authority Hierarchy and Confidence Scoring

| Level | Condition | Confidence |
|-------|-----------|------------|
| L1 | Publisher page confirms field AND DOAJ agrees | ≥ 0.90 |
| L2 | DOAJ confirms alone (curated registry, highly reliable) | 0.80–0.89 |
| L3 | Scimago confirms OR publisher page alone (no DOAJ entry) | 0.65–0.79 |
| L4 | CrossRef or other secondary source alone | 0.55–0.64 |
| L5 | Single ambiguous or low-quality source | < 0.55 → SKIP |

**For Scimago Journal Title suggestions specifically**:
- If you find the exact journal title in Scimago after a name variation search: confidence = 0.85
- If the Scimago result is clearly the same journal (same publisher, same field): add 0.05
- If the Scimago result is ambiguous (common name): subtract 0.10

**For new/niche journals without DOAJ entry**:
- Publisher page alone is sufficient (L3 confidence = 0.65–0.79) — do not skip these
- They are often the most important to fill (not yet indexed by aggregators)

---

## 5. Suggestion Output Format

In automated one-journal mode, return suggestion objects in JSON to the caller.
The caller persists accepted rows to `agent/output/AI_suggestions.csv`.
Do not write that file directly unless the caller explicitly asks for file mutation.

### Column Schema (exact, in this order)
```
journal,field,current_value,suggested_value,confidence,source_urls,reasoning,suggestion_type,priority
```

| Column | Type | Description |
|--------|------|-------------|
| `journal` | string | Exact journal name as it appears in the Google Sheet |
| `field` | string | Column to update (e.g. "Business model", "Scimago Journal Title") |
| `current_value` | string | Current value in the sheet (empty string if null/missing) |
| `suggested_value` | string | Proposed new value |
| `confidence` | float | 0.00–1.00 |
| `source_urls` | string | Pipe-separated (`\|`) list of URLs actually visited |
| `reasoning` | string | 1–2 sentences explaining the evidence |
| `suggestion_type` | string | `fill` / `alt_name` / `correct` / `remove` |
| `priority` | string | `high` / `medium` / `low` |

### suggestion_type Values
- `fill` — adding a value to an empty field
- `alt_name` — providing/correcting the `Scimago Journal Title` to fix a failed pipeline join
- `correct` — changing an existing (wrong) value
- `remove` — flag a journal for removal (non-existent)

### Priority Rules
- `high`: Business model, Publisher, Scimago Journal Title (when missing Scimago data)
- `medium`: Country, Website, APC Euros, Publisher type
- `low`: Institution, Institution type

### Example Row
```
"Genome Biology","Business model","","OA diamond",0.92,"https://genomebiology.biomedcentral.com/submission-guidelines/article-processing-charges|https://doaj.org/toc/1474-760X","Publisher page and DOAJ both confirm fully open access with no APC for BioMed Central model. APC waiver is available, net APC = 0 for society-sponsored articles.","fill","high"
```

### CSV Formatting Rules
- Use comma as delimiter.
- Quote every field with double-quotes.
- Escape internal double-quotes by doubling them: `""`.
- Use `|` (pipe) to separate multiple URLs in `source_urls`.
- Do NOT use newlines inside any field.
- First row must be the header: `journal,field,current_value,suggested_value,confidence,source_urls,reasoning,suggestion_type,priority`

---

## 6. Step-by-Step Workflow

```
START
    │
    ├─ Step 1: Read the caller-provided journal name, known metadata, requested gaps, and lookup URLs.
    │
    ├─ Step 2: Fetch the provided official website, DOAJ lookup URL, and Scimago lookup URL.
    │
    ├─ Step 3: If those sources are insufficient, use other public sources such as CrossRef.
    │
    ├─ Step 4: Score evidence using the source hierarchy and confidence rules above.
    │
    ├─ Step 5: Return one JSON object containing only supported suggestions for requested fields.
    │
    └─ Step 6: If evidence is insufficient, return `status: "unresolved"` with an empty suggestions array.
```

---

## 7. Key Do-Nots
- Do NOT use ISSN — this database has no ISSN column.
- Do NOT modify WhereToPublish.github.io/ files directly.
- Do NOT write to the Google Sheet.
- Do NOT write to `AI_suggestions.csv` directly in automated one-journal runs.
- Do NOT hallucinate URLs — only cite pages you actually browsed.
- Do NOT use Docker or containers.
- Do NOT schedule runs — this is manual-start only.
- Do NOT suggest more than ~50 items per session.
- Do NOT skip HIGH priority gaps to do LOW priority ones.

---

## 8. Useful URLs for Research
- DOAJ search: `https://doaj.org/search/journals/<JOURNAL_NAME>`
- DOAJ journal page: `https://doaj.org/toc/<ISSN>` (if ISSN known from DOAJ search)
- Scimago search: `https://www.scimagojr.com/journalsearch.php?q=<JOURNAL_NAME>`
- CrossRef search: `https://search.crossref.org/?q=<JOURNAL_NAME>&from_ui=yes`
- NLM catalog: `https://www.ncbi.nlm.nih.gov/nlmcatalog/?term=<JOURNAL_NAME>`

When searching, try these name variations if the first search fails:
1. Exact name as in the sheet
2. Name without leading "The "
3. Name with "&" replaced by "and" (or vice versa)
4. Abbreviated name (common for older journals)

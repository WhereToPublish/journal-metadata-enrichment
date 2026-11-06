# Subagent Research Protocol — One Journal per Subagent

## Mission
You research exactly **one** academic journal and write structured metadata suggestions for it. Your caller (the swarm orchestrator) launched you with an input file; everything you need to start is in that file.

## Workflow
1. Read your input file (path given by the orchestrator). It contains:
   - `journal` — the exact journal name in the WhereToPublish dataset
   - `known_metadata` — current website, publisher, business model, and ISSNs
   - `requested_gaps` — the only fields you may suggest values for
   - `api_lookup_urls` — pre-built public API URLs for this exact journal
   - `result_file` — the absolute path where you must write your result JSON
2. **Fetch the public APIs first** using your bash tool with `curl -s` (they are not Cloudflare-blocked), in this order:
   - `api_lookup_urls.doaj_api_by_issn` (if any ISSN is known) or `doaj_api_by_title`
   - `api_lookup_urls.openalex_api_by_issn` (if any ISSN is known)
   - `api_lookup_urls.crossref_journals_by_title` — cross-check ISSN gaps
3. Use the API evidence (rules below) to fill as many requested gaps as possible. Only for gaps the APIs do not resolve, use further web research: your web search and page fetch tools, the official website in `known_metadata`, and Scimago lookups (`api_lookup_urls.scimago_by_issn` / `scimago_search`).
4. Validate every suggestion against the strict rules below. Unreliable or blocked sources mean "leave unresolved", never guess.
5. Write exactly one JSON object (schema below) to `result_file`. Before finishing, verify the file parses: `python3 -m json.tool <result_file>`.
6. Reply with one short line summarizing your outcome.

## API Evidence Interpretation

### DOAJ (`https://doaj.org/api/v3/search/journals/...`, response JSON: `results[0].bibjson`)
- `apc.has_apc == true` + `apc.max[0].price/currency` → sufficient evidence for Business model='OA' and APC Euros (convert to EUR if needed)
- `apc.has_apc == false` (journal found) → sufficient evidence for Business model='OA diamond' AND APC Euros='0'
- `bibjson.publisher.name` → publisher name
- `bibjson.title` → canonical DOAJ title (may be a real alternative name)

### OpenAlex (`https://api.openalex.org/sources?filter=issn:<issn>&select=...`, response: `results[0]`)
- `apc_prices` → list of `{price, currency}` — prefer EUR directly, otherwise convert correctly (USD → EUR at ~0.92)
- `is_oa=true` AND `apc_usd>0` → strong evidence for Business model='OA'
- `is_oa=false` AND `apc_usd>0` → strong evidence for Business model='Hybrid'
- `is_oa=false` AND `apc_usd=null` → suggests 'Subscription' (verify with the publisher page when possible)
- `is_oa=true` AND `apc_usd=null` → suggests 'OA diamond' (DOAJ is more authoritative)
- `host_organization_name` → publisher name. APC data comes from ESAC/OpenAPC registries and is reliable for Hybrid and OA journals.

### CrossRef (`https://api.crossref.org/journals?query=<name>&rows=5`, response: `message.items`)
- Prefer the item whose `title` exactly equals the journal name (case-insensitive). If none matches exactly, verify the publisher matches `known_metadata.publisher` before using the data.
- `issn-type` entries give the print (`type=="print"`) and electronic (`type=="electronic"`) ISSNs — use them directly for p-ISSN / e-ISSN.
- ISSN-L is typically equal to the print ISSN (or the only ISSN when a single one exists).
- Exact title match → confidence 0.90; approximate match → ~0.75. When the exact match exists, do not fetch anything else for ISSN fields.
- Never use ISSN values as the Alternative journal name.

## Strict Rules
- **Command safety**: the ONLY commands you may run are `curl` against public web APIs, read-only file operations (`cat`, `ls`, `head`), and `python3 -m json.tool` to validate your result file. NEVER execute project scripts (`run_agent.sh`, anything in `scripts/`, `../scripts/`), never launch `omp` or any other agent or pipeline, and never touch files under `agent/output/` other than your own `result_file`.
- Only suggest values for fields listed in `requested_gaps`. Omit every other field. Never suggest an empty `suggested_value`.
- **Business model requires hard evidence**: explicit text on the journal page or in DOAJ/OpenAlex data (e.g. "This journal is open access", "APC: $X"). Never infer from publisher reputation. Allowed values only: `OA diamond`, `OA`, `Hybrid`, `Subscription`.
- **Absence of APC mention is NOT evidence of a zero APC.** Only suggest APC Euros = 0 when a source explicitly states no charge (e.g. "no APC", "free to publish", "does not charge") or DOAJ's `apc.has_apc` is false. Never suggest APC Euros = 0 when `known_metadata.business_model` is 'Subscription'.
- APC Euros must be a plain integer string, converted to EUR correctly when the source lists another currency (leave unresolved if uncertain).
- `e-ISSN`, `p-ISSN`, and `ISSN-L` values must be formatted `XXXX-XXXX` (last character may be an `X` check digit).
- `Alternative journal name`: always use `suggestion_type: "alt_name"`, never `fill`. Get the name from Scimago or DOAJ (an unambiguous match for the same journal — same publisher/scope). Never infer it from topic keywords, and never suggest a name that is just a formatting variant of the current name. Confidence must be ≥ 0.70.
- `Institution`: never infer from a commercial publisher name alone; only suggest when a source explicitly identifies the sponsoring institution. `Institution type` must be a category label: `Society`, `Society/Association`, `University`, `Research Institute` — never the institution name itself.
- Only cite URLs you actually fetched during this run. Never fabricate URLs.
- Do not propose adding new journals. Do not write any file other than your `result_file`. Do not read or modify other journals' files.
- Suggestions need confidence between 0.55 and 1; prefer unresolved over weak guesses.

## Confidence Guidance
- Publisher page confirms AND DOAJ agrees → ≥ 0.90
- DOAJ alone (curated registry) → 0.80–0.89
- OpenAlex APC data (ESAC/OpenAPC registries) → 0.75–0.84
- Scimago or publisher page alone → 0.65–0.79
- CrossRef or other secondary source alone → 0.55–0.64
- Single ambiguous source → below 0.55 → leave unresolved

## Result JSON Schema
Write exactly one JSON object to your `result_file` (no comments inside the JSON):

```json
{
  "journal": "Exact journal name",
  "suggestions": [
    {
      "field": "Business model",
      "current_value": "",
      "suggested_value": "Hybrid",
      "confidence": 0.86,
      "source_urls": ["https://api.doaj.org/..."],
      "reasoning": "1-2 sentences of evidence.",
      "suggestion_type": "fill",
      "priority": "high"
    }
  ],
  "status": "ok",
  "notes": "Optional short note."
}
```

- `suggestion_type`: `fill` (empty field), `alt_name` (Alternative journal name), `correct` (fix a wrong value), `remove` (flag non-existent journal, field="Journal", suggested_value="REMOVE", confidence ≥ 0.90).
- `priority`: `high` for Business model / Publisher / Alternative journal name; `medium` for Country / Website / APC Euros / Publisher type; `low` for Institution / Institution type / e-ISSN / p-ISSN / ISSN-L.
- `source_urls` lists the URLs you actually fetched that support the suggestion.
- `status` is `"ok"` when at least one suggestion survives the rules above; `"unresolved"` when zero do. You must always write the result file, even when everything is unresolved or blocked.

## Useful URLs for Research (fallbacks)
- DOAJ by title: `https://doaj.org/api/v3/search/journals/title:<JOURNAL_NAME>`
- Scimago by name: `https://www.scimagojr.com/journalsearch.php?q=<JOURNAL_NAME>&tip=jou`
- Scimago by ISSN: `https://www.scimagojr.com/journalsearch.php?q=<ISSN>&tip=issn`
- CrossRef works fallback: `https://api.crossref.org/works?query.bibliographic=<JOURNAL_NAME>&rows=5&select=container-title,ISSN,issn-type,publisher`
- NLM catalog: `https://www.ncbi.nlm.nih.gov/nlmcatalog/?term=<JOURNAL_NAME>`

When searching by name, try variations: exact name; without leading "The "; with "&" replaced by "and" (or vice versa); abbreviated name. Publisher websites (Scimago, SAGE, Elsevier) are often Cloudflare-blocked and return 403 — skip them and leave the field unresolved rather than guessing.

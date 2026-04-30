# Local Tool Conventions

## exec
- Always use full absolute paths for scripts:
  `/Users/tlatrille/Documents/journal-metadata-enrichment/agent/scripts/gap_analysis.py`
- Use the active Python from the virtualenv if available, otherwise `python3`.
- Preferred virtualenv: `/Users/tlatrille/Documents/venv/py312stats/bin/python3`
- Run all Python scripts with: `/Users/tlatrille/Documents/venv/py312stats/bin/python3 <script>`
- Never run with a bare `python` (could be Python 2 on some systems).
- Do NOT run destructive commands (`rm -rf`, `git push`, `git reset --hard`).
- Do NOT modify any files inside `WhereToPublish.github.io/` via exec.
- Run pipeline scripts via gap_analysis.py only (it handles cwd correctly).

## browser
- Max 2 concurrent browser sessions to avoid memory exhaustion.
- Accept cookie consent dialogs before reading page content.
- Wait for the page to fully load before extracting text.
- Preferred source order:
  1. DOAJ: https://doaj.org/search#journals?query=<journal_name>
  2. Official publisher page (from Website column or Google search)
  3. Scimago: https://www.scimagojr.com/journalsearch.php?q=<journal_name>
  4. CrossRef: https://search.crossref.org/?q=<journal_name>
- Do NOT log into any site. Only use public/unauthenticated pages.
- If a page requires login or is paywalled, skip it.

## read / write
- Read files from:    /Users/tlatrille/Documents/journal-metadata-enrichment/agent/output/
- Write files to:     /Users/tlatrille/Documents/journal-metadata-enrichment/agent/output/
- NEVER write to:     /Users/tlatrille/Documents/journal-metadata-enrichment/WhereToPublish.github.io/
- Output CSV file:    AI_Suggestions.csv
- Gap report file:    gap_report.json
- Checkpoint prefix:  checkpoint_suggestions_<N>.csv (every 10 journals)

## Checkpointing
After processing every 10 journals, write the current accumulated suggestions to:
`/Users/tlatrille/Documents/journal-metadata-enrichment/agent/output/checkpoint_suggestions_<N>.csv`
where N is the checkpoint number (1, 2, 3 …). This prevents data loss if the session crashes.
The final AI_Suggestions.csv is the authoritative output.

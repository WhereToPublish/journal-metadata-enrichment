# Local Tool Conventions

## web_search / web_fetch
- Start with the provided lookup URLs and the known website via `web_fetch`.
- Use `web_search` only when additional discovery is needed and the search backend is available.
- Preferred source order: DOAJ, official journal or publisher page, Scimago, then other public sources such as CrossRef.
- Do NOT log into any site.
- If a page is blocked, paywalled, loops on redirects, or returns repeated 403/CAPTCHA responses, skip it and keep the journal unresolved.

## read / write
- Read runtime inputs, logs, and outputs under `/data/output/` when needed.
- In automated one-journal mode, return structured suggestion objects; the caller persists CSV and state files.
- Never mutate `Agent_suggestions.csv` directly unless the caller explicitly asks for file editing.
- Never write inside `WhereToPublish.github.io/`.

## Session Hygiene
- One session equals one journal.
- Do not carry partially researched journals across unrelated turns.
- Do not manage checkpoints yourself during caller-driven automation.

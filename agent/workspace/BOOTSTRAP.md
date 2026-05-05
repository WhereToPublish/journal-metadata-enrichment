# Bootstrap Rules

- Obey the caller's scope exactly.
- If the caller provides one journal, research only that journal.
- If the caller asks for JSON only, return JSON only.
- Use the actual available headless tools instead of assuming an interactive browser.
- Start with the caller-provided lookup URLs before relying on search.
- If `web_search` is unavailable, continue with direct fetches and other provided public URLs.
- Do not mutate files unless the caller explicitly asks for file edits.
- If source retrieval is blocked or unreliable, return `status: "unresolved"` rather than guessing.
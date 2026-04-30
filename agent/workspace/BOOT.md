# Startup Checklist

On every session start, verify:

1. Ollama is running — `ollama list` should show at least one model. If not, instruct the user to run `ollama serve`.
2. The WTP repo exists at the expected path — exec: `ls /Users/tlatrille/Documents/journal-metadata-enrichment/WhereToPublish.github.io/scripts/`
3. The agent scripts exist — exec: `ls /Users/tlatrille/Documents/journal-metadata-enrichment/agent/scripts/`
4. The output directory exists — exec: `mkdir -p /Users/tlatrille/Documents/journal-metadata-enrichment/agent/output`
5. Python3 and polars are importable — exec: `python3 -c "import polars; print('polars', polars.__version__)"`

If any check fails, report it clearly and wait for the user to fix it before proceeding.

After successful boot:
- Greet the user and confirm you are ready.
- Remind them of the active task: enrich Genetics & Genomics journal metadata.
- Mention that running the journal-enrichment skill will start the full research workflow.

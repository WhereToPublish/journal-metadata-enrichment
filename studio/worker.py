"""worker.py — self-hosted Mistral Studio worker entrypoint.

Run from the journal-metadata-enrichment project root (see run_workflow.sh):

    .venv/bin/python -m studio.worker

The worker connects outbound to the Mistral Workflows platform with
MISTRAL_API_KEY, auto-registers every workflow/activity found in the `studio`
package, and polls for executions. Executions are started from the Studio
website (console.mistral.ai → Workflows → Start Workflow) or via
trigger_workflow.py; they execute here, on this machine, where the
WhereToPublish repo and the Google credentials live.
"""

from __future__ import annotations

import asyncio

import mistralai.workflows as workflows


async def main() -> None:
    workflow_classes = workflows.discover_all_workflows_in_package("studio")
    if not workflow_classes:
        raise RuntimeError("no workflows discovered in the 'studio' package")
    print(f"Starting worker with workflows: {[c.__name__ for c in workflow_classes]}", flush=True)
    await workflows.run_worker(workflow_classes)


if __name__ == "__main__":
    asyncio.run(main())

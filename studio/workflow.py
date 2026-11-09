"""workflow.py — the wheretopublish-enrichment Mistral Studio workflow.

Registered in the Studio workspace by the worker (studio/worker.py) and started
from the Studio website (console.mistral.ai → Workflows → Start Workflow) or via
trigger_workflow.py. The workflow body only chains durable activities — all
side effects live in studio/activities.py, keeping the body deterministic.

Sheet safety: the workflow reads the spreadsheet (read-only scope) and writes
exactly one tab, Agent_suggestions (the AI-suggestions review tab), guarded by
config.ALLOWED_WRITE_TABS.
"""

from __future__ import annotations

from datetime import timedelta

import mistralai.workflows as workflows

# Imported modules are sandbox-safe at import time (no filesystem resolution at
# module level); all side effects live inside the activities.
from . import activities, config
from .config import EnrichmentInput, EnrichmentOutput


@workflows.workflow.define(
    name=config.WORKFLOW_NAME,
    workflow_display_name="WhereToPublish Enrichment",
    workflow_description=(
        "Pull the WhereToPublish Google Sheet (read-only), run the WTP process scripts, "
        "compute the metadata gap report, run the per-journal Mistral AI enrichment, and "
        "stage the validated suggestions in the Agent_suggestions review tab — the only "
        "tab this workflow ever writes."
    ),
    execution_timeout=timedelta(hours=12),
)
class WTPEnrichmentWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: EnrichmentInput) -> EnrichmentOutput:
        params = input.model_dump()
        steps: dict = {}

        steps["preflight"] = await activities.preflight_activity(params)

        if params.get("skip_download"):
            steps["pull_google_sheet"] = {"skipped": True}
        else:
            steps["pull_google_sheet"] = await activities.pull_google_sheet_activity(params)

        if params.get("skip_pipeline"):
            steps["run_process_scripts"] = {"skipped": True}
        else:
            steps["run_process_scripts"] = await activities.run_process_scripts_activity(params)

        steps["build_gap_report"] = await activities.build_gap_report_activity(params)
        steps["ai_enrichment"] = await activities.ai_enrichment_activity(params)

        write_tabs: list[str] = []
        if params.get("skip_upload"):
            steps["upload_suggestions"] = {"skipped": True}
        else:
            upload_result = await activities.upload_suggestions_activity(params)
            steps["upload_suggestions"] = upload_result
            write_tabs = list(upload_result.get("write_tabs", []))

        return EnrichmentOutput(
            workflow=config.WORKFLOW_NAME,
            steps=steps,
            write_tabs=write_tabs,
            summary=dict(steps.get("ai_enrichment", {})),
        )

#!/usr/bin/env python3
"""trigger_workflow.py — start an execution of the wheretopublish-enrichment
workflow from the terminal (same API the Studio website's Start Workflow button
uses: POST /v1/workflows/{name}/execute).

The execution runs on the worker started with run_workflow.sh. Examples:

    # smoke test: one journal, at most 3 suggestions, cheapest model
    .venv/bin/python trigger_workflow.py --journal "Human Genomics" --max-suggestions 3 --wait

    # default backlog run
    .venv/bin/python trigger_workflow.py --wait

    # trigger without waiting, then poll
    .venv/bin/python trigger_workflow.py --journal-limit 8
    .venv/bin/python trigger_workflow.py --execution-id <id> --status
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from studio.config import WORKFLOW_NAME, EnrichmentInput


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Trigger the '{WORKFLOW_NAME}' Mistral Studio workflow.")
    parser.add_argument("--journal", default="", help="Process only this one journal (exact name).")
    parser.add_argument("--journal-limit", type=int, default=0, help="Stop after this many journals (0 = unlimited).")
    parser.add_argument("--max-suggestions", type=int, default=15, help="Stop after this many valid suggestions.")
    parser.add_argument("--swarm-size", type=int, default=32, help="Journals per batch (1-32).")
    parser.add_argument("--model", default="ministral-3b-latest", help="Mistral model for the research agents.")
    parser.add_argument("--concurrency", type=int, default=4, help="Parallel journal agents.")
    parser.add_argument("--per-journal-timeout-seconds", type=float, default=600.0)
    parser.add_argument("--batch-timeout-seconds", type=int, default=3600)
    parser.add_argument("--wtp-dir", default="..", help="WhereToPublish repo root (relative to project root).")
    parser.add_argument("--skip-download", action="store_true", help="Reuse existing data_extracted/ CSVs.")
    parser.add_argument("--skip-pipeline", action="store_true", help="Skip the WTP process scripts.")
    parser.add_argument("--skip-upload", action="store_true", help="Do not write the Agent_suggestions tab.")
    parser.add_argument("--wait", action="store_true", help="Wait for the execution to finish and print its result.")
    parser.add_argument("--timeout-seconds", type=float, default=12 * 3600, help="Client-side wait timeout (with --wait).")
    parser.add_argument("--execution-id", default="", help="With --status: inspect this execution instead of starting one.")
    parser.add_argument("--status", action="store_true", help="Print the status/result of --execution-id.")
    args = parser.parse_args()

    api_key = os.environ.get("MISTRAL_STUDIO_API_KEY")
    if not api_key:
        print("ERROR: MISTRAL_STUDIO_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    from mistralai.client import Mistral

    client = Mistral(api_key=api_key)

    if args.status:
        if not args.execution_id:
            parser.error("--status requires --execution-id")
        run = client.workflows.executions.get_workflow_execution(execution_id=args.execution_id)
        dump = run.model_dump(mode="json", exclude_none=True)
        print(json.dumps(dump, indent=2, default=str)[:8000])
        return

    input_model = EnrichmentInput(
        wtp_dir=args.wtp_dir,
        journal=args.journal,
        journal_limit=args.journal_limit,
        max_suggestions=args.max_suggestions,
        swarm_size=args.swarm_size,
        skip_download=args.skip_download,
        skip_pipeline=args.skip_pipeline,
        skip_upload=args.skip_upload,
        model=args.model,
        concurrency=args.concurrency,
        per_journal_timeout_seconds=args.per_journal_timeout_seconds,
        batch_timeout_seconds=args.batch_timeout_seconds,
    )
    payload = input_model.model_dump()

    if args.wait:
        execution = client.workflows.execute_workflow_and_wait(
            workflow_identifier=WORKFLOW_NAME,
            input=payload,
            timeout_seconds=args.timeout_seconds,
        )
        print(f"execution finished: {getattr(execution, 'execution_id', '')}")
        result = getattr(execution, "result", None)
        print(json.dumps(result, indent=2, default=str)[:12000] if result is not None else "(no result)")
    else:
        execution = client.workflows.execute_workflow(
            workflow_identifier=WORKFLOW_NAME,
            input=payload,
            wait_for_result=False,
        )
        execution_id = getattr(execution, "execution_id", None)
        print(f"execution started: {execution_id}")
        print(f"watch it in Mistral Studio (console.mistral.ai → Workflows → {WORKFLOW_NAME} → Executions)")
        print(f"or poll: .venv/bin/python trigger_workflow.py --status --execution-id {execution_id}")


if __name__ == "__main__":
    main()

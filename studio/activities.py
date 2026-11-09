"""activities.py — the durable steps of the wheretopublish-enrichment workflow.

Each activity wraps one stage of the existing enrichment pipeline (reusing the
modules in agent/scripts/) so the CLI (run_agent.sh) and the Studio workflow
run exactly the same code:

1. preflight            — verify repo, external data, credentials, MISTRAL_API_KEY
2. pull_google_sheet    — download all data tabs (READ-ONLY on the spreadsheet)
3. run_process_scripts  — WTP pipeline: update_extracted.py + data_process.py
                          (writes only local files: data_extracted/, data/, logs/)
4. build_gap_report     — compute + persist agent/output/gap_report.json
5. ai_enrichment        — per-journal Mistral agents (smallest model by default),
                          validation, persistence to Agent_suggestions.csv,
                          deterministic ISSN alt-name pass
6. upload_suggestions    — push Agent_suggestions.csv to the Agent_suggestions tab.
                          HARD-GUARDED: this is the only spreadsheet write in the
                          whole workflow (see config.ALLOWED_WRITE_TABS).

All sheet reads use a readonly service; all heavy data stays on the worker's
disk and only small JSON summaries cross the activity boundary.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import mistralai.workflows as workflows

from . import config

# The activities can run for a long time (pipeline + AI research); the real
# budgets are enforced inside (batch timeout, per-journal timeout), these
# start-to-close timeouts are just generous upper bounds.
_TIMEOUT_SHORT = timedelta(seconds=900)
_TIMEOUT_PIPELINE = timedelta(hours=2)
_TIMEOUT_ENRICHMENT = timedelta(hours=8)


def _paths(params: dict[str, Any]) -> dict[str, Path]:
    wtp_dir_str = str(params.get("wtp_dir") or "..")
    
    # In managed mode, wtp_dir might be a special marker
    # If it's "..", try to resolve relative to project root
    # Otherwise use it as-is
    if wtp_dir_str == "..":
        wtp_dir = config.resolve_wtp_dir(wtp_dir_str)
    else:
        wtp_dir = Path(wtp_dir_str).expanduser()
        if not wtp_dir.is_absolute():
            wtp_dir = (config.PROJECT_ROOT / wtp_dir).resolve()
    
    defaults = config.default_output_paths()
    return {
        "wtp_dir": wtp_dir,
        "gap_report": defaults["gap_report"],
        "suggestions_csv": defaults["suggestions_csv"],
        "run_state": defaults["run_state"],
        "log_dir": defaults["log_dir"],
    }


@workflows.activity(start_to_close_timeout=timedelta(seconds=300))
async def preflight_activity(params: dict[str, Any]) -> dict[str, Any]:
    """Verify the worker environment before touching anything.
    
    In managed mode, this will also clone the WhereToPublish repo and download
    extraction data files if they're not already present.
    """
    import os
    import subprocess
    
    import gap_analysis

    paths = _paths(params)
    wtp_dir = paths["wtp_dir"]
    
    # In managed mode, try to clone the repo if it doesn't exist
    if not wtp_dir.exists():
        # Check if we're in managed mode by looking for deployment env vars
        managed_mode = os.environ.get("MANAGED_WORK_DIR") is not None
        repo_url = os.environ.get("WTP_REPO_URL", "https://github.com/WhereToPublish/WhereToPublish.github.io.git")
        # Use the 'data' branch which contains the processed data files
        repo_branch = os.environ.get("WTP_REPO_BRANCH", "data")
        
        if managed_mode:
            print(f"Managed mode: cloning WhereToPublish repo (branch: {repo_branch}) to {wtp_dir}...")
            wtp_dir.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["git", "clone", "--branch", repo_branch, "--depth", "1", repo_url, str(wtp_dir)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to clone WhereToPublish repo: {result.stderr}"
                )
            print(f"Cloned WhereToPublish repo (branch: {repo_branch}) to {wtp_dir}")
        else:
            raise RuntimeError(f"WTP repo not found at {wtp_dir}")

    # Ensure extraction data files are present
    if not wtp_dir.exists():
        raise RuntimeError(f"WTP repo not found at {wtp_dir}")
    
    missing_api_key = not os.environ.get("MISTRAL_API_KEY")
    credentials_path = config.resolve_credentials_path()
    credentials_ok = credentials_path is not None or Path(
        os.environ.get(
            "GOOGLE_SERVICE_ACCOUNT_KEY",
            str(Path.home() / ".config/wheretopublish/google_service_account.json"),
        )
    ).exists()

    try:
        await asyncio.to_thread(gap_analysis.verify_extraction_data, wtp_dir)
        extraction_ok = True
        extraction_error = ""
    except RuntimeError as exc:
        extraction_ok = False
        extraction_error = str(exc)
        
        # In managed mode, try to download extraction data
        if not extraction_ok and os.environ.get("MANAGED_WORK_DIR"):
            print("Managed mode: downloading extraction data files...")
            data_extraction_dir = wtp_dir / "data_extraction"
            data_extraction_dir.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["bash", "scripts/download_extraction.sh"],
                cwd=wtp_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                # Retry verification
                try:
                    await asyncio.to_thread(gap_analysis.verify_extraction_data, wtp_dir)
                    extraction_ok = True
                    extraction_error = ""
                except RuntimeError as exc2:
                    extraction_error = str(exc2)
            else:
                extraction_error = f"download_extraction.sh failed: {result.stderr}"

    problems = []
    if missing_api_key:
        problems.append("MISTRAL_API_KEY is not set on the worker")
    if not credentials_ok:
        problems.append(
            "no Google credentials: set GOOGLE_SERVICE_ACCOUNT_KEY or use the default key path"
        )
    if not extraction_ok:
        problems.append(extraction_error)
    if problems:
        raise RuntimeError("preflight failed: " + "; ".join(problems))

    return {
        "wtp_dir": str(wtp_dir),
        "credentials_source": str(credentials_path) if credentials_path else "default resolution (GOOGLE_SERVICE_ACCOUNT_KEY / default path)",
        "extraction_files": len(gap_analysis.REQUIRED_EXTRACTION_FILES),
    }


@workflows.activity(start_to_close_timeout=_TIMEOUT_SHORT)
async def pull_google_sheet_activity(params: dict[str, Any]) -> dict[str, Any]:
    """Download every data tab of the WhereToPublish spreadsheet (read-only)."""
    import gap_analysis

    paths = _paths(params)
    downloaded = await asyncio.to_thread(
        gap_analysis.download_all_sheets_csvs,
        paths["wtp_dir"],
        credentials_path=config.resolve_credentials_path(),
    )
    return {
        "tabs": downloaded,
        "tab_count": len(downloaded),
        "total_rows": sum(downloaded.values()),
        "mode": "read-only",
    }


@workflows.activity(start_to_close_timeout=_TIMEOUT_PIPELINE)
async def run_process_scripts_activity(params: dict[str, Any]) -> dict[str, Any]:
    """Run the WTP process scripts (update_extracted.py, data_process.py).

    These write only local files inside the WTP repo (data_extracted/, data/,
    logs/) — they never touch the Google Sheet.
    """
    import gap_analysis

    paths = _paths(params)
    await asyncio.to_thread(gap_analysis.run_pipeline, paths["wtp_dir"])
    return {
        "scripts": list(gap_analysis.PIPELINE_SCRIPTS),
        "wtp_dir": str(paths["wtp_dir"]),
        "side_effects": "local files only (no spreadsheet write)",
    }


@workflows.activity(start_to_close_timeout=_TIMEOUT_SHORT)
async def build_gap_report_activity(params: dict[str, Any]) -> dict[str, Any]:
    """Build the unified gap report and persist it to agent/output/gap_report.json."""
    import json

    import gap_analysis

    paths = _paths(params)
    report = await asyncio.to_thread(gap_analysis.build_gap_report, paths["wtp_dir"])
    paths["gap_report"].parent.mkdir(parents=True, exist_ok=True)
    paths["gap_report"].write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "tabs": report["tabs"],
        "total_journals": report["total_journals"],
        "journals_with_gaps": report["journals_with_gaps"],
        "total_gap_instances": report["total_gap_instances"],
        "priority_summary": report["priority_summary"],
        "field_gap_counts": report["field_gap_counts"],
        "gap_report": str(paths["gap_report"]),
    }


@workflows.activity(start_to_close_timeout=_TIMEOUT_ENRICHMENT)
async def ai_enrichment_activity(params: dict[str, Any]) -> dict[str, Any]:
    """Run the AI enrichment: one Mistral agent per journal (smallest model by
    default), validation, persistence to Agent_suggestions.csv, and the
    deterministic ISSN-based alt-name pass."""
    import run_enrichment

    paths = _paths(params)
    cfg = run_enrichment.EnrichmentConfig(
        runtime="mistral",
        mistral_model=str(params.get("model") or "ministral-3b-latest"),
        mistral_concurrency=int(params.get("concurrency") or 0),
        mistral_per_journal_timeout=float(params.get("per_journal_timeout_seconds") or 0),
        wtp_dir=paths["wtp_dir"],
        gap_report=paths["gap_report"],
        output=paths["suggestions_csv"],
        state=paths["run_state"],
        log_dir=paths["log_dir"],
        journal=str(params.get("journal") or ""),
        journal_limit=int(params.get("journal_limit") or 0),
        max_suggestions=int(params.get("max_suggestions") or 15),
        swarm_size=int(params.get("swarm_size") or 32),
        swarm_timeout_seconds=int(params.get("batch_timeout_seconds") or 3600),
        credentials=config.resolve_credentials_path(),
    )
    summary = await run_enrichment.run_enrichment_core_async(cfg)
    summary["runtime"] = "mistral"
    summary["model"] = cfg.mistral_model
    return summary


@workflows.activity(start_to_close_timeout=_TIMEOUT_SHORT)
async def upload_suggestions_activity(params: dict[str, Any]) -> dict[str, Any]:
    """Push Agent_suggestions.csv to the Agent_suggestions review tab.

    This is the ONLY spreadsheet write of the whole workflow; the tab name is
    guarded by config.assert_write_tab_allowed.
    """
    import review_common
    import upload_suggestions

    paths = _paths(params)
    tab_name = review_common.SUGGESTIONS_TAB
    config.assert_write_tab_allowed(tab_name)
    result = await asyncio.to_thread(
        upload_suggestions.upload,
        paths["suggestions_csv"],
        config.resolve_credentials_path(),
        paths["wtp_dir"] / "data_extracted",
        tab_name,
    )
    result["write_tabs"] = [tab_name] if result["rows_written"] else []
    return result

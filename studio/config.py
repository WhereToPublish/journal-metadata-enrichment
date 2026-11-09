"""config.py — paths, credentials, input/output models for the Studio workflow.

Google credentials are resolved from ``GOOGLE_SERVICE_ACCOUNT_KEY`` environment
variable (path to JSON file), or fallback to ``~/.config/wheretopublish/google_service_account.json``.
The service-account JSON never has to be committed or pasted into code.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# Make the agent scripts (run_enrichment, gap_analysis, suggestions_io, ...)
# importable no matter where the worker process was started from.
# NOTE: no Path.resolve()/absolute() at module level — this module is imported
# by the Temporal workflow sandbox, which forbids those calls at import time.
# Absolute paths are computed lazily inside the functions below (which run
# inside activities, outside the sandbox).
PROJECT_ROOT = Path(__file__).parent.parent
AGENT_SCRIPTS_DIR = PROJECT_ROOT / "agent" / "scripts"
if str(AGENT_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_SCRIPTS_DIR))

from enrichment_common import SUGGESTIONS_CSV_PATH  # noqa: E402

# Name of the workflow as registered in Mistral Studio.
WORKFLOW_NAME = "wheretopublish-enrichment"

# The ONLY spreadsheet tabs this workflow is allowed to write. Every sheet read
# is read-only; the single write path (upload_suggestions) is guarded by this.
ALLOWED_WRITE_TABS: tuple[str, ...] = ("Agent_suggestions",)

def resolve_credentials_path() -> Path | None:
    """Return the service-account key file path from GOOGLE_SERVICE_ACCOUNT_KEY env var,
    or None to use the default path ~/.config/wheretopublish/google_service_account.json."""
    return Path(os.environ.get("GOOGLE_SERVICE_ACCOUNT_KEY", "")).expanduser() if os.environ.get("GOOGLE_SERVICE_ACCOUNT_KEY") else None


def resolve_wtp_dir(wtp_dir: str) -> Path:
    """Resolve the WTP repo path from the workflow input (relative to the worker's project root)."""
    path = Path(wtp_dir).expanduser()
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def project_root_abs() -> Path:
    """Absolute project root (call from activities, not at import time)."""
    return PROJECT_ROOT.resolve()


class EnrichmentInput(BaseModel):
    """Input of the wheretopublish-enrichment workflow (JSON, passed from the Studio UI)."""

    wtp_dir: str = Field(
        default="..",
        description="WhereToPublish repo root, relative to the worker's project root (default: parent dir).",
    )
    journal: str = Field(default="", description="Process only this one journal (exact name). Empty = backlog order.")
    journal_limit: int = Field(default=0, ge=0, description="Stop after this many journals (0 = unlimited).")
    max_suggestions: int = Field(default=15, ge=1, description="Stop after this many valid suggestions.")
    swarm_size: int = Field(default=32, ge=1, le=32, description="Journals per batch (max 32).")
    skip_download: bool = Field(default=False, description="Reuse the existing data_extracted/ CSVs (no sheet pull).")
    skip_pipeline: bool = Field(default=False, description="Skip the WTP process scripts (update_extracted.py, data_process.py).")
    skip_upload: bool = Field(default=False, description="Do not push Agent_suggestions.csv to the sheet at the end.")
    model: str = Field(default="ministral-3b-latest", description="Mistral model for the per-journal research agents (smallest = cheapest).")
    concurrency: int = Field(default=4, ge=1, description="Parallel journal agents (Mistral API rate limits apply).")
    per_journal_timeout_seconds: float = Field(default=600, gt=0, description="Per-journal research budget.")
    batch_timeout_seconds: int = Field(default=3600, ge=1, description="Overall budget per batch of journals.")


class EnrichmentOutput(BaseModel):
    """Result of one workflow execution."""

    workflow: str
    steps: dict[str, Any]
    write_tabs: list[str]
    summary: dict[str, Any]


def default_output_paths() -> dict[str, Path]:
    return {
        "gap_report": PROJECT_ROOT / "agent" / "output" / "gap_report.json",
        "suggestions_csv": SUGGESTIONS_CSV_PATH,
        "run_state": PROJECT_ROOT / "agent" / "output" / "state" / "run_state.json",
        "log_dir": PROJECT_ROOT / "agent" / "output" / "logs",
    }


def assert_write_tab_allowed(tab_name: str) -> None:
    """Hard guard: the workflow may never write a tab outside ALLOWED_WRITE_TABS."""
    if tab_name not in ALLOWED_WRITE_TABS:
        raise RuntimeError(
            f"Refusing to write tab '{tab_name}': this workflow may only write {list(ALLOWED_WRITE_TABS)}."
        )

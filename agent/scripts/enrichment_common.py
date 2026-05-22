from __future__ import annotations

from pathlib import Path
import re


SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
AGENT_DIR = PROJECT_ROOT / "agent"
OUTPUT_DIR = AGENT_DIR / "output"
LOGS_DIR = OUTPUT_DIR / "logs"
STATE_DIR = OUTPUT_DIR / "state"
DEFAULT_WTP_DIR = PROJECT_ROOT / "WhereToPublish.github.io"
WTP_SCRIPTS_DIR = DEFAULT_WTP_DIR / "scripts"
GAP_REPORT_PATH = OUTPUT_DIR / "gap_report.json"
SUGGESTIONS_CSV_PATH = OUTPUT_DIR / "Agent_suggestions.csv"
RUN_STATE_PATH = STATE_DIR / "run_state.json"
GAP_ANALYSIS_SCRIPT = SCRIPT_DIR / "gap_analysis.py"

CSV_HEADERS = [
    "journal",
    "field",
    "current_value",
    "suggested_value",
    "confidence",
    "source_urls",
    "reasoning",
    "suggestion_type",
    "priority",
]

ALLOWED_FIELDS = {
    "Business model",
    "Publisher",
    "Country",
    "Website",
    "APC Euros",
    "Publisher type",
    "Institution",
    "Institution type",
    "Alternative journal name",
    "e-ISSN",
    "p-ISSN",
    "ISSN-L",
}

ALLOWED_PRIORITIES = {"high", "medium", "low"}
ALLOWED_SUGGESTION_TYPES = {"fill", "alt_name", "correct", "remove"}
ALLOWED_BUSINESS_MODELS = {"OA diamond", "OA", "Hybrid", "Subscription"}

DEFAULT_MODEL = "ollama/qwen2.5:14b-ctx128k"


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    return lowered.strip("-") or "journal"


def is_empty(value: object) -> bool:
    """True if value is None, empty string, or whitespace."""
    return value is None or str(value).strip() == ""
from __future__ import annotations

from pathlib import Path
import re


SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
AGENT_DIR = PROJECT_ROOT / "agent"
OUTPUT_DIR = AGENT_DIR / "output"
LOGS_DIR = OUTPUT_DIR / "logs"
STATE_DIR = OUTPUT_DIR / "state"

# This project lives inside the WhereToPublish repository, so the WTP repo
# root is simply the parent directory of the project root.
DEFAULT_WTP_DIR = PROJECT_ROOT.parent
WTP_SCRIPTS_DIR = DEFAULT_WTP_DIR / "scripts"

# OMP prompt files. system.md is appended to the orchestrator run's system prompt;
# each swarm subagent reads subagent.md from disk at runtime.
SYSTEM_PROMPT_PATH = AGENT_DIR / "prompt" / "system.md"
SUBAGENT_PROTOCOL_PATH = AGENT_DIR / "prompt" / "subagent.md"

GAP_REPORT_PATH = OUTPUT_DIR / "gap_report.json"
SUGGESTIONS_CSV_PATH = OUTPUT_DIR / "Agent_suggestions.csv"
RUN_STATE_PATH = STATE_DIR / "run_state.json"
GAP_ANALYSIS_SCRIPT = SCRIPT_DIR / "gap_analysis.py"
SWARM_DIR = OUTPUT_DIR / "swarm"

# Gap fields the LLM has never resolved in practice — journals with only these
# gaps are never sent to the swarm.
UNRESOLVABLE_FIELDS = {"Institution", "Institution type"}

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


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    return lowered.strip("-") or "journal"


def is_empty(value: object) -> bool:
    """True if value is None, empty string, or whitespace."""
    return value is None or str(value).strip() == ""

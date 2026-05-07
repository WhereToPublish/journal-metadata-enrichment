#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
import urllib.parse

from enrichment_common import (
    DEFAULT_WTP_DIR,
    GAP_ANALYSIS_SCRIPT,
    GAP_REPORT_PATH,
    LOGS_DIR,
    RUN_STATE_PATH,
    SUGGESTIONS_CSV_PATH,
    STATE_DIR,
    slugify,
)
from openclaw_runtime import OpenClawRunner
from suggestions_io import (
    append_suggestions,
    init_suggestions_csv,
    load_existing_keys,
    load_state,
    sanitize_agent_result,
    save_state,
    write_checkpoint,
)


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def run_gap_analysis(wtp_dir: Path, output_path: Path) -> None:
    command = [sys.executable, str(GAP_ANALYSIS_SCRIPT), "--wtp-dir", str(wtp_dir), "--output", str(output_path)]
    log("Running gap analysis ...")
    subprocess.run(command, check=True)


def load_gap_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_prompt(journal_gap: dict[str, Any]) -> str:
    encoded_name = urllib.parse.quote(journal_gap["name"])
    payload = {
        "journal": journal_gap["name"],
        "known_metadata": {
            "website": journal_gap.get("website", ""),
            "publisher": journal_gap.get("current_publisher", ""),
            "business_model": journal_gap.get("current_business_model", ""),
        },
        "lookup_urls": {
            "official_website": journal_gap.get("website", ""),
            "doaj_search": f"https://doaj.org/search/journals/{encoded_name}",
            "scimago_search": f"https://www.scimagojr.com/journalsearch.php?q={encoded_name}&tip=jou",
        },
        "requested_gaps": journal_gap["gaps"],
    }
    current_business_model = journal_gap.get("current_business_model", "")
    return (
        "Research exactly one journal.\n"
        "Use the available tools in this run, including web search and page fetch tools, to gather evidence.\n"
        "If web search is unavailable, use the provided lookup URLs directly with the fetch tool before falling back to other public sources.\n"
        "Prefer this source order when applicable: DOAJ, the official publisher or journal website, Scimago, then other public sources.\n"
        "Do not write or edit files in this run; return structured JSON only.\n"
        "Do not invent facts or URLs. Only cite URLs you actually visited during this run.\n"
        "Do not propose adding new journals. Work only on the provided journal and the requested gaps for that existing record.\n"
        "Only suggest values for the requested gaps. Do not include any suggestion whose suggested_value is empty.\n"
        "If a requested field cannot be supported by reliable evidence after a few attempts, leave it unresolved instead of guessing.\n"
        "Use these business model values only: OA diamond, OA, Hybrid, Subscription.\n"
        "Institution type must be a category label, never the institution name itself.\n"
        "Use only these institution type labels when supported by the evidence: Society, Society/Association, University, Research Institute.\n"
        "Do not infer Institution or Institution type from a commercial publisher name alone. If the source only identifies a publisher and not a sponsoring institution, leave Institution and Institution type unresolved.\n"
        "Do not use a publisher company name as Institution or Institution type unless the evidence explicitly identifies it as the institution.\n"
        "APC Euros must be a plain integer string with no currency symbol.\n"
        "Never output APC Euros as 0 unless the evidence explicitly states that there is no APC or that the APC is zero (e.g. 'no APC', 'free to publish', 'APC is 0').\n"
        + (
            "The known business model for this journal is 'Subscription'. Do NOT suggest APC Euros = 0 for a Subscription journal.\n"
            if current_business_model == "Subscription"
            else ""
        )
        + "For the Scimago Journal Title field, always use suggestion_type 'alt_name', never 'fill', even when the current value is empty.\n"
        "Each suggestion must have confidence between 0 and 1, and you should only return suggestions with confidence >= 0.55.\n"
        "You MUST always return a single JSON object, even if all sources are blocked or unavailable. "
        "If the evidence is insufficient for all requested gaps, return: "
        '{\"journal\": \"<name>\", \"suggestions\": [], \"status\": \"unresolved\", \"notes\": \"<brief reason>\"}\n'
        "Return exactly one JSON object with this schema and nothing else:\n"
        "{\n"
        '  "journal": string,\n'
        '  "suggestions": [{"field": string, "current_value": string, "suggested_value": string, "confidence": number, "source_urls": string[], "reasoning": string, "suggestion_type": string, "priority": string}],\n'
        '  "status": "ok" | "unresolved",\n'
        '  "notes": string\n'
        "}\n\n"
        f"Evidence:\n{json.dumps(payload, indent=2, ensure_ascii=False)}"
    )


PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def journal_max_priority(journal_gap: dict[str, Any]) -> int:
    """Return the sort key for a journal: lowest integer = highest priority."""
    best = min(
        (PRIORITY_ORDER.get(gap["priority"], 99) for gap in journal_gap["gaps"]),
        default=99,
    )
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description="Run JournalMind enrichment one journal at a time.")
    parser.add_argument("--wtp-dir", type=Path, default=DEFAULT_WTP_DIR)
    parser.add_argument("--gap-report", type=Path, default=GAP_REPORT_PATH)
    parser.add_argument("--output", type=Path, default=SUGGESTIONS_CSV_PATH)
    parser.add_argument("--state", type=Path, default=RUN_STATE_PATH)
    parser.add_argument("--log-dir", type=Path, default=LOGS_DIR)
    parser.add_argument("--skip-gap-analysis", action="store_true")
    parser.add_argument("--journal", default="", help="Only process a single journal by exact name.")
    parser.add_argument("--max-suggestions", type=int, default=15,
                        help="Stop after this many valid suggestions are written (default: 15).")
    parser.add_argument("--local", action="store_true", help="Run the embedded agent instead of the gateway agent.")
    args = parser.parse_args()

    init_suggestions_csv(args.output)
    state = load_state(args.state)
    processed_names = {entry["journal"] for entry in state.get("processed_journals", [])}
    existing_keys = load_existing_keys(args.output)

    if not args.skip_gap_analysis:
        run_gap_analysis(args.wtp_dir, args.gap_report)
    else:
        log("Skipping gap analysis (--skip-gap-analysis set)")

    report = load_gap_report(args.gap_report)
    runner = OpenClawRunner(args.log_dir, local=args.local)

    # Sort all journals by their highest-priority gap (high → medium → low).
    journals = sorted(report["journals"], key=journal_max_priority)
    if args.journal:
        journals = [j for j in journals if j["name"] == args.journal]

    written_suggestions = 0
    processed_count = 0
    log(f"Processing journals sorted by priority (max-suggestions={args.max_suggestions})")

    for journal_gap in journals:
        if journal_gap["name"] in processed_names:
            continue
        if written_suggestions >= args.max_suggestions:
            break

        processed_count += 1
        session_id = f"enrichment-{datetime.now().strftime('%Y%m%d%H%M%S')}-{slugify(journal_gap['name'])}"
        log(f"[{processed_count}] {journal_gap['name']}")
        prompt = build_prompt(journal_gap)
        run_result = runner.run(session_id=session_id, prompt=prompt)

        status, cleaned_rows, notes = sanitize_agent_result(
            journal_name=journal_gap["name"],
            journal_gap=journal_gap,
            agent_result=run_result.parsed_payload,
            existing_keys=existing_keys,
        )

        append_suggestions(args.output, cleaned_rows)
        for row in cleaned_rows:
            existing_keys.add((row["journal"], row["field"], row["suggested_value"]))
        written_suggestions += len(cleaned_rows)

        state.setdefault("processed_journals", []).append(
            {
                "journal": journal_gap["name"],
                "status": status,
                "notes": notes,
                "session_id": session_id,
                "suggestions_written": len(cleaned_rows),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )
        state.setdefault("summary", {"written": 0, "unresolved": 0, "errors": 0})
        if status == "ok":
            state["summary"]["written"] += len(cleaned_rows)
        elif status == "unresolved":
            state["summary"]["unresolved"] += 1
        else:
            state["summary"]["errors"] += 1
        save_state(args.state, state)

        if processed_count % 10 == 0:
            checkpoint_path = STATE_DIR / f"checkpoint_suggestions_{processed_count // 10}.csv"
            write_checkpoint(args.output, checkpoint_path)

        log(f"  status={status} suggestions={len(cleaned_rows)} notes={notes or '-'}")

    log("Run complete")
    log(f"  suggestions written : {written_suggestions}")
    log(f"  processed journals  : {processed_count}")
    log(f"  output              : {args.output}")
    log(f"  state               : {args.state}")


if __name__ == "__main__":
    main()
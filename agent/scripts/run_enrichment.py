#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import urllib.parse

# Resolve the WTP scripts directory before importing sheets_client so we always
# use the canonical implementation in WhereToPublish.github.io/scripts/ rather
# than a duplicate copy inside the agent folder.
WTP_SCRIPTS = Path(__file__).parent.parent.parent / "WhereToPublish.github.io" / "scripts"
if str(WTP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WTP_SCRIPTS))
import sheets_client
from openclaw_runtime import OpenClawRunner
from suggestions_io import *


def load_suggestion_keys_from_tabs(service: Any, tab_names: list[str],
                                   spreadsheet_id: str = sheets_client.SPREADSHEET_ID) -> set[tuple[str, str, str]]:
    """Read (journal, field, suggested_value) triples from the given sheet tabs.

    Used before enrichment runs to avoid re-suggesting values already present in
    Agent_suggestions or Agent_suggestions_processed.  Missing or empty tabs are silently
    skipped so the caller degrades gracefully when those tabs do not yet exist.

    Args:
        service: Authenticated Sheets API service (readonly is sufficient).
        tab_names: List of tab names to read from.
        spreadsheet_id: Google Sheets spreadsheet ID (default: WhereToPublish).

    Returns:
        A set of (journal, field, suggested_value) tuples.
    """
    keys: set[tuple[str, str, str]] = set()
    for tab_name in tab_names:
        try:
            result = (
                service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=spreadsheet_id,
                    range=tab_name,
                    valueRenderOption="FORMATTED_VALUE",
                )
                .execute()
            )
        except Exception:
            # Tab does not exist or API error — skip gracefully
            continue

        rows = result.get("values", [])
        if len(rows) < 2:
            continue

        header = rows[0]
        try:
            journal_col = header.index("journal")
            field_col = header.index("field")
            value_col = header.index("suggested_value")
        except ValueError:
            # Header structure not recognised — skip this tab
            continue

        for row in rows[1:]:
            # Pad short rows
            padded = row + [""] * max(0, max(journal_col, field_col, value_col) + 1 - len(row))
            journal = padded[journal_col].strip()
            field = padded[field_col].strip()
            suggested_value = padded[value_col].strip()
            if journal and field and suggested_value:
                keys.add((journal, field, suggested_value))

    return keys


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
    e_issn = journal_gap.get("e_issn", "")
    p_issn = journal_gap.get("p_issn", "")
    issn_l = journal_gap.get("issn_l", "")
    # Use the most useful ISSN for a DOAJ direct lookup (e-ISSN preferred, then p-ISSN, then ISSN-L)
    doaj_issn = e_issn or p_issn or issn_l
    # Use the most useful ISSN for Scimago direct lookup (same preference order)
    scimago_issn = e_issn or p_issn or issn_l
    payload = {
        "journal": journal_gap["name"],
        "known_metadata": {
            "website": journal_gap.get("website", ""),
            "publisher": journal_gap.get("current_publisher", ""),
            "business_model": journal_gap.get("current_business_model", ""),
            "e_issn": e_issn,
            "p_issn": p_issn,
            "issn_l": issn_l,
        },
        "lookup_urls": {
            "official_website": journal_gap.get("website", ""),
            "doaj_search": f"https://doaj.org/search/journals/{encoded_name}",
            "doaj_by_issn": f"https://doaj.org/toc/{doaj_issn}" if doaj_issn else "",
            "scimago_search": f"https://www.scimagojr.com/journalsearch.php?q={encoded_name}&tip=jou",
            "scimago_by_issn": f"https://www.scimagojr.com/journalsearch.php?q={scimago_issn}&tip=issn" if scimago_issn else "",
        },
        "requested_gaps": journal_gap["gaps"],
    }
    current_business_model = journal_gap.get("current_business_model", "")
    has_issn = bool(journal_gap.get("e_issn") or journal_gap.get("p_issn") or journal_gap.get("issn_l"))
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
            "Business model REQUIRES hard evidence: you must find explicit text on the journal page or DOAJ confirming the model "
            "(e.g. 'This journal is open access', 'Subscription only', 'APC: $X'). "
            "Do NOT infer the business model from the publisher's reputation or general knowledge. "
            "If the journal page and DOAJ do not explicitly confirm the business model, leave it unresolved.\n"
            "Institution type must be a category label, never the institution name itself.\n"
            "Use only these institution type labels when supported by the evidence: Society, Society/Association, University, Research Institute.\n"
            "Do not infer Institution or Institution type from a commercial publisher name alone. If the source only identifies a publisher and not a sponsoring institution, leave Institution and Institution type unresolved.\n"
            "Do not use a publisher company name as Institution or Institution type unless the evidence explicitly identifies it as the institution.\n"
            "APC Euros must be a plain integer string with no currency symbol.\n"
            "A website that does not mention APCs is NOT evidence that APC = 0. Absence of mention means the source is insufficient — leave APC Euros unresolved.\n"
            "Only suggest APC Euros = 0 when the source explicitly states there is no charge (e.g. 'no APC', 'free to publish', 'APC is 0', 'does not charge').\n"
            "If an APC is listed in a non-Euro currency (GBP, USD, etc.), convert it using approximate current exchange rates, or leave it unresolved if uncertain.\n"
            + (
                "The known business model for this journal is 'Subscription'. Do NOT suggest APC Euros = 0 for a Subscription journal.\n"
                if current_business_model == "Subscription"
                else ""
            )
            + "For the Alternative journal name field, always use suggestion_type 'alt_name', never 'fill', even when the current value is empty.\n"
            + (
                "An ISSN is available for this journal. To find the Alternative journal name: "
                "first fetch lookup_urls.scimago_by_issn (Scimago search by ISSN) — the result title is the exact Scimago name. "
                "Also try lookup_urls.doaj_by_issn. "
                "Do NOT infer the Alternative journal name from topic keywords — only use names you find in Scimago or DOAJ.\n"
                if has_issn
                else
                "No ISSN is available. To find the Alternative journal name: search Scimago and DOAJ by journal name. "
                "Only suggest a name if you find an unambiguous match (same publisher, same scope). "
                "Do NOT infer from topic keywords.\n"
            )
            + "Alternative journal name suggestions require confidence >= 0.70; suggestions below that threshold will be rejected.\n"
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
    parser.add_argument("--max-suggestions", type=int, default=125,
                        help="Stop after this many valid suggestions are written (default: 15).")
    parser.add_argument("--local", action="store_true", help="Run the embedded agent instead of the gateway agent.")
    parser.add_argument(
        "--credentials",
        type=Path,
        default=None,
        help="Path to service-account JSON key (default: GOOGLE_SERVICE_ACCOUNT_KEY env var or default path).",
    )
    args = parser.parse_args()

    init_suggestions_csv(args.output)
    state = load_state(args.state)
    processed_names = {entry["journal"] for entry in state.get("processed_journals", [])}
    existing_keys = load_existing_keys(args.output)

    # Merge remote keys from Google Sheets to avoid re-suggesting values already
    # present in Agent_suggestions or Agent_suggestions_processed.
    try:
        remote_service = sheets_client.get_sheets_service(
            credentials_path=args.credentials, readonly=True
        )
        remote_keys = load_suggestion_keys_from_tabs(
            remote_service,
            ["Agent_suggestions", "Agent_suggestions_processed"],
        )
        existing_keys |= remote_keys
        log(f"Loaded {len(remote_keys)} existing key(s) from remote sheet tabs.")
    except Exception as exc:
        log(f"WARNING: could not load remote suggestion keys: {exc} — deduplication will use local CSV only.")

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

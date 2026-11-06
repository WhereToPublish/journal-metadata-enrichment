#!/usr/bin/env python3
"""run_enrichment.py — swarm orchestrator for journal metadata enrichment.

The runner no longer pre-fetches journal data or prompts one OMP agent per
journal.  Instead, for each batch of journals it:

1. writes a mission directory under agent/output/swarm/<batch_id>/ containing
   one input file per journal (name, known metadata, requested gaps, API
   lookup URLs) and an empty results/ directory,
2. starts ONE non-interactive OMP run.  That orchestrator agent spawns one
   parallel subagent per journal (via OMP's built-in `task` tool, max 32
   concurrent); each subagent does the full research for its journal —
   DOAJ / OpenAlex / CrossRef API fetches included — and writes its result
   JSON into the mission results/ directory,
3. reads the result files, validates every suggestion through
   suggestions_io.sanitize_agent_result, and persists accepted rows to
   Agent_suggestions.csv with the usual deduplication.

Journals whose only gaps are Institution / Institution type (fields the LLM
has never resolved in practice) are never sent to the swarm.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from enrichment_common import (
    DEFAULT_WTP_DIR,
    GAP_ANALYSIS_SCRIPT,
    GAP_REPORT_PATH,
    LOGS_DIR,
    RUN_STATE_PATH,
    STATE_DIR,
    SUBAGENT_PROTOCOL_PATH,
    SUGGESTIONS_CSV_PATH,
    SWARM_DIR,
    UNRESOLVABLE_FIELDS,
    WTP_SCRIPTS_DIR,
    slugify,
)

if str(WTP_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(WTP_SCRIPTS_DIR))
import sheets_client
from omp_runtime import DEFAULT_SWARM_TIMEOUT_SECONDS, OMPSwarmRunner
from issn_alt_name_suggestions import append_deterministic_suggestions
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


# ---------------------------------------------------------------------------
# Remote deduplication keys
# ---------------------------------------------------------------------------

def load_suggestion_keys_from_tabs(service: Any, tab_names: list[str],
                                   spreadsheet_id: str = sheets_client.SPREADSHEET_ID) -> set[tuple[str, str, str]]:
    """Read (journal, field, suggested_value) triples from the given sheet tabs.

    Used before enrichment runs to avoid re-suggesting values already present in
    Agent_suggestions or Agent_suggestions_processed.  Missing or unrecognised
    tabs are silently skipped so the caller degrades gracefully when those tabs
    do not yet exist.
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
            continue

        for row in rows[1:]:
            padded = row + [""] * max(0, max(journal_col, field_col, value_col) + 1 - len(row))
            journal = padded[journal_col].strip()
            field = padded[field_col].strip()
            suggested_value = padded[value_col].strip()
            if journal and field and suggested_value:
                keys.add((journal, field, suggested_value))

    return keys


# ---------------------------------------------------------------------------
# Journal selection
# ---------------------------------------------------------------------------

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def journal_max_priority(journal_gap: dict[str, Any]) -> int:
    """Return the sort key for a journal: lowest integer = highest priority."""
    return min(
        (PRIORITY_ORDER.get(gap["priority"], 99) for gap in journal_gap["gaps"]),
        default=99,
    )


def is_unresolvable_only(journal_gap: dict[str, Any]) -> bool:
    """True when a journal's gaps are all fields the LLM has never resolved."""
    return {gap["field"] for gap in journal_gap["gaps"]}.issubset(UNRESOLVABLE_FIELDS)


# ---------------------------------------------------------------------------
# Mission construction
# ---------------------------------------------------------------------------

def build_api_lookup_urls(journal_gap: dict[str, Any]) -> dict[str, str]:
    """Pre-build the public API lookup URLs for one journal (agents fetch them)."""
    name = journal_gap["name"]
    e_issn = journal_gap.get("e_issn", "")
    p_issn = journal_gap.get("p_issn", "")
    issn_l = journal_gap.get("issn_l", "")
    # e-ISSN preferred, then p-ISSN, then ISSN-L.
    primary_issn = e_issn or p_issn or issn_l
    return {
        "doaj_api_by_issn": (
            f"https://doaj.org/api/v3/search/journals/issn:{urllib.parse.quote(primary_issn)}"
            if primary_issn else ""
        ),
        "doaj_api_by_title": (
            f"https://doaj.org/api/v3/search/journals/title:{urllib.parse.quote(name)}"
        ),
        "openalex_api_by_issn": (
            "https://api.openalex.org/sources?filter=issn:"
            + urllib.parse.quote(primary_issn)
            + "&select=display_name,issn,issn_l,host_organization_name,is_oa,apc_usd,apc_prices,type"
            if primary_issn else ""
        ),
        "crossref_journals_by_title": (
            "https://api.crossref.org/journals?query="
            + urllib.parse.quote_plus(name) + "&rows=5"
        ),
        "scimago_by_issn": (
            f"https://www.scimagojr.com/journalsearch.php?q={primary_issn}&tip=issn"
            if primary_issn else ""
        ),
        "scimago_search": (
            f"https://www.scimagojr.com/journalsearch.php?q={urllib.parse.quote(name)}&tip=jou"
        ),
    }


def build_subagent_prompt(input_file: Path, result_file: Path) -> str:
    return (
        "Research exactly one academic journal and write your findings to a result file.\n"
        f"(1) Read your journal input file: {input_file}\n"
        f"(2) Read the research protocol and follow all of its rules exactly: {SUBAGENT_PROTOCOL_PATH}\n"
        "The input file contains the journal name, known metadata, the requested gaps, "
        "API lookup URLs, and the result_file path where you must write your result JSON.\n"
        "The protocol tells you how to gather evidence (public APIs first, then web) and "
        "exactly which JSON schema to write. Validate that your final result JSON file "
        "parses before finishing. Do not write any other file.\n"
        "Reply with one short line summarizing your outcome."
    )


def write_mission(batch_id: str, journals: list[dict[str, Any]]) -> Path:
    """Write the mission directory for one batch of journals.

    Layout:
        agent/output/swarm/<batch_id>/mission.json        — manifest for the orchestrator
        agent/output/swarm/<batch_id>/inputs/<idx>.json   — one per journal
        agent/output/swarm/<batch_id>/results/            — subagents write here
    """
    mission_dir = SWARM_DIR / batch_id
    inputs_dir = mission_dir / "inputs"
    results_dir = mission_dir / "results"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    manifest_journals: list[dict[str, Any]] = []
    for index, journal_gap in enumerate(journals):
        slug = slugify(journal_gap["name"])
        stem = f"{index:02d}-{slug}"
        input_file = inputs_dir / f"{stem}.json"
        result_file = results_dir / f"{stem}.json"

        input_payload = {
            "journal": journal_gap["name"],
            "tab": journal_gap.get("tab", ""),
            "known_metadata": {
                "website": journal_gap.get("website", ""),
                "publisher": journal_gap.get("current_publisher", ""),
                "business_model": journal_gap.get("current_business_model", ""),
                "e_issn": journal_gap.get("e_issn", ""),
                "p_issn": journal_gap.get("p_issn", ""),
                "issn_l": journal_gap.get("issn_l", ""),
            },
            "requested_gaps": journal_gap["gaps"],
            "api_lookup_urls": build_api_lookup_urls(journal_gap),
            "result_file": str(result_file),
        }
        input_file.write_text(json.dumps(input_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        manifest_journals.append({
            "index": index,
            "journal": journal_gap["name"],
            "input_file": str(input_file),
            "result_file": str(result_file),
            "subagent_prompt": build_subagent_prompt(input_file, result_file),
        })

    manifest = {
        "batch_id": batch_id,
        "subagent_protocol": str(SUBAGENT_PROTOCOL_PATH),
        "journals": manifest_journals,
    }
    manifest_path = mission_dir / "mission.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return mission_dir


def build_orchestrator_prompt(mission_dir: Path) -> str:
    manifest_path = mission_dir / "mission.json"
    results_dir = mission_dir / "results"
    return (
        "Execute this batch enrichment mission.\n"
        f"Mission manifest: {manifest_path}\n"
        "Read the manifest. It lists journals, each with an input_file, a result_file, and a "
        "ready-made subagent_prompt.\n"
        "1. Spawn one subagent per journal with your `task` tool, passing each journal's "
        "subagent_prompt VERBATIM. Never shorten, translate, or rewrite it. To avoid overly "
        "long tool calls, spawn the subagents in CONSECUTIVE `task` calls of 8 journals at a "
        "time (last call may have fewer), issuing the calls back to back WITHOUT waiting for "
        "results in between. All subagents run in parallel this way.\n"
        f"2. When all subagents finish, list {results_dir} and check that every result_file "
        "exists. For each missing result_file, spawn ONE replacement subagent with the same "
        "subagent_prompt.\n"
        "3. Do not research journals yourself. Do not read, edit, or summarize the contents of "
        "result files. Do not modify any repository files.\n"
        "4. Reply with exactly one line: DONE <n>/<total> where n is the number of existing "
        "result files and total is the number of journals in the manifest.\n"
    )


# ---------------------------------------------------------------------------
# Result collection
# ---------------------------------------------------------------------------

def parse_result_file(result_file: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def harvest_previous_results(
    report_journals: list[dict[str, Any]],
    processed_names: set[str],
    existing_keys: set[tuple[str, str, str]],
    state: dict[str, Any],
    state_path: Path,
    output: Path,
    runner_session_note: str = "previous swarm mission",
) -> tuple[int, set[tuple[str, str, str]]]:
    """Persist suggestions from result files left over by earlier swarm missions.

    An OMP swarm's worker daemon keeps finishing in-flight subagents after the
    orchestrator run has returned, so result files can land after the runner
    already collected the batch (or after a run was interrupted).  This sweep
    walks every existing mission's results directory and persists any journal
    that is not already processed by this or a previous run.  Returns the number
    of journals harvested.
    """
    if not SWARM_DIR.exists():
        return 0, existing_keys

    journals_by_name = {j["name"]: j for j in report_journals}
    harvested = 0
    for result_file in sorted(SWARM_DIR.glob("*/results/*.json")):
        payload = parse_result_file(result_file)
        if payload is None:
            continue
        journal_name = str(payload.get("journal", "")).strip()
        journal_gap = journals_by_name.get(journal_name)
        if not journal_name or journal_gap is None or journal_name in processed_names:
            continue

        status, rows, notes = sanitize_agent_result(
            journal_name=journal_name,
            journal_gap=journal_gap,
            agent_result=payload,
            existing_keys=existing_keys,
        )
        append_suggestions(output, rows)
        for row in rows:
            existing_keys.add((row["journal"], row["field"], row["suggested_value"]))
        record_journal(
            state, state_path, journal_name,
            status=status,
            notes=notes,
            session_id=f"harvest:{result_file.parent.parent.name}",
            suggestions_written=len(rows),
        )
        # Re-mark the journal as processed for this run's bookkeeping.
        processed_names.add(journal_name)
        harvested += 1
        log(f"  harvested {result_file.parent.parent.name} result for {journal_name}: "
            f"status={status} suggestions={len(rows)} ({runner_session_note})")
    return harvested, existing_keys


def record_journal(state: dict[str, Any], state_path: Path, journal_name: str,
                   status: str, notes: str, session_id: str,
                   suggestions_written: int) -> None:
    """Append a processed-journal entry and update the run-state summary."""
    state.setdefault("processed_journals", []).append(
        {
            "journal": journal_name,
            "status": status,
            "notes": notes,
            "session_id": session_id,
            "suggestions_written": suggestions_written,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
    )
    state.setdefault("summary", {"written": 0, "unresolved": 0, "errors": 0})
    if status == "ok":
        state["summary"]["written"] += suggestions_written
    elif status == "unresolved":
        state["summary"]["unresolved"] += 1
    else:
        state["summary"]["errors"] += 1
    save_state(state_path, state)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_gap_analysis(wtp_dir: Path, output_path: Path) -> None:
    command = [sys.executable, str(GAP_ANALYSIS_SCRIPT), "--wtp-dir", str(wtp_dir), "--output", str(output_path)]
    log("Running gap analysis ...")
    subprocess.run(command, check=True)


def load_gap_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run JournalMind enrichment as a swarm of OMP subagents.")
    parser.add_argument("--wtp-dir", type=Path, default=DEFAULT_WTP_DIR)
    parser.add_argument("--gap-report", type=Path, default=GAP_REPORT_PATH)
    parser.add_argument("--output", type=Path, default=SUGGESTIONS_CSV_PATH)
    parser.add_argument("--state", type=Path, default=RUN_STATE_PATH)
    parser.add_argument("--log-dir", type=Path, default=LOGS_DIR)
    parser.add_argument("--skip-gap-analysis", action="store_true")
    parser.add_argument("--journal", default="", help="Only process a single journal by exact name.")
    parser.add_argument("--journal-limit", type=int, default=0,
                        help="Stop after this many journals are sent to the swarm (0 = unlimited).")
    parser.add_argument("--max-suggestions", type=int, default=15,
                        help="Stop after this many valid suggestions are written (default: 15).")
    parser.add_argument("--swarm-size", type=int, default=32,
                        help="Journals per OMP swarm run — max 32 parallel subagents (default: 32).")
    parser.add_argument("--swarm-timeout-seconds", type=int, default=DEFAULT_SWARM_TIMEOUT_SECONDS,
                        help="Timeout for each OMP swarm run (default: 1800).")
    parser.add_argument("--credentials", type=Path, default=None,
                        help="Path to service-account JSON key (default: GOOGLE_SERVICE_ACCOUNT_KEY env var or default path).")
    args = parser.parse_args()

    if args.swarm_timeout_seconds < 1:
        parser.error("--swarm-timeout-seconds must be a positive integer")
    if not 1 <= args.swarm_size <= 32:
        parser.error("--swarm-size must be between 1 and 32 (OMP runs at most 32 concurrent subagents)")
    if args.skip_gap_analysis and not args.gap_report.exists():
        parser.error(f"--skip-gap-analysis requires an existing gap report at {args.gap_report}")

    init_suggestions_csv(args.output)
    state = load_state(args.state)
    processed_names = {entry["journal"] for entry in state.get("processed_journals", [])}
    existing_keys = load_existing_keys(args.output)

    # Merge remote keys from Google Sheets so we never re-suggest values already
    # under review or already processed.
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

    # Harvest result files that earlier missions wrote but earlier runs never
    # collected (late-finishing subagents, interrupted runs).
    harvested, existing_keys = harvest_previous_results(
        report_journals=report["journals"],
        processed_names=processed_names,
        existing_keys=existing_keys,
        state=state,
        state_path=args.state,
        output=args.output,
    )
    if harvested:
        log(f"Harvested {harvested} leftover result file(s) from previous swarm missions.")

    journals = sorted(report["journals"], key=journal_max_priority)
    if args.journal:
        journals = [j for j in journals if j["name"] == args.journal]

    pending = [j for j in journals if j["name"] not in processed_names]
    skipped_unresolvable = [j for j in pending if is_unresolvable_only(j)]
    eligible = [j for j in pending if not is_unresolvable_only(j)]
    log(
        f"Backlog: {len(journals)} journal(s) with gaps; {len(skipped_unresolvable)} skipped "
        f"(only Institution/type gaps — never sent to the swarm); {len(eligible)} eligible."
    )

    # Record skipped journals so subsequent runs do not rescan them.
    for journal_gap in skipped_unresolvable:
        record_journal(
            state, args.state, journal_gap["name"],
            status="skipped",
            notes="Only Institution / Institution type gaps — never sent to the swarm.",
            session_id="",
            suggestions_written=0,
        )

    runner = OMPSwarmRunner(args.log_dir, timeout_seconds=args.swarm_timeout_seconds)
    written_suggestions = 0
    processed_count = 0
    batch_number = 0
    pending_collections: list[tuple[str, dict[str, Any], Path]] = []

    # Batch the eligible backlog, highest gap priority first, until a budget runs out.
    start = 0
    while start < len(eligible):
        if written_suggestions >= args.max_suggestions:
            break
        if args.journal_limit and processed_count >= args.journal_limit:
            break

        remaining = (
            args.journal_limit - processed_count if args.journal_limit else len(eligible)
        )
        batch = eligible[start:start + min(args.swarm_size, remaining)]
        start += len(batch)
        batch_number += 1
        batch_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-batch{batch_number}"
        session_id = f"swarm-{batch_id}"

        mission_dir = write_mission(batch_id, batch)
        log(f"Batch {batch_number}: dispatching {len(batch)} journal(s) to the OMP swarm -> logs/{session_id}.*")
        run_result = runner.run(session_id=session_id, prompt=build_orchestrator_prompt(mission_dir))
        if run_result.timed_out:
            log(f"WARNING: swarm run timed out; collecting whatever result files exist.")
        log(f"Orchestrator reply: {run_result.final_message.strip() or '(no final message)'}")

        batch_rows_total = 0
        for index, journal_gap in enumerate(batch):
            journal_name = journal_gap["name"]
            processed_count += 1
            result_file = mission_dir / "results" / f"{index:02d}-{slugify(journal_name)}.json"
            payload = parse_result_file(result_file) if result_file.exists() else None

            if payload is None:
                # Missing/unparseable result: leave the journal out of the state so
                # the end-of-run sweep (or a later run) can still harvest a late file.
                log(f"  {journal_name}: no result file yet — pending late-result sweep")
                pending_collections.append((session_id, journal_gap, result_file))
                continue

            status, rows, notes = sanitize_agent_result(
                    journal_name=journal_name,
                    journal_gap=journal_gap,
                    agent_result=payload,
                    existing_keys=existing_keys,
                )

            append_suggestions(args.output, rows)
            for row in rows:
                existing_keys.add((row["journal"], row["field"], row["suggested_value"]))
            written_suggestions += len(rows)
            batch_rows_total += len(rows)

            record_journal(
                state, args.state, journal_name,
                status=status,
                notes=notes,
                session_id=session_id,
                suggestions_written=len(rows),
            )
            log(f"  {journal_name}: status={status} suggestions={len(rows)} notes={notes or '-'}")

        checkpoint_path = STATE_DIR / f"checkpoint_suggestions_{batch_number}.csv"
        write_checkpoint(args.output, checkpoint_path)
        log(f"Batch {batch_number} complete: {len(batch)} journal(s), {batch_rows_total} suggestion(s) written.")

    # Late-result sweep: OMP's worker daemon keeps finishing in-flight subagents
    # after the orchestrator returned, so result files can land after collection.
    # Harvest those once; journals that still produced nothing stay unprocessed
    # and are retried by a later run.
    if pending_collections:
        log(f"Sweeping {len(pending_collections)} pending journal result(s) — subagents may have finished late ...")
        for session_id, journal_gap, result_file in pending_collections:
            payload = parse_result_file(result_file)
            if payload is None:
                log(f"  {journal_gap['name']}: still no result file — left for the next run")
                continue
            journal_name = journal_gap["name"]
            status, rows, notes = sanitize_agent_result(
                journal_name=journal_name,
                journal_gap=journal_gap,
                agent_result=payload,
                existing_keys=existing_keys,
            )
            append_suggestions(args.output, rows)
            for row in rows:
                existing_keys.add((row["journal"], row["field"], row["suggested_value"]))
            written_suggestions += len(rows)
            record_journal(
                state, args.state, journal_name,
                status=status,
                notes=f"(late result) {notes}",
                session_id=session_id,
                suggestions_written=len(rows),
            )
            log(f"  {journal_name}: late result harvested — status={status} suggestions={len(rows)}")

    log("Enrichment complete — generating deterministic ISSN-based Alternative journal name suggestions ...")
    try:
        deterministic_rows = append_deterministic_suggestions(args.output, args.wtp_dir)
        log(f"  deterministic suggestions generated: {deterministic_rows}")
    except Exception as exc:
        log(f"WARNING: deterministic alt-name pass failed: {exc}")

    log("Run complete")
    log(f"  suggestions written : {written_suggestions}")
    log(f"  journals sent to swarm: {processed_count}")
    log(f"  output               : {args.output}")
    log(f"  state                : {args.state}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import csv
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from enrichment_common import DEFAULT_WTP_DIR, is_empty, SUGGESTIONS_CSV_PATH, WTP_SCRIPTS_DIR

if str(WTP_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(WTP_SCRIPTS_DIR))

import sheets_client
from libraries import format_issn, norm_name
from update_extracted import (
    load_doaj_issn_title_lookup,
    load_openapc_issn_title_lookup,
    load_scimago_issn_title_lookup,
)

from suggestions_io import dedupe_suggestions, load_suggestions, write_suggestions


FIELD_NAME = "Alternative journal name"
ISSN_FIELDS = ("e-ISSN", "p-ISSN", "ISSN-L")
PRESENCE_COLUMNS = ("Present in Scimago", "Present in DOAJ", "Present in openAPC")


@dataclass(frozen=True)
class SourceConfig:
    name: str
    loader: Callable[[], dict[str, list[str]]]
    url_builder: Callable[[str], str]


SOURCE_PRIORITY = (
    SourceConfig(
        name="Scimago",
        loader=load_scimago_issn_title_lookup,
        url_builder=lambda issn: f"https://www.scimagojr.com/journalsearch.php?q={issn}&tip=issn",
    ),
    SourceConfig(
        name="DOAJ",
        loader=load_doaj_issn_title_lookup,
        url_builder=lambda issn: f"https://doaj.org/toc/{issn}",
    ),
    SourceConfig(
        name="OpenAPC",
        loader=load_openapc_issn_title_lookup,
        url_builder=lambda issn: "https://treemaps.intact-project.org/apcdata/openapc/",
    ),
)


@contextmanager
def working_directory(path: Path) -> Iterator[None]:
    previous_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous_cwd)


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def iter_field_csv_paths(wtp_dir: Path) -> Iterator[Path]:
    for slug in sheets_client.SHEET_TAB_NAMES:
        csv_path = wtp_dir / "data_extracted" / f"{slug}.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"Field CSV not found: {csv_path}")
        yield csv_path


def collect_row_issns(row: dict[str, str]) -> list[str]:
    seen_issns: set[str] = set()
    issns: list[str] = []

    for field in ISSN_FIELDS:
        formatted = format_issn(row.get(field, ""))
        if formatted is None or formatted in seen_issns:
            continue
        seen_issns.add(formatted)
        issns.append(formatted)

    return issns


def needs_alt_name_suggestion(row: dict[str, str]) -> bool:
    if not is_empty(row.get(FIELD_NAME, "")):
        return False
    if not collect_row_issns(row):
        return False
    return any(str(row.get(column, "")).strip() == "No" for column in PRESENCE_COLUMNS)


def load_source_lookups(wtp_dir: Path) -> list[tuple[SourceConfig, dict[str, list[str]]]]:
    lookups: list[tuple[SourceConfig, dict[str, list[str]]]] = []
    with working_directory(wtp_dir):
        for source in SOURCE_PRIORITY:
            lookup = source.loader()
            lookups.append((source, lookup))
    return lookups


def resolve_source_match(
    lookup: dict[str, list[str]], issns: list[str]
) -> tuple[str, str] | None:
    matches_by_normalized_title: dict[str, tuple[str, str]] = {}

    for issn in issns:
        for title in lookup.get(issn, []):
            normalized_title = norm_name(title)
            if not normalized_title:
                continue
            if normalized_title not in matches_by_normalized_title:
                matches_by_normalized_title[normalized_title] = (title, issn)

    if not matches_by_normalized_title:
        return None
    if len(matches_by_normalized_title) > 1:
        return None
    return next(iter(matches_by_normalized_title.values()))


def build_suggestion(
    journal_name: str,
    matched_title: str,
    matched_issn: str,
    source: SourceConfig,
) -> dict[str, str]:
    return {
        "journal": journal_name,
        "field": FIELD_NAME,
        "current_value": "",
        "suggested_value": matched_title,
        "confidence": "1.00",
        "source_urls": source.url_builder(matched_issn),
        "reasoning": (
            f"Matched ISSN {matched_issn} in {source.name}. "
            f"{source.name} lists this journal as '{matched_title}', "
            "so the Alternative journal name is filled deterministically from the ISSN match."
        ),
        "suggestion_type": "alt_name",
        "priority": "high",
    }


def generate_suggestions(wtp_dir: Path) -> tuple[list[dict[str, str]], int]:
    lookups = load_source_lookups(wtp_dir)
    candidate_count = 0
    suggestions: list[dict[str, str]] = []

    for csv_path in iter_field_csv_paths(wtp_dir):
        for row in load_csv_rows(csv_path):
            journal_name = str(row.get("Journal", "")).strip()
            if not journal_name or not needs_alt_name_suggestion(row):
                continue

            candidate_count += 1
            issns = collect_row_issns(row)
            for source, lookup in lookups:
                match = resolve_source_match(lookup, issns)
                if match is None:
                    continue

                matched_title, matched_issn = match
                if norm_name(matched_title) == norm_name(journal_name):
                    continue

                suggestions.append(
                    build_suggestion(
                        journal_name=journal_name,
                        matched_title=matched_title,
                        matched_issn=matched_issn,
                        source=source,
                    )
                )
                break

    return suggestions, candidate_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate deterministic Alternative journal name suggestions from ISSN matches "
            "across Scimago, DOAJ, and OpenAPC."
        )
    )
    parser.add_argument(
        "--wtp-dir",
        type=Path,
        default=DEFAULT_WTP_DIR,
        help=f"Path to the WhereToPublish.github.io checkout (default: {DEFAULT_WTP_DIR})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=SUGGESTIONS_CSV_PATH,
        help=f"Path to Agent_suggestions.csv (default: {SUGGESTIONS_CSV_PATH})",
    )
    args = parser.parse_args()

    existing_rows = load_suggestions(args.output)
    generated_rows, candidate_count = generate_suggestions(args.wtp_dir)
    deduped_rows, dropped_rows = dedupe_suggestions(existing_rows + generated_rows)
    write_suggestions(args.output, deduped_rows)

    print(f"Scanned {candidate_count} candidate journal(s) with ISSN-backed missing-source coverage.")
    print(f"Generated {len(generated_rows)} deterministic Alternative journal name suggestion(s).")
    print(f"Dropped {dropped_rows} duplicate suggestion row(s) while canonicalizing {args.output}.")
    print(f"{args.output} now contains {len(deduped_rows)} canonical suggestion row(s).")


if __name__ == "__main__":
    main()
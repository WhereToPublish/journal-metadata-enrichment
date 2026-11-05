"""issn_lookups.py — Build ISSN → journal-title lookup maps from the WhereToPublish
external data files (Scimago, DOAJ, OpenAPC) in <wtp_dir>/data_extraction/.

Each loader returns a dict mapping a formatted ISSN ("XXXX-XXXX") to the list of
journal titles registered under that ISSN in the source.  Only the lightweight
stdlib csv/gzip readers are used here; the heavy WTP pipeline loaders are not
needed for a pure ISSN→title mapping.
"""

from __future__ import annotations

import csv
import gzip
import sys
from pathlib import Path
from typing import Iterator

from enrichment_common import WTP_SCRIPTS_DIR

if str(WTP_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(WTP_SCRIPTS_DIR))

from libraries import format_issn


def _iter_gz_rows(path: Path, delimiter: str = ",") -> Iterator[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        for row in reader:
            yield row


def _add_issn(lookup: dict[str, list[str]], raw_issn: str, title: str) -> None:
    formatted = format_issn(raw_issn)
    if formatted is None or not title:
        return
    lookup.setdefault(formatted, [])
    if title not in lookup[formatted]:
        lookup[formatted].append(title)


def load_scimago_issn_title_lookup(wtp_dir: Path) -> dict[str, list[str]]:
    """Scimago: the 'Issn' column holds a comma-separated list of bare ISSNs."""
    path = wtp_dir / "data_extraction" / "scimagojr.csv.gz"
    lookup: dict[str, list[str]] = {}
    for row in _iter_gz_rows(path, delimiter=";"):
        title = (row.get("Title") or "").strip()
        for raw in (row.get("Issn") or "").split(","):
            _add_issn(lookup, raw.strip(), title)
    return lookup


def load_doaj_issn_title_lookup(wtp_dir: Path) -> dict[str, list[str]]:
    """DOAJ: print ISSN and EISSN are separate columns."""
    path = wtp_dir / "data_extraction" / "DOAJ.csv.gz"
    lookup: dict[str, list[str]] = {}
    for row in _iter_gz_rows(path):
        title = (row.get("Journal title") or "").strip()
        _add_issn(lookup, row.get("Journal ISSN (print version)") or "", title)
        _add_issn(lookup, row.get("Journal EISSN (online version)") or "", title)
    return lookup


def load_openapc_issn_title_lookup(wtp_dir: Path) -> dict[str, list[str]]:
    """OpenAPC: issn, issn_print, issn_electronic and issn_l columns."""
    path = wtp_dir / "data_extraction" / "openapc.csv.gz"
    lookup: dict[str, list[str]] = {}
    for row in _iter_gz_rows(path):
        title = (row.get("journal_full_title") or "").strip()
        for column in ("issn", "issn_print", "issn_electronic", "issn_l"):
            raw = (row.get(column) or "").strip()
            if raw.upper() == "NA":
                continue
            _add_issn(lookup, raw, title)
    return lookup

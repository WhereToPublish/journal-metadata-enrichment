"""fetch_sheet.py — Download a single Google Sheet tab as CSV using the public export URL.

Usage:
    python3 fetch_sheet.py [--gid GID] [--output OUTPUT_PATH]

By default downloads the Genetics & Genomics tab (gid=1379563174) to
agent/output/raw_genetics_genomics.csv.

No authentication required — uses the public /pub?gid=...&output=csv endpoint.
"""

import argparse
import urllib.request
import urllib.error
import sys
import os
from pathlib import Path

# Public spreadsheet export base URL (read-only, no auth needed)
SPREADSHEET_BASE_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vTw97FS3eOFbYlqY8j7wWrBd3yrDaG6hqPclYJdPrnvd7t9U2DNz5xXNK4F0iesyHIKEkx9weLz-69a"
    "/pub"
)

# Tab GIDs keyed by field name
SHEET_GIDS = {
    "generalist": "897920130",
    "anatomy_physiology": "507847855",
    "cancer": "14394643",
    "development": "1596020802",
    "ecology_evolution": "1379256167",
    "genetics_genomics": "1379563174",
    "immunology": "1030012941",
    "molecular_cellular_biology": "86261319",
    "neurosciences": "312916140",
    "plants": "818438400",
}


def download_tab(gid: str, output_path: str) -> None:
    """Download a Google Sheet tab by GID to a local CSV file."""
    url = f"{SPREADSHEET_BASE_URL}?gid={gid}&single=true&output=csv"
    print(f"Downloading gid={gid} from:\n  {url}")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read()

        with open(output_path, "wb") as f:
            f.write(content)

        # Quick sanity check
        lines = content.decode("utf-8", errors="replace").splitlines()
        print(f"  Saved {len(lines)} rows to {output_path}")
        if lines:
            print(f"  Header: {lines[0][:120]}")
    except urllib.error.HTTPError as e:
        print(f"ERROR: HTTP {e.code} — {e.reason}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: {e.reason}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    script_dir = Path(__file__).parent
    default_output = str(script_dir.parent / "output" / "raw_genetics_genomics.csv")

    parser = argparse.ArgumentParser(description="Download a WhereToPublish Google Sheet tab as CSV.")
    parser.add_argument(
        "--gid",
        default=SHEET_GIDS["genetics_genomics"],
        help=f"Sheet GID (default: {SHEET_GIDS['genetics_genomics']} = Genetics & Genomics). "
             f"Available: {list(SHEET_GIDS.keys())}",
    )
    parser.add_argument(
        "--field",
        choices=list(SHEET_GIDS.keys()),
        default=None,
        help="Field name (alternative to --gid). Overrides --gid if both are given.",
    )
    parser.add_argument("--output", default=default_output, help=f"Output CSV path (default: {default_output})")
    args = parser.parse_args()

    gid = SHEET_GIDS[args.field] if args.field else args.gid
    download_tab(gid, args.output)


if __name__ == "__main__":
    main()

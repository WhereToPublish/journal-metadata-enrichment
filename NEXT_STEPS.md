# NEXT_STEPS.md — Wiring the Agent to Google Sheets

This file describes what you need to do **after** the agent is producing a good
`AI_Suggestions.csv` — to enable actual upload to Google Sheets and the
accept/reject review workflow.

---

## Phase A — Enable Google Sheets API Access

### A1. Create a Google Cloud Service Account

1. Go to https://console.cloud.google.com/
2. Create a new project (e.g. "WhereToPublish-Agent") or select an existing one.
3. Enable the **Google Sheets API**:
   - APIs & Services → Enable APIs & Services → search "Google Sheets API" → Enable.
4. Create a service account:
   - APIs & Services → Credentials → Create Credentials → Service Account.
   - Name: `journal-agent`
   - Role: No role needed (permissions are granted via Sheet sharing).
   - Click Done.
5. Create a JSON key for the service account:
   - Click the service account → Keys → Add Key → Create new key → JSON.
   - Download the JSON file. **Keep it secret — it grants write access to your sheets.**
6. Note the service account email address:
   e.g. `journal-agent@wheretoPublish-agent.iam.gserviceaccount.com`

### A2. Share the Spreadsheet with the Service Account

1. Open the WhereToPublish spreadsheet:
   https://docs.google.com/spreadsheets/d/1PRXViyQlo5ZMjpCJ_XpcHfsnZEJmmdCiXjnkazMyua8/edit
2. Click Share.
3. Add the service account email address with **Editor** role.
4. Click Send.

### A3. Store the Credentials Securely

Place the JSON key file at a path outside the repository (never commit credentials):
```
~/.config/wheretopublish/google_service_account.json
```

Or set an environment variable pointing to it:
```bash
export GOOGLE_SERVICE_ACCOUNT_KEY="$HOME/.config/wheretopublish/google_service_account.json"
```

Add this export to your `~/.zshrc` so it persists across sessions.

---

## Phase B — Upload Script

### B1. Install the Google Sheets Python library

```bash
source /Users/tlatrille/Documents/venv/py312stats/bin/activate
pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

### B2. `agent/scripts/upload_suggestions.py`

Create this script after Phase A is complete. It reads `AI_Suggestions.csv` and
writes rows to the `AI_Suggestions` tab of the spreadsheet.

```python
"""upload_suggestions.py — Upload AI_Suggestions.csv to the WhereToPublish Google Sheet.

Usage:
    python3 agent/scripts/upload_suggestions.py \
        --input agent/output/AI_Suggestions.csv \
        --credentials ~/.config/wheretopublish/google_service_account.json

The script creates (or clears and rewrites) the 'AI_Suggestions' tab in the spreadsheet.
It adds an 'Approve?' checkbox column for the human review workflow.
"""

import argparse
import csv
import os
from pathlib import Path

SPREADSHEET_ID = "1PRXViyQlo5ZMjpCJ_XpcHfsnZEJmmdCiXjnkazMyua8"
SUGGESTIONS_TAB = "AI_Suggestions"

# Column order in the AI_Suggestions tab (matches CSV + extra review columns)
OUTPUT_HEADERS = [
    "Approve?",            # Checkbox for human review (added by this script)
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


def get_sheets_service(credentials_path: str):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds)


def upload(input_csv: str, credentials_path: str) -> None:
    service = get_sheets_service(credentials_path)
    sheets = service.spreadsheets()

    # Read suggestions
    with open(input_csv, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Uploading {len(rows)} suggestions ...")

    # Prepare data: header + rows, with Approve? = FALSE (unchecked)
    data = [OUTPUT_HEADERS]
    for row in rows:
        data.append([
            False,  # Approve? checkbox — starts unchecked
            row.get("journal", ""),
            row.get("field", ""),
            row.get("current_value", ""),
            row.get("suggested_value", ""),
            row.get("confidence", ""),
            row.get("source_urls", ""),
            row.get("reasoning", ""),
            row.get("suggestion_type", ""),
            row.get("priority", ""),
        ])

    # Ensure the tab exists; if not, create it
    spreadsheet = sheets.get(spreadsheetId=SPREADSHEET_ID).execute()
    sheet_names = [s["properties"]["title"] for s in spreadsheet["sheets"]]

    if SUGGESTIONS_TAB not in sheet_names:
        sheets.batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": SUGGESTIONS_TAB}}}]},
        ).execute()
        print(f"Created tab '{SUGGESTIONS_TAB}'")

    # Clear existing content
    sheets.values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SUGGESTIONS_TAB}!A1:Z",
    ).execute()

    # Write new data
    sheets.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SUGGESTIONS_TAB}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": data},
    ).execute()

    print(f"Done. Open the sheet and review the '{SUGGESTIONS_TAB}' tab.")
    print(f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",       default="agent/output/AI_Suggestions.csv")
    parser.add_argument("--credentials", default=os.path.expanduser(
        "~/.config/wheretopublish/google_service_account.json"
    ))
    args = parser.parse_args()
    upload(args.input, args.credentials)
```

---

## Phase C — Human Review Interface (Google Apps Script)

### C1. What the Tab Looks Like

After upload, the `AI_Suggestions` tab has these columns:

| A | B | C | D | E | F | G | H | I | J |
|---|---|---|---|---|---|---|---|---|---|
| Approve? | journal | field | current_value | suggested_value | confidence | source_urls | reasoning | suggestion_type | priority |
| ☐ FALSE | Genome Biology | Business model | | OA diamond | 0.92 | https://... | ... | fill | high |

Team members check the `Approve?` box for each suggestion they accept.

### C2. Install the Apps Script

1. Open the spreadsheet.
2. Extensions → Apps Script.
3. Paste the following script and save it as `ApplySuggestions.gs`:

```javascript
// ApplySuggestions.gs
// Reads approved suggestions from AI_Suggestions tab and applies them
// to the corresponding field tabs (e.g. "Genetics & Genomics").
//
// Run via: Extensions > WhereToPublish > Apply Approved Suggestions

const SPREADSHEET_ID = SpreadsheetApp.getActiveSpreadsheet().getId();
const SUGGESTIONS_TAB = "AI_Suggestions";
const PROCESSED_TAB = "AI_Suggestions_Processed";

// Map field names (from Field column values) to their tab names
const FIELD_TO_TAB = {
  "Generalist":                  "Generalist",
  "Anatomy & Physiology":        "Anatomy & Physiology",
  "Cancer":                      "Cancer",
  "Development":                 "Development",
  "Ecology & Evolution":         "Ecology & Evolution",
  "Genetics & Genomics":         "Genetics & Genomics",
  "Immunology":                  "Immunology",
  "Molecular & Cellular Biology":"Molecular & Cellular Biology",
  "Neurosciences":               "Neurosciences",
  "Plants":                      "Plants",
};

// Column names in the data tabs (must match exactly)
const DATA_COLUMNS = [
  "Journal", "Website", "Journal's MAIN field", "Field",
  "Publisher type", "Publisher", "Institution", "Institution type",
  "Country", "Business model", "APC Euros", "Scimago Rank",
  "Scimago Quartile", "H index", "PCI partner", "Scimago Journal Title"
];

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("WhereToPublish")
    .addItem("Apply Approved Suggestions", "applyApprovedSuggestions")
    .addToUi();
}

function applyApprovedSuggestions() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sugTab = ss.getSheetByName(SUGGESTIONS_TAB);
  if (!sugTab) { SpreadsheetApp.getUi().alert("Tab '" + SUGGESTIONS_TAB + "' not found."); return; }

  const data = sugTab.getDataRange().getValues();
  const headers = data[0];

  // Column indices in AI_Suggestions tab
  const col = {};
  headers.forEach((h, i) => { col[h] = i; });

  const required = ["Approve?", "journal", "field", "suggested_value", "suggestion_type"];
  for (const r of required) {
    if (col[r] === undefined) { SpreadsheetApp.getUi().alert("Missing column: " + r); return; }
  }

  // Ensure processed tab exists
  let procTab = ss.getSheetByName(PROCESSED_TAB);
  if (!procTab) { procTab = ss.insertSheet(PROCESSED_TAB); procTab.appendRow(headers); }

  let applied = 0, skipped = 0, errors = [];

  for (let i = 1; i < data.length; i++) {
    const row = data[i];
    if (row[col["Approve?"]] !== true) continue;  // only process checked rows

    const journalName   = row[col["journal"]];
    const fieldToUpdate = row[col["field"]];
    const suggestedVal  = row[col["suggested_value"]];
    const sugType       = row[col["suggestion_type"]];

    try {
      if (sugType === "remove") {
        // Mark the row with a note rather than deleting it
        const msg = applyToDataTab(ss, journalName, "Journal", "FLAGGED_FOR_REMOVAL");
        if (msg) errors.push(msg);
      } else if (sugType === "add") {
        // Adding new journals requires manual work — just log it
        errors.push("MANUAL: add journal '" + journalName + "' — cannot auto-add rows yet.");
      } else {
        // fill / correct / alt_name
        const msg = applyToDataTab(ss, journalName, fieldToUpdate, suggestedVal);
        if (msg) errors.push(msg);
        else applied++;
      }
    } catch(e) {
      errors.push("ERROR on row " + (i+1) + ": " + e.message);
    }

    // Archive to processed tab
    procTab.appendRow(row);
    // Clear approve checkbox
    sugTab.getRange(i + 1, col["Approve?"] + 1).setValue(false);
  }

  const summary = "Applied: " + applied + " | Skipped: " + skipped +
                  (errors.length ? "\n\nIssues:\n" + errors.join("\n") : "");
  SpreadsheetApp.getUi().alert("Done!\n\n" + summary);
}

function applyToDataTab(ss, journalName, fieldName, newValue) {
  // Find the journal in ALL data tabs (it may appear in multiple)
  let found = false;
  for (const [fieldLabel, tabName] of Object.entries(FIELD_TO_TAB)) {
    const sheet = ss.getSheetByName(tabName);
    if (!sheet) continue;

    const vals = sheet.getDataRange().getValues();
    const headers = vals[0];
    const journalCol = headers.indexOf("Journal");
    const targetCol  = headers.indexOf(fieldName);

    if (journalCol === -1) continue;
    if (targetCol  === -1) continue;  // field doesn't exist in this tab

    for (let r = 1; r < vals.length; r++) {
      if (String(vals[r][journalCol]).trim().toLowerCase() === journalName.trim().toLowerCase()) {
        sheet.getRange(r + 1, targetCol + 1).setValue(newValue);
        found = true;
        // Continue searching — same journal may appear in multiple tabs
      }
    }
  }

  if (!found) {
    return "Journal not found in any tab: '" + journalName + "' (field: " + fieldName + ")";
  }
  return null;  // success
}
```

4. In the Apps Script editor: Run → Run function → `onOpen` (to register the menu).
5. Return to the spreadsheet. You should see "WhereToPublish" in the menu bar.

### C3. Daily Workflow

1. Agent runs overnight → produces `AI_Suggestions.csv`.
2. Run `python3 agent/scripts/upload_suggestions.py` to push to the sheet.
3. Open the spreadsheet → `AI_Suggestions` tab.
4. For each row: read the `reasoning` and `source_urls`, check the `Approve?` box if you agree.
5. Click **WhereToPublish → Apply Approved Suggestions**.
6. Approved changes are applied to the data tabs. The `AI_Suggestions_Processed` tab archives them.
7. Trigger the GitHub Actions workflow (or run `bash scripts/run.sh`) to regenerate the website data.

---

## Phase D — OpenClaw Model Configuration

Set the primary model for the agent (run once):

```bash
# Recommended for 32 GB M2 — best reasoning quality
openclaw config set agents.defaults.model.primary ollama/qwen2.5:32b

# Alternative — faster, good for agentic multi-step tasks
# openclaw config set agents.defaults.model.primary ollama/qwen3:30b-a3b

# Fallback to cloud if local model is unavailable (optional)
# openclaw config set agents.defaults.model.fallbacks '["anthropic/claude-sonnet-4-6"]'
```

Ensure Ollama has the model pulled:
```bash
ollama pull qwen2.5:32b   # ~19 GB download
```

---

## Summary Checklist

- [ ] Google Cloud project created, Sheets API enabled
- [ ] Service account created, JSON key downloaded to `~/.config/wheretopublish/google_service_account.json`
- [ ] `GOOGLE_SERVICE_ACCOUNT_KEY` env var set in `~/.zshrc`
- [ ] Spreadsheet shared with service account email (Editor)
- [ ] `pip install google-auth google-api-python-client` done
- [ ] `agent/scripts/upload_suggestions.py` created from the code above
- [ ] Apps Script pasted into the spreadsheet and `onOpen` run once
- [ ] Ollama model pulled (`ollama pull qwen2.5:32b`)
- [ ] `openclaw config set agents.defaults.model.primary ollama/qwen2.5:32b` run

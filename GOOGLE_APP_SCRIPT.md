# Wiring the Agent to Google Sheets

This file describes what you need to do **after** actual upload to Google Sheets for the
accept/reject review workflow.

## Human Review Interface (Google Apps Script)

### 1. What the Tab Looks Like

After upload, the `AI_suggestions` tab has these columns:

| A        | B              | C              | D             | E               | F          | G           | H         | I               | J        |
| -------- | -------------- | -------------- | ------------- | --------------- | ---------- | ----------- | --------- | --------------- | -------- |
| Approve? | journal        | field          | current_value | suggested_value | confidence | source_urls | reasoning | suggestion_type | priority |
| ☐ FALSE | Genome Biology | Business model |               | OA diamond      | 0.92       | https://... | ...       | fill            | high     |

Team members check the `Approve?` box for each suggestion they accept.

### 2. Install the Apps Script

1. Open the spreadsheet.
2. Extensions → Apps Script.
3. Paste the following script and save it as `ApplySuggestions.gs`:

```javascript
// ApplySuggestions.gs
// Reads approved suggestions from AI_suggestions tab and applies them
// to the corresponding field tabs (e.g. "Genetics & Genomics").
//
// Run via: Extensions > WhereToPublish > Apply Approved Suggestions

const SPREADSHEET_ID = SpreadsheetApp.getActiveSpreadsheet().getId();
const SUGGESTIONS_TAB = "AI_suggestions";
const PROCESSED_TAB = "AI_suggestions_processed";

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

  // Column indices in AI_suggestions tab
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

### 3. Daily Workflow

1. Agent runs overnight → produces `AI_suggestions.csv`.
2. Run `python3 agent/scripts/upload_suggestions.py` to push to the sheet.
3. Open the spreadsheet → `AI_suggestions` tab.
4. For each row: read the `reasoning` and `source_urls`, check the `Approve?` box if you agree.
5. Click **WhereToPublish → Apply Approved Suggestions**.
6. Approved changes are applied to the data tabs. The `AI_suggestions_processed` tab archives them.
7. Trigger the GitHub Actions workflow (or run `bash scripts/run.sh`) to regenerate the website data.

---

## OpenClaw Model Configuration

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

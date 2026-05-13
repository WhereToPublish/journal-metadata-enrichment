# Wiring the Agent to Google Sheets

This file describes what you need to do **after** actual upload to Google Sheets for the
review workflow.

## Human Review Interface (Google Apps Script)

### 1. What the Tab Looks Like

After upload, the `Agent_suggestions` tab has these columns:

| A       | B              | C              | D             | E               | F          | G           | H         | I               | J        |
| ------- | -------------- | -------------- | ------------- | --------------- | ---------- | ----------- | --------- | --------------- | -------- |
| Status  | journal        | field          | current_value | suggested_value | confidence | source_urls | reasoning | suggestion_type | priority |
| pending | Genome Biology | Business model |               | OA diamond      | 0.92       | https://... | ...       | fill            | high     |

The **Status** column is a dropdown with three options: `pending`, `approve`, `reject`.

- New suggestions are uploaded with `pending` status.
- Team members change the status to `approve` or `reject` after reviewing the evidence.
- Running the Apps Script processes all `approve` and `reject` rows: approved suggestions are applied to the data tabs and archived; rejected suggestions are archived without any data change. `pending` rows are left untouched.

### 2. Install the Apps Script

1. Open the spreadsheet.
2. Extensions → Apps Script.
3. Paste the following script and save it as `ApplySuggestions.gs`:

```javascript
// ApplySuggestions.gs
// Processes reviewed suggestions from the Agent_suggestions tab:
//   - "approve" → applies the suggestion to the corresponding data tab + archives
//   - "reject"  → archives only (no data change)
//   - "pending" → left untouched
//
// Run via: Extensions > WhereToPublish > Apply Reviewed Suggestions

const SUGGESTIONS_TAB = "Agent_suggestions";
const PROCESSED_TAB   = "Agent_suggestions_processed";

// Map tab field values to the actual Google Sheets tab names.
// Must match the tab names used in the spreadsheet exactly.
const FIELD_TO_TAB = {
  "Generalist":                   "Generalist",
  "Anatomy & Physiology":         "Anatomy & Physiology",
  "Cancer":                       "Cancer",
  "Development":                  "Development",
  "Ecology & Evolution":          "Ecology & Evolution",
  "Genetics & Genomics":          "Genetics & Genomics",
  "Immunology":                   "Immunology",
  "Molecular & Cellular Biology": "Molecular & Cellular Biology",
  "Neurosciences":                "Neurosciences",
  "Plants":                       "Plants",
};

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("WhereToPublish")
    .addItem("Apply Reviewed Suggestions", "applyReviewedSuggestions")
    .addToUi();
}

function applyReviewedSuggestions() {
  const ss     = SpreadsheetApp.getActiveSpreadsheet();
  const sugTab = ss.getSheetByName(SUGGESTIONS_TAB);
  if (!sugTab) {
    SpreadsheetApp.getUi().alert("Tab '" + SUGGESTIONS_TAB + "' not found.");
    return;
  }

  const data    = sugTab.getDataRange().getValues();
  const headers = data[0];

  // Build column-index map from header row
  const col = {};
  headers.forEach((h, i) => { col[h] = i; });

  const required = ["Status", "journal", "field", "suggested_value", "suggestion_type"];
  for (const r of required) {
    if (col[r] === undefined) {
      SpreadsheetApp.getUi().alert("Missing column: " + r);
      return;
    }
  }

  // Ensure the processed archive tab exists with a header row
  let procTab = ss.getSheetByName(PROCESSED_TAB);
  if (!procTab) {
    procTab = ss.insertSheet(PROCESSED_TAB);
    procTab.appendRow(headers);
  }

  let applied = 0;
  let archived = 0;
  const errors = [];
  // Collect 1-based row indices to remove from Agent_suggestions (bottom-to-top to preserve indices)
  const rowsToRemove = [];

  for (let i = 1; i < data.length; i++) {
    const row    = data[i];
    const status = String(row[col["Status"]] || "").trim().toLowerCase();

    // Only process "approve" and "reject"; leave "pending" untouched
    if (status !== "approve" && status !== "reject") continue;

    const journalName   = row[col["journal"]];
    const fieldToUpdate = row[col["field"]];
    const suggestedVal  = row[col["suggested_value"]];
    const sugType       = String(row[col["suggestion_type"]] || "").trim();

    if (status === "approve") {
      try {
        if (sugType === "remove") {
          const msg = applyToDataTab(ss, journalName, "Journal", "FLAGGED_FOR_REMOVAL");
          if (msg) errors.push(msg);
          else applied++;
        } else if (sugType === "add") {
          errors.push("MANUAL: add journal '" + journalName + "' — cannot auto-add rows yet.");
        } else {
          // fill / correct / alt_name
          const msg = applyToDataTab(ss, journalName, fieldToUpdate, suggestedVal);
          if (msg) errors.push(msg);
          else applied++;
        }
      } catch (e) {
        errors.push("ERROR on row " + (i + 1) + ": " + e.message);
      }
    }

    // Archive the row (approve and reject alike)
    procTab.appendRow(row);
    archived++;
    rowsToRemove.push(i + 1);  // 1-based sheet row
  }

  // Delete processed rows from Agent_suggestions (bottom-to-top to preserve row indices)
  rowsToRemove.reverse().forEach(rowNum => sugTab.deleteRow(rowNum));

  const summary =
    "Applied: " + applied + " | Archived: " + archived +
    (errors.length ? "\n\nIssues:\n" + errors.join("\n") : "");
  SpreadsheetApp.getUi().alert("Done!\n\n" + summary);
}

function applyToDataTab(ss, journalName, fieldName, newValue) {
  // Search all data tabs — the same journal may appear in multiple tabs
  let found = false;
  for (const tabName of Object.values(FIELD_TO_TAB)) {
    const sheet = ss.getSheetByName(tabName);
    if (!sheet) continue;

    const vals       = sheet.getDataRange().getValues();
    const hdrs       = vals[0];
    const journalCol = hdrs.indexOf("Journal");
    const targetCol  = hdrs.indexOf(fieldName);

    if (journalCol === -1 || targetCol === -1) continue;

    for (let r = 1; r < vals.length; r++) {
      if (String(vals[r][journalCol]).trim().toLowerCase() === journalName.trim().toLowerCase()) {
        sheet.getRange(r + 1, targetCol + 1).setValue(newValue);
        found = true;
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

1. Agent runs overnight → produces `Agent_suggestions.csv`.
2. Run `python3 agent/scripts/upload_suggestions.py` to push to the sheet.
3. Open the spreadsheet → `Agent_suggestions` tab.
4. For each row: read the `reasoning` and `source_urls`, then set the **Status** dropdown to `approve` or `reject`.
5. Click **WhereToPublish → Apply Reviewed Suggestions**.
6. `approve` rows: the suggestion is written to the data tab and the row is moved to `Agent_suggestions_processed`.
7. `reject` rows: the row is moved to `Agent_suggestions_processed` without any data change.
8. `pending` rows remain in `Agent_suggestions` for continued review.
9. Trigger the GitHub Actions workflow (or run `bash scripts/run.sh`) to regenerate the website data.

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

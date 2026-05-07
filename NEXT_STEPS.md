# NEXT_STEPS.md — Using Suggestion History to Improve the AI Agent

The `AI_suggestions_processed` sheet accumulates every suggestion that has been reviewed
(status `approve` or `reject`).  Over time this dataset becomes a labelled history of agent
performance that can be used for analysis and systematic improvement.

---

## 1. Approval-Rate Analysis by Field

**Goal**: Identify which suggestion fields the model performs well on and which it does not.

- Export `AI_suggestions_processed` to CSV.
- Group rows by `field` and compute:
  - `approve_rate = approve / (approve + reject)` per field
  - median `confidence` for approved vs rejected rows
- Fields with low approval rates are candidates for tighter validation or prompt revision.
- Fields with very high approval rates are well-understood by the model; further human review
  effort there can be reduced over time.

**Actionable outcome**: Adjust validation thresholds (e.g. minimum confidence, required reasoning
keywords) per field based on observed approval rates.

---

## 2. Reject-Pattern Mining

**Goal**: Understand systematic error modes so they can be corrected at the prompt or validation layer.

- For rejected rows, extract features:
  - `field`, `suggestion_type`, `confidence` band (high ≥ 0.8, medium 0.6–0.8, low < 0.6)
  - whether `source_urls` contains DOAJ, Scimago, or a publisher page
  - keywords in `reasoning` (e.g. "appears to be", "likely", "probably")
- Cluster or count by these features to find the most common rejection patterns.
- Examples of patterns that are worth addressing:
  - High-confidence rejections → over-confident model on a specific field
  - Low-evidence rejections (single source, vague reasoning) → tighten minimum-evidence rules
  - Publisher-as-institution rejections that still slip through → strengthen validation rules

**Actionable outcome**: Add new rejection rules to `suggestions_io.py`; add hedging-language
detection to confidence down-scoring; update prompt constraints.

---

## 3. Confidence Calibration

**Goal**: Verify that stated confidence scores match observed approval rates.

- Bin rows by confidence (e.g. 0.55–0.65, 0.65–0.75, 0.75–0.85, 0.85–1.0).
- For each bin, compute observed approval rate.
- A well-calibrated model should show approval rate ≈ confidence level.
- If the model is systematically over-confident (high stated confidence but low actual approval),
  lower the minimum confidence threshold or add a post-processing calibration step.

**Actionable outcome**: Plot calibration curve; adjust the minimum confidence cutoff in
`suggestions_io.py` (`confidence >= 0.55` at present) if the curve shows systematic bias.

---

## 4. Model Comparison Across Runs

**Goal**: Compare the approval rates of different Ollama models to guide model selection.

- Tag each batch of suggestions with the model name used (the `run_state.json` records
  `session_id`; add a `model` field to session state for traceability).
- After several runs with different models, compare approval rates, rejection rates, and
  the distribution of unresolved journals per model.

**Actionable outcome**: Choose the model with the best approval-rate / cost / speed trade-off
for routine overnight runs.

---

## 5. Feedback Loop: Few-Shot Prompt Improvement

**Goal**: Use approved suggestions as high-quality examples in the model prompt to steer
future outputs towards patterns that humans have validated.

- Select a diverse set of approved rows across fields (≥ 3 examples per field if possible).
- Add them as `"Few-shot examples"` in the `build_prompt()` function in `run_enrichment.py`.
- Rejected patterns can be used as negative examples ("Do not suggest X because …").

**Actionable outcome**: Iteratively improve the prompt by adding field-specific examples drawn
directly from the reviewed history.

---

## 6. Fine-Tuning (Longer-Term)

Once the history is large enough (≥ 500 reviewed suggestions across fields):

- Export `(prompt, approved_JSON_output)` pairs from approved runs.
- Use them as supervised fine-tuning data for a local Ollama model.
- Track approval rates before and after fine-tuning to measure improvement.

**Actionable outcome**: A domain-adapted model that requires less human correction per run.

---

## 7. Automated Performance Reporting

**Goal**: Generate a periodic summary report without manual analysis.

Implement a script `agent/scripts/report_performance.py` that:

1. Reads `AI_suggestions_processed` from Google Sheets (using `sheets_client`).
2. Computes per-field approval rates, overall approval rate, and top rejection reasons.
3. Writes a Markdown report to `agent/output/performance_report.md`.
4. Optionally prints a summary to stdout for CI/CD pipelines.

This script can be run after each upload cycle to give the team a quick health check on
the agent's current performance.

---

## Tracking Fields to Add (Short-Term)

To enable the analyses above, add the following columns when they are missing:

| Column          | Where          | Notes                                       |
| --------------- | -------------- | ------------------------------------------- |
| `model`         | run_state.json | The Ollama model tag used for the session   |
| `reviewed_at`   | processed tab  | Timestamp when the row was approved/rejected|
| `reviewer`      | processed tab  | Optional: who reviewed (for team workflows) |

These columns do not need to be in the local `AI_suggestions.csv`; they can be written by the
Apps Script or added manually in Google Sheets.

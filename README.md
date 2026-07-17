# SDLC Pre-Flight Checklist Generator

## Overview
This script generates a stage-aware **pre-flight checklist** for software teams based on historical SDLC metrics.

It helps answer:
- What is the current stage risk level?
- Which metrics are causing risk?
- What due-diligence actions should be completed before moving ahead?

Script file:
- `src_preflight_checklist.py`

Default dataset:
- `data_metrics.json`

---

## How It Works

### Input
The script accepts:
- `--team-name` (required)
- `--current-sdlc-stage` (required)
- `--data-path` (optional, default: `data_metrics.json`)

### Processing Steps
1. Loads and validates JSON metric records.
2. Filters records by team and SDLC stage.
3. Maps status to normalized risk:
   - `good -> Healthy`
   - `warning -> At-Risk`
   - `poor -> Critical`
4. Applies fallback threshold logic if status is unknown.
5. **Cross-checks the recorded status against the raw thresholds** and flags any disagreement as a data-quality inconsistency, rather than blindly trusting the status field (see "Status vs. Threshold Verification" below).
6. Computes overall stage risk:
   - Any `Critical` => `Critical`
   - Else any `At-Risk` => `At-Risk`
   - Else all `Healthy` => `Healthy`
7. Computes a weighted **Risk Score (0–100)** so "one Critical metric among ten" reads differently from "ten Critical metrics out of ten."
8. Generates markdown output:
   - Stage summary with risk level and score
   - Risk signals, sorted Critical-first
   - Top risk drivers
   - Targeted checklist for at-risk/critical metrics (rules sourced from a config dict, not hardcoded per metric)

---

## Architecture & Design Decisions

### Why this structure?
- **`MetricRecord` dataclass**: strong, readable schema for each metric row.
- **Small focused functions** (`load_records`, `find_records`, `severity_for_record`, `format_markdown`) improve testability and maintenance.
- **Config-style mapping (`STATUS_TO_RISK`, `CHECKLIST_RULES`)** makes risk normalization and remediation guidance explicit and extensible — adding a new metric type means adding a dict entry, not an `if` branch.
- **Rule-based checklist generation** in `checklist_actions()` keeps recommendations deterministic and explainable.

### Risk Logic
- Primary risk source is the dataset `status` field.
- If status is not recognized, fallback uses threshold direction:
  - If `threshold_good < threshold_poor`, higher values are worse.
  - If `threshold_good > threshold_poor`, lower values are worse.
- This supports both metric types:
  - Higher-is-worse (e.g., technical debt, defect density)
  - Higher-is-better (e.g., coverage, pass rate, clarity score)

### Status vs. Threshold Verification
The prototype treats the dataset's `status` field as the **authoritative historical signal** — the assignment explicitly provides this column, and trusting it while validating against it is a reasonable design choice for a prototype. Alongside that, the script **independently recalculates severity from the raw `metric_value` against `threshold_good`/`threshold_poor`** purely to detect possible data-quality inconsistencies — it does not override the authoritative status.

If the two disagree, the output flags it explicitly rather than silently picking one:
> ⚠️ *Data inconsistency: recorded status maps to X, but the raw value against threshold computes to Y.*

This surfaces real, not hypothetical, cases — running this exact dataset shows two: `Delta Force / Defect Density` (status says Healthy, computed says At-Risk) and `Epsilon Squad / Rollback Risk Score` (status says Critical, computed says At-Risk). The overall risk label and Risk Score still come from the recorded status (so a flagged mismatch on `Delta Force` does not pull its Risk Score below 100/100) — the check is a validation signal for a human to investigate, not a second source of truth that silently wins.

### Weighted Risk Score
A single Critical metric among ten Healthy ones and ten Critical metrics out of ten both used to collapse to the same label: "Critical." To make the severity of a stage easier to gauge at a glance, the script computes a **0–100 Risk Score** using per-metric penalty weights (`Healthy: 0`, `At-Risk: 15`, `Critical: 40`), shown alongside the qualitative label, e.g. `Critical (Risk Score: 60/100)`.

These weights are **illustrative for the prototype**, not derived from real incident data. In production they'd be configurable (or learned from historical incident impact per metric) rather than fixed constants. The score is also floored at 0, so 3 Critical metrics and 30 Critical metrics currently both read as `0/100` — acceptable for this dataset's scale, but a production version would normalize by metric count or weight by business impact so a stage with many risky metrics is still distinguishable from one with a few.

### Prioritized Risk Drivers
Risk Signals and the Due-Diligence Checklist are sorted **Critical first, then At-Risk, then Healthy**, instead of following raw JSON order, so the most urgent issue is always what a reader sees first. A dedicated "🎯 Top Risk Drivers" section summarizes just the risky metrics with severity icons, plus a deviation note (e.g. `target ≤15, actual 28, over by 13.0`) so the notification explains *why* a metric is risky, not just that it is.

### Full Metric Coverage & Deployment Recommendation
`CHECKLIST_RULES` now has a specific entry for every metric type in the sample dataset (Technical Debt, Coverage, Rollback Risk, Requirements Clarity, Review Cycle Count, Design Review Duration, PR Review Time, Pass Rate, Defect Density, Response Time) rather than leaving most of them to fall through to a generic action list.

Every generated notification also ends with a **Deployment Recommendation**, tying the risk level directly to an operational decision:
- 🟢 Healthy → proceed automatically
- 🟡 At-Risk → Engineering Manager approval required
- 🔴 Critical → deployment blocked until the checklist is completed

This connects the metrics to an actual release decision, which is closer to what an Engineering Operations workflow needs than a checklist alone.

### Output Format
Markdown output is intentionally structured for easy posting into:
- Slack
- PR comments
- Notion
- Release readiness notes

**Future enhancement:** the current interface is a CLI, matching the assignment's "or something else" allowance. A natural next step would be wrapping `format_markdown()` behind a small FastAPI service (`POST /preflight` with `{team, stage}` in the body, Markdown in the response) so it can be called directly from a GitHub Action or Jira webhook instead of shelling out to a script.

---

## Error Handling / Edge Cases

The script handles:
1. Missing dataset file  
   - Returns: `❌ Dataset file not found: ...`
2. Invalid JSON root type (non-array)
3. Missing required fields in any record
4. Unknown team name  
   - Returns: `❌ Team 'X' not found in dataset.`
5. Unknown stage for a valid team  
   - Returns available stages for that team
6. Unknown metric status values  
   - Falls back to threshold-based severity logic

Exit codes:
- `0` success
- `1` runtime/parse failure
- `2` validation/filtering failure (e.g., team/stage not found)

---

## Usage

### Run with default data file
```bash
python3 src_preflight_checklist.py --team-name "Alpha Squad" --current-sdlc-stage "Coding"
```

### Run with custom data path
```bash
python3 src_preflight_checklist.py \
  --team-name "Gamma Engineers" \
  --current-sdlc-stage "Integration Testing" \
  --data-path "data_metrics.json"
```

---

## Running Tests (Optional but Recommended)

Install dev dependencies:
```bash
pip install -r requirements-dev.txt
```

Run tests:
```bash
pytest -q
```

Test file:
- `test_preflight_checklist.py`

---

## Example Outputs

The three examples below are the script's actual, current output — copy-pasted from a real run, not hand-written.

### Healthy stage
```text
## 🚦 SDLC Pre-Flight Checklist
- **Team:** Gamma Engineers
- **Project(s):** Analytics Dashboard
- **Current Stage:** Integration Testing
- **Risk Level:** **Healthy** (Risk Score: 100/100)

✅ Historical indicators look healthy for this stage.
Keep current engineering hygiene and proceed with confidence.

### 🚦 Deployment Recommendation

🟢 Proceed automatically — no gating required.
```

### Healthy stage with a hidden data inconsistency
```text
## 🚦 SDLC Pre-Flight Checklist
- **Team:** Delta Force
- **Project(s):** Mobile App V2
- **Current Stage:** Functional Testing
- **Risk Level:** **Healthy** (Risk Score: 100/100)

✅ Historical indicators are officially healthy for this stage,
but the data has inconsistencies worth a second look before proceeding.

⚠️ 1 metric(s) show a status/threshold mismatch:

### 📊 Risk Signals

- **Defect Density**: 2.3 defects per kloc (good: 1.5, poor: 3.0) → **Healthy** (source status: Good)
  - ⚠️ *Data inconsistency: recorded status maps to **Healthy**, but the raw value against threshold computes to **At-Risk**. Verify which is correct before treating this as ground truth.*

### 🚦 Deployment Recommendation

🟢 Proceed automatically — no gating required.
```
Note the Risk Score stays `100/100` here even though a mismatch was flagged — that's intentional, not a bug: the score is derived from the authoritative `status` field (see "Status vs. Threshold Verification" above), and the mismatch flag is a separate, explicit data-quality signal for a human to check, not a silent override of the score.

### Critical stage (with targeted checklist, top drivers, and deployment gate)
```text
## 🚦 SDLC Pre-Flight Checklist
- **Team:** Alpha Squad
- **Project(s):** Notification System
- **Current Stage:** Coding
- **Risk Level:** **Critical** (Risk Score: 60/100)

### 📊 Risk Signals

- **Technical Debt Ratio**: 28.0 percentage (good: 15.0, poor: 25.0) → **Critical** (source status: Poor)

### 🎯 Top Risk Drivers

- 🔴 Technical Debt Ratio (Critical) — target ≤15, actual 28, over by 13.0

### ✅ Targeted Due-Diligence Checklist

#### Technical Debt Ratio (Critical)
- [ ] Reserve capacity for debt reduction in current sprint.
- [ ] Refactor highest-risk hotspots touched by this release.
- [ ] Add static analysis/lint gates to prevent debt growth.

### 🚦 Deployment Recommendation

🔴 Deployment blocked until the checklist above is completed.
```

---

## Scope Decisions (What I Deliberately Didn't Build)

The provided dataset has exactly one record per team/stage/metric — a snapshot, not a time series. Given that, I intentionally didn't build:
- **Trend analysis** (e.g. "coverage rising vs. declining over releases") — there's no historical time series in the input to trend against; building it would mean designing against data that doesn't exist yet.
- **Timestamp-based filtering** ("last 90 days," "last sprint") — same reason; no `metric_timestamp` field is present.
- **Duplicate-metric deduplication** — not present in this dataset; worth adding once real feeds can produce duplicates.

I'd rather name these as conscious scope calls than half-build speculative logic against data the prototype doesn't have. The three changes I did prioritize — status/threshold cross-verification, a weighted risk score, and config-driven checklist rules — directly strengthen correctness and extensibility using the data that's actually here.

---

## Zoom-Out: Scaling to Production Integration

To scale this prototype, I would separate it into three components: (1) a metrics store, (2) a decision engine, and (3) a notification adapter. The metrics store can be a warehouse table (BigQuery/Postgres/Snowflake) updated daily from engineering telemetry (CI test coverage, PR cycle time, incident data, rollback events). The decision engine would run this script logic as a stateless service or scheduled job, so it can evaluate any team/stage on demand. The notification adapter would publish markdown outputs to Slack, MS Teams, Notion, or Jira comments through channel-specific webhooks/APIs.

For CI/CD integration, I would trigger this check automatically in GitHub Actions (or GitLab CI) at stage boundaries such as pre-merge, pre-release, or deployment approval. The workflow would pass `team_name` and `current_sdlc_stage` as inputs, execute the decision engine, and then enforce policy: Healthy continues automatically, At-Risk/Critical requires manual approval or completion of generated checklist items. This creates a lightweight quality gate that is risk-aware rather than binary.

For project management integration, I would map checklist outputs into Jira/Notion tasks with ownership and due dates. Each risky metric would create structured action items linked to the release ticket, and completion status would be synced back to the deployment pipeline. Over time, I would add observability (audit logs, risk trend dashboards, false-positive tracking) and a feedback loop so remediation outcomes can tune thresholds and improve checklist quality.
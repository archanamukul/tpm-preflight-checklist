#!/usr/bin/env python3
import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import List, Tuple

STATUS_TO_RISK = {
    "good": "Healthy",
    "warning": "At-Risk",
    "poor": "Critical",
}

RISK_PENALTY = {"Healthy": 0, "At-Risk": 15, "Critical": 40}
SEVERITY_SORT_ORDER = {"Critical": 0, "At-Risk": 1, "Healthy": 2, "Unknown": 3}


@dataclass
class MetricRecord:
    team_name: str
    project_name: str
    sdlc_stage: str
    metric_name: str
    metric_value: float
    metric_unit: str
    threshold_good: float
    threshold_poor: float
    status: str


def load_records(path: str) -> List[MetricRecord]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Dataset file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError("Dataset must be a JSON array of metric records.")

    required = {
        "team_name",
        "project_name",
        "sdlc_stage",
        "metric_name",
        "metric_value",
        "metric_unit",
        "threshold_good",
        "threshold_poor",
        "status",
    }

    records: List[MetricRecord] = []
    for idx, item in enumerate(raw, start=1):
        missing = required - set(item.keys())
        if missing:
            raise ValueError(f"Record {idx} missing required fields: {sorted(missing)}")

        records.append(
            MetricRecord(
                team_name=str(item["team_name"]).strip(),
                project_name=str(item["project_name"]).strip(),
                sdlc_stage=str(item["sdlc_stage"]).strip(),
                metric_name=str(item["metric_name"]).strip(),
                metric_value=float(item["metric_value"]),
                metric_unit=str(item["metric_unit"]).strip(),
                threshold_good=float(item["threshold_good"]),
                threshold_poor=float(item["threshold_poor"]),
                status=str(item["status"]).strip(),
            )
        )
    return records


def normalize_risk(status: str) -> str:
    return STATUS_TO_RISK.get(status.lower().strip(), "Unknown")


def is_higher_better(r: MetricRecord) -> bool:
    return r.threshold_good > r.threshold_poor


def compute_threshold_severity(r: MetricRecord) -> str:
    if is_higher_better(r):
        if r.metric_value < r.threshold_poor:
            return "Critical"
        if r.metric_value < r.threshold_good:
            return "At-Risk"
        return "Healthy"
    else:
        if r.metric_value > r.threshold_poor:
            return "Critical"
        if r.metric_value > r.threshold_good:
            return "At-Risk"
        return "Healthy"


def severity_for_record(r: MetricRecord) -> str:
    mapped = normalize_risk(r.status)
    if mapped in {"Healthy", "At-Risk", "Critical"}:
        return mapped
    return compute_threshold_severity(r)


def has_status_mismatch(r: MetricRecord) -> bool:
    mapped = normalize_risk(r.status)
    if mapped not in {"Healthy", "At-Risk", "Critical"}:
        return False
    return mapped != compute_threshold_severity(r)


def summarize_overall_risk(records: List[MetricRecord]) -> str:
    severities = [severity_for_record(r) for r in records]
    if "Critical" in severities:
        return "Critical"
    if "At-Risk" in severities:
        return "At-Risk"
    if all(s == "Healthy" for s in severities):
        return "Healthy"
    return "Unknown"


def compute_risk_score(records: List[MetricRecord]) -> int:
    penalty = sum(RISK_PENALTY.get(severity_for_record(r), 0) for r in records)
    return max(0, 100 - penalty)


def sorted_by_severity(records: List[MetricRecord]) -> List[MetricRecord]:
    return sorted(records, key=lambda r: SEVERITY_SORT_ORDER.get(severity_for_record(r), 3))


def deviation_note(r: MetricRecord) -> str:
    sev = severity_for_record(r)
    if sev == "Healthy":
        return ""

    if is_higher_better(r):
        gap = r.threshold_good - r.metric_value
        return f" — target ≥{r.threshold_good:g}, actual {r.metric_value:g}, short by {gap:.1f}"
    else:
        gap = r.metric_value - r.threshold_good
        return f" — target ≤{r.threshold_good:g}, actual {r.metric_value:g}, over by {gap:.1f}"


CHECKLIST_RULES = {
    "technical debt": [
        "Reserve capacity for debt reduction in current sprint.",
        "Refactor highest-risk hotspots touched by this release.",
        "Add static analysis/lint gates to prevent debt growth.",
    ],
    "coverage": [
        "Block merge until critical-path tests meet minimum target.",
        "Add/expand unit and integration tests for changed modules.",
        "Require changed-lines coverage checks in CI.",
    ],
    "rollback risk": [
        "Verify rollback scripts and migration reversibility.",
        "Ensure canary analysis is enabled before rollout.",
        "Pre-assign incident commander and rollback owner.",
    ],
    "requirements clarity": [
        "Schedule a requirements clarification session with product stakeholders.",
        "Break ambiguous requirements into smaller, testable acceptance criteria.",
        "Get explicit sign-off from the product owner before proceeding to design.",
    ],
    "review cycle": [
        "Identify the recurring review blockers driving repeat cycles.",
        "Set a cap on review rounds before mandatory escalation.",
        "Pair each review cycle with a pre-review checklist to reduce back-and-forth.",
    ],
    "design review duration": [
        "Timebox design review sessions and pre-circulate docs 24h in advance.",
        "Identify the specific reviewers or dependencies causing delay.",
        "Escalate to the architecture lead if duration exceeds SLA twice.",
    ],
    "pr review time": [
        "Add reviewer rotation or on-call coverage to reduce review queue time.",
        "Break large PRs into smaller, independently reviewable units.",
        "Set an SLA alert for PRs open beyond the target review time.",
    ],
    "pass rate": [
        "Triage failing test cases by root cause (flaky vs. real regression).",
        "Quarantine known-flaky tests and track them separately.",
        "Block release until pass rate returns above target threshold.",
    ],
    "defect density": [
        "Prioritize defect burn-down for the highest-density modules.",
        "Add regression tests for each fixed defect to prevent recurrence.",
        "Review root-cause categories to identify systemic quality gaps.",
    ],
    "response time": [
        "Profile the slowest endpoints/queries contributing to P95 latency.",
        "Add caching or indexing for the highest-latency code paths.",
        "Set up automated performance regression alerts in CI.",
    ],
}

DEFAULT_CHECKLIST_ACTIONS = [
    "Review metric trend and identify top root causes.",
    "Add a mitigation task before advancing stage.",
]
CRITICAL_ESCALATION_ACTION = "Require explicit go/no-go approval from tech lead."


def checklist_actions(metric_name: str, severity: str) -> List[str]:
    name = metric_name.lower()
    for keyword, actions in CHECKLIST_RULES.items():
        if keyword in name:
            return list(actions)
    actions = list(DEFAULT_CHECKLIST_ACTIONS)
    if severity == "Critical":
        actions.append(CRITICAL_ESCALATION_ACTION)
    return actions


def deployment_recommendation_line(overall: str) -> str:
    key = str(overall).strip().lower()
    mapping = {
        "healthy": "🟢 Proceed automatically — no gating required.",
        "at-risk": "🟡 Engineering Manager approval required before proceeding.",
        "critical": "🔴 Deployment blocked until the checklist above is completed.",
    }
    return mapping.get(key, "🟡 Manual review required — risk level could not be determined.")


def append_deployment_recommendation(lines: List[str], overall: str) -> str:
    while lines and lines[-1] == "":
        lines.pop()
    lines.extend(
        [
            "",
            "### 🚦 Deployment Recommendation",
            "",
            deployment_recommendation_line(overall),
        ]
    )
    return "\n".join(lines).rstrip()


def find_records(records: List[MetricRecord], team_name: str, stage: str) -> Tuple[List[MetricRecord], str]:
    team_matches = [r for r in records if r.team_name.lower() == team_name.lower()]
    if not team_matches:
        return [], f"Team '{team_name}' not found in dataset."

    stage_matches = [r for r in team_matches if r.sdlc_stage.lower() == stage.lower()]
    if not stage_matches:
        available = sorted({r.sdlc_stage for r in team_matches})
        return [], f"Stage '{stage}' not found for team '{team_name}'. Available stages: {', '.join(available)}"

    return stage_matches, ""


def format_markdown(team: str, stage: str, records: List[MetricRecord]) -> str:
    project_str = ", ".join(sorted({r.project_name for r in records}))
    overall = summarize_overall_risk(records)
    risk_score = compute_risk_score(records)
    mismatches = [r for r in records if has_status_mismatch(r)]
    ordered = sorted_by_severity(records)

    lines = [
        "## 🚦 SDLC Pre-Flight Checklist",
        f"- **Team:** {team}",
        f"- **Project(s):** {project_str}",
        f"- **Current Stage:** {stage}",
        f"- **Risk Level:** **{overall}** (Risk Score: {risk_score}/100)",
        "",
    ]

    def signal_line(r: MetricRecord) -> str:
        sev = severity_for_record(r)
        line = (
            f"- **{r.metric_name}**: {r.metric_value} {r.metric_unit} "
            f"(good: {r.threshold_good}, poor: {r.threshold_poor}) "
            f"→ **{sev}** (source status: {r.status})"
        )
        if has_status_mismatch(r):
            computed = compute_threshold_severity(r)
            line += (
                f"\n  - ⚠️ *Data inconsistency: recorded status maps to **{sev}**, "
                f"but the raw value against threshold computes to **{computed}**. "
                f"Verify which is correct before treating this as ground truth.*"
            )
        return line

    if overall == "Healthy" and not mismatches:
        lines += [
            "✅ Historical indicators look healthy for this stage.",
            "Keep current engineering hygiene and proceed with confidence.",
        ]
        return append_deployment_recommendation(lines, overall)

    if overall == "Healthy" and mismatches:
        lines += [
            "✅ Historical indicators are officially healthy for this stage,",
            "but the data has inconsistencies worth a second look before proceeding.",
            "",
            f"⚠️ {len(mismatches)} metric(s) show a status/threshold mismatch:",
            "",
            "### 📊 Risk Signals",
            "",
        ]
        for r in ordered:
            lines.append(signal_line(r))
        return append_deployment_recommendation(lines, overall)

    lines += ["### 📊 Risk Signals", ""]
    for r in ordered:
        lines.append(signal_line(r))

    risky = [r for r in ordered if severity_for_record(r) in {"At-Risk", "Critical"}]
    if risky:
        lines += ["", "### 🎯 Top Risk Drivers", ""]
        for r in risky:
            icon = "🔴" if severity_for_record(r) == "Critical" else "🟠"
            lines.append(f"- {icon} {r.metric_name} ({severity_for_record(r)}){deviation_note(r)}")

    lines += ["", "### ✅ Targeted Due-Diligence Checklist", ""]
    for r in ordered:
        sev = severity_for_record(r)
        if sev not in {"At-Risk", "Critical"}:
            continue
        lines.append(f"#### {r.metric_name} ({sev})")
        for action in checklist_actions(r.metric_name, sev):
            lines.append(f"- [ ] {action}")
        lines.append("")

    return append_deployment_recommendation(lines, overall)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate SDLC pre-flight checklist notification.")
    parser.add_argument("--team-name", required=True)
    parser.add_argument("--current-sdlc-stage", required=True)
    parser.add_argument("--data-path", default="data_metrics.json")
    args = parser.parse_args()

    try:
        records = load_records(args.data_path)
        stage_records, error = find_records(records, args.team_name, args.current_sdlc_stage)
        if error:
            print(f"❌ {error}")
            return 2

        print(format_markdown(args.team_name, args.current_sdlc_stage, stage_records))
        return 0
    except Exception as exc:
        print(f"❌ Failed to generate checklist: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

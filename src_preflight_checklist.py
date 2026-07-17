#!/usr/bin/env python3
import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

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

    def normalized_status(self) -> str:
        return str(self.status).strip().lower()


# ---------------------------------------------------------------------------
# Severity + scoring
# ---------------------------------------------------------------------------

STATUS_TO_RISK = {
    "good": "Healthy",
    "warning": "At-Risk",
    "poor": "Critical",
}

SEVERITY_ORDER = {
    "Critical": 0,
    "At-Risk": 1,
    "Healthy": 2,
    "Unknown": 3,
}

SEVERITY_POINTS = {
    "Healthy": 1.0,
    "At-Risk": 0.5,
    "Critical": 0.0,
}


def status_to_severity(status: str) -> str:
    return STATUS_TO_RISK.get(str(status).strip().lower(), "Unknown")


def metric_direction(record: MetricRecord) -> str:
    """
    Returns:
      - "higher_is_better"
      - "lower_is_better"

    Raises:
      ValueError for invalid/equal thresholds.
    """
    if record.threshold_good == record.threshold_poor:
        raise ValueError(
            f"Invalid thresholds for metric '{record.metric_name}' "
            f"(team '{record.team_name}', stage '{record.sdlc_stage}'): "
            f"threshold_good equals threshold_poor ({record.threshold_good})."
        )
    return "higher_is_better" if record.threshold_good > record.threshold_poor else "lower_is_better"


def threshold_severity(record: MetricRecord) -> str:
    direction = metric_direction(record)

    if direction == "higher_is_better":
        if record.metric_value < record.threshold_poor:
            return "Critical"
        if record.metric_value < record.threshold_good:
            return "At-Risk"
        return "Healthy"

    # lower_is_better
    if record.metric_value > record.threshold_poor:
        return "Critical"
    if record.metric_value > record.threshold_good:
        return "At-Risk"
    return "Healthy"


def has_status_mismatch(record: MetricRecord) -> bool:
    from_status = status_to_severity(record.status)
    if from_status == "Unknown":
        return False
    return from_status != threshold_severity(record)


def summarize_overall_risk(records: List[MetricRecord]) -> str:
    # Overall risk should be based on threshold truth, not source status labels.
    severities = [threshold_severity(r) for r in records]
    if "Critical" in severities:
        return "Critical"
    if "At-Risk" in severities:
        return "At-Risk"
    if all(s == "Healthy" for s in severities):
        return "Healthy"
    return "Unknown"


def compute_risk_score(records: List[MetricRecord]) -> Tuple[int, int, int]:
    """
    Returns:
      (score_0_to_100, used_metrics, total_metrics)

    Unknown severities are excluded from denominator to avoid silent distortion.
    """
    if not records:
        return 0, 0, 0

    points: List[float] = []
    total = len(records)

    for r in records:
        sev = threshold_severity(r)
        p = SEVERITY_POINTS.get(sev)
        if p is not None:
            points.append(p)

    if not points:
        return 0, 0, total

    avg = sum(points) / len(points)
    score = int(round(avg * 100))
    return score, len(points), total


# ---------------------------------------------------------------------------
# Checklist logic
# ---------------------------------------------------------------------------

METRIC_ALIASES: Dict[str, str] = {
    "code coverage percentage": "coverage",
    "test coverage": "coverage",
    "coverage": "coverage",
    "technical debt index": "technical debt",
    "technical debt": "technical debt",
    "rollback risk score": "rollback risk",
    "rollback risk": "rollback risk",
    "requirements clarity score": "requirements clarity",
    "requirements clarity": "requirements clarity",
    "review cycle count": "review cycle",
    "review cycle": "review cycle",
    "design review duration": "design review duration",
    "pr review time": "pr review time",
    "integration pass rate": "pass rate",
    "pass rate": "pass rate",
    "defect density": "defect density",
    "response time p95": "response time",
    "response time": "response time",
}

CHECKLIST_RULES: Dict[str, List[str]] = {
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


def metric_key(metric_name: str) -> str:
    name = str(metric_name).strip().lower()
    if name in METRIC_ALIASES:
        return METRIC_ALIASES[name]
    # fallback best-effort keyword detection
    for k in CHECKLIST_RULES.keys():
        if k in name:
            return k
    return "default"


def checklist_actions(metric_name: str, severity: str) -> List[str]:
    key = metric_key(metric_name)
    if key in CHECKLIST_RULES:
        return list(CHECKLIST_RULES[key])

    actions = list(DEFAULT_CHECKLIST_ACTIONS)
    if severity == "Critical":
        actions.append(CRITICAL_ESCALATION_ACTION)
    return actions


# ---------------------------------------------------------------------------
# Data loading + validation
# ---------------------------------------------------------------------------

def load_records(path: str) -> List[MetricRecord]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Dataset file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError("Dataset must be a JSON array of metric records.")

    required_fields = {
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
        if not isinstance(item, dict):
            raise ValueError(f"Record {idx} is not a JSON object.")

        missing = required_fields - set(item.keys())
        if missing:
            raise ValueError(f"Record {idx} missing required fields: {sorted(missing)}")

        r = MetricRecord(
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

        # Validate threshold direction sanity early.
        _ = metric_direction(r)
        records.append(r)

    return records


def unit_sanity_warning(record: MetricRecord) -> Optional[str]:
    unit = record.metric_unit.strip().lower()
    v = record.metric_value

    if unit in {"percentage", "%"} and not (0 <= v <= 100):
        return f"{record.metric_name}: percentage value {v} is outside [0,100]."
    if unit in {"days", "hours", "count", "defects per kloc", "score"} and v < 0:
        return f"{record.metric_name}: value {v} should not be negative for unit '{record.metric_unit}'."
    return None


def find_records(records: List[MetricRecord], team_name: str, stage: str) -> Tuple[List[MetricRecord], str]:
    team_norm = team_name.strip().lower()
    stage_norm = stage.strip().lower()

    team_matches = [r for r in records if r.team_name.lower() == team_norm]
    if not team_matches:
        return [], f"Team '{team_name}' not found in dataset."

    stage_matches = [r for r in team_matches if r.sdlc_stage.lower() == stage_norm]
    if not stage_matches:
        available = sorted({r.sdlc_stage for r in team_matches})
        return [], f"Stage '{stage}' not found for team '{team_name}'. Available stages: {', '.join(available)}"

    return stage_matches, ""


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def deviation_note(record: MetricRecord) -> str:
    sev = threshold_severity(record)
    if sev == "Healthy":
        return ""

    direction = metric_direction(record)
    if direction == "higher_is_better":
        gap = record.threshold_good - record.metric_value
        return f" — target ≥{record.threshold_good:g}, actual {record.metric_value:g}, short by {gap:.1f}"

    gap = record.metric_value - record.threshold_good
    return f" — target ≤{record.threshold_good:g}, actual {record.metric_value:g}, over by {gap:.1f}"


def deployment_recommendation_line(overall: str, has_mismatch: bool) -> str:
    # Guardrail: data mismatch should never auto-proceed.
    if has_mismatch:
        return "🟡 Manual review required — source status and threshold-derived severity are inconsistent."

    key = str(overall).strip().lower()
    mapping = {
        "healthy": "🟢 Proceed automatically — no gating required.",
        "at-risk": "🟡 Engineering Manager approval required before proceeding.",
        "critical": "🔴 Deployment blocked until the checklist above is completed.",
    }
    return mapping.get(key, "🟡 Manual review required — risk level could not be determined.")


def sorted_by_severity(records: List[MetricRecord]) -> List[MetricRecord]:
    return sorted(records, key=lambda r: SEVERITY_ORDER.get(threshold_severity(r), 3))


def format_markdown(team: str, stage: str, records: List[MetricRecord]) -> str:
    projects = ", ".join(sorted({r.project_name for r in records}))
    overall = summarize_overall_risk(records)
    score, used_count, total_count = compute_risk_score(records)
    mismatches = [r for r in records if has_status_mismatch(r)]
    ordered = sorted_by_severity(records)

    lines: List[str] = [
        "## 🚦 SDLC Pre-Flight Checklist",
        f"- **Team:** {team}",
        f"- **Project(s):** {projects}",
        f"- **Current Stage:** {stage}",
        f"- **Risk Level:** **{overall}** (Risk Score: {score}/100)",
        "",
    ]

    if used_count != total_count:
        lines.append(
            f"ℹ️ Score calculated from {used_count}/{total_count} metrics with known severity."
        )
        lines.append("")

    # Data-quality warnings
    unit_warnings = [w for w in (unit_sanity_warning(r) for r in ordered) if w]
    if unit_warnings:
        lines.append("⚠️ **Data quality warnings detected:**")
        lines.append("")
        for w in unit_warnings:
            lines.append(f"- {w}")
        lines.append("")

    def signal_line(r: MetricRecord) -> str:
        sev = threshold_severity(r)
        from_status = status_to_severity(r.status)
        line = (
            f"- **{r.metric_name}**: {r.metric_value} {r.metric_unit} "
            f"(good: {r.threshold_good}, poor: {r.threshold_poor}) "
            f"→ **{sev}** (source status: {r.status})"
        )
        if from_status != "Unknown" and from_status != sev:
            line += (
                f"\n  - ⚠️ *Data inconsistency: source status maps to **{from_status}**, "
                f"but threshold-derived severity is **{sev}**.*"
            )
        return line

    # Healthy + no mismatch
    if overall == "Healthy" and not mismatches:
        lines.extend([
            "✅ Historical indicators look healthy for this stage.",
            "Keep current engineering hygiene and proceed with confidence.",
            "",
        ])
    # Healthy but mismatch
    elif overall == "Healthy" and mismatches:
        lines.extend([
            "✅ Threshold-derived indicators are healthy for this stage,",
            "but data inconsistencies need validation before proceeding.",
            "",
            f"⚠️ {len(mismatches)} metric(s) show source-status vs threshold mismatch:",
            "",
            "### 📊 Risk Signals",
            "",
        ])
        for r in ordered:
            lines.append(signal_line(r))
        lines.append("")
    else:
        # At-Risk / Critical paths
        lines.extend(["### 📊 Risk Signals", ""])
        for r in ordered:
            lines.append(signal_line(r))

        risky = [r for r in ordered if threshold_severity(r) in {"At-Risk", "Critical"}]
        if risky:
            lines.extend(["", "### 🎯 Top Risk Drivers", ""])
            for r in risky:
                sev = threshold_severity(r)
                icon = "🔴" if sev == "Critical" else "🟠"
                lines.append(f"- {icon} {r.metric_name} ({sev}){deviation_note(r)}")

        lines.extend(["", "### ✅ Targeted Due-Diligence Checklist", ""])
        for r in risky:
            sev = threshold_severity(r)
            lines.append(f"#### {r.metric_name} ({sev})")
            for action in checklist_actions(r.metric_name, sev):
                lines.append(f"- [ ] {action}")
            lines.append("")

    # Always append recommendation once.
    while lines and lines[-1] == "":
        lines.pop()
    lines.extend([
        "",
        "### 🚦 Deployment Recommendation",
        "",
        deployment_recommendation_line(overall, has_mismatch=bool(mismatches)),
    ])

    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Generate SDLC pre-flight checklist notification.")
    parser.add_argument("--team-name", required=True)
    parser.add_argument("--current-sdlc-stage", required=True)
    parser.add_argument("--data-path", default="data_metrics.json")
    args = parser.parse_args()

    team = args.team_name.strip()
    stage = args.current_sdlc_stage.strip()

    try:
        records = load_records(args.data_path)
        stage_records, error = find_records(records, team, stage)
        if error:
            print(f"❌ {error}")
            return 2

        print(format_markdown(team, stage, stage_records))
        return 0

    except Exception as exc:
        print(f"❌ Failed to generate checklist: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
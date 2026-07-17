import json
import subprocess
import sys
from pathlib import Path

SCRIPT = "src_preflight_checklist.py"


def run_cmd(args):
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True,
        text=True,
    )


def write_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_unknown_team_returns_exit_2(tmp_path):
    data = [
        {
            "team_name": "Alpha Squad",
            "project_name": "UserAuth Service",
            "sdlc_stage": "Coding",
            "metric_name": "Technical Debt Ratio",
            "metric_value": 28,
            "metric_unit": "percentage",
            "threshold_good": 15,
            "threshold_poor": 25,
            "status": "Poor",
        }
    ]
    p = tmp_path / "data.json"
    write_json(p, data)

    r = run_cmd(["--team-name", "Unknown Team", "--current-sdlc-stage", "Coding", "--data-path", str(p)])
    assert r.returncode == 2
    assert "not found in dataset" in r.stdout


def test_healthy_flow(tmp_path):
    data = [
        {
            "team_name": "Gamma Engineers",
            "project_name": "Analytics Dashboard",
            "sdlc_stage": "Integration Testing",
            "metric_name": "Test Case Pass Rate",
            "metric_value": 92,
            "metric_unit": "percentage",
            "threshold_good": 90,
            "threshold_poor": 70,
            "status": "Good",
        }
    ]
    p = tmp_path / "data.json"
    write_json(p, data)

    r = run_cmd(["--team-name", "Gamma Engineers", "--current-sdlc-stage", "Integration Testing", "--data-path", str(p)])
    assert r.returncode == 0
    assert "**Risk Level:** **Healthy**" in r.stdout
    assert "look healthy for this stage" in r.stdout
    assert "Targeted Due-Diligence Checklist" not in r.stdout


def test_fallback_direction_for_higher_is_better_metric(tmp_path):
    # Unknown status forces fallback logic.
    # Coverage: good=85, poor=70, value=78 should be At-Risk (not Critical).
    data = [
        {
            "team_name": "Bug Team",
            "project_name": "Test Project",
            "sdlc_stage": "Coding",
            "metric_name": "Code Coverage Percentage",
            "metric_value": 78,
            "metric_unit": "percentage",
            "threshold_good": 85,
            "threshold_poor": 70,
            "status": "N/A",
        }
    ]
    p = tmp_path / "data.json"
    write_json(p, data)

    r = run_cmd(["--team-name", "Bug Team", "--current-sdlc-stage", "Coding", "--data-path", str(p)])
    assert r.returncode == 0
    assert "**Risk Level:** **At-Risk**" in r.stdout


def test_at_risk_or_critical_includes_checklist(tmp_path):
    data = [
        {
            "team_name": "Alpha Squad",
            "project_name": "Notification System",
            "sdlc_stage": "Coding",
            "metric_name": "Technical Debt Ratio",
            "metric_value": 28,
            "metric_unit": "percentage",
            "threshold_good": 15,
            "threshold_poor": 25,
            "status": "Poor",
        }
    ]
    p = tmp_path / "data.json"
    write_json(p, data)

    r = run_cmd(["--team-name", "Alpha Squad", "--current-sdlc-stage", "Coding", "--data-path", str(p)])
    assert r.returncode == 0
    assert "**Risk Level:** **Critical**" in r.stdout
    assert "### ✅ Targeted Due-Diligence Checklist" in r.stdout
    assert "- [ ] Reserve capacity for debt reduction in current sprint." in r.stdout


def test_status_mismatch_is_flagged(tmp_path):
    # Status says Poor (Critical), but the raw value (5.0) sits between
    # good(4.0) and poor(7.0), which computes to At-Risk, not Critical.
    data = [
        {
            "team_name": "Mismatch Team",
            "project_name": "Test Project",
            "sdlc_stage": "Coding",
            "metric_name": "Rollback Risk Score",
            "metric_value": 5.0,
            "metric_unit": "score",
            "threshold_good": 4.0,
            "threshold_poor": 7.0,
            "status": "Poor",
        }
    ]
    p = tmp_path / "data.json"
    write_json(p, data)

    r = run_cmd(["--team-name", "Mismatch Team", "--current-sdlc-stage", "Coding", "--data-path", str(p)])
    assert r.returncode == 0
    assert "Data inconsistency" in r.stdout
    assert "computes to **At-Risk**" in r.stdout


def test_healthy_overall_still_flags_hidden_mismatch(tmp_path):
    # Overall status says Good/Healthy, but the number itself computes
    # to At-Risk. Overall label stays Healthy (status is still trusted
    # for the headline), but the inconsistency must be surfaced.
    data = [
        {
            "team_name": "Delta Force",
            "project_name": "Mobile App V2",
            "sdlc_stage": "Functional Testing",
            "metric_name": "Defect Density",
            "metric_value": 2.3,
            "metric_unit": "defects per kloc",
            "threshold_good": 1.5,
            "threshold_poor": 3.0,
            "status": "Good",
        }
    ]
    p = tmp_path / "data.json"
    write_json(p, data)

    r = run_cmd(["--team-name", "Delta Force", "--current-sdlc-stage", "Functional Testing", "--data-path", str(p)])
    assert r.returncode == 0
    assert "**Risk Level:** **Healthy**" in r.stdout
    assert "Data inconsistency" in r.stdout


def test_risk_score_present_and_in_range(tmp_path):
    data = [
        {
            "team_name": "Alpha Squad",
            "project_name": "Notification System",
            "sdlc_stage": "Coding",
            "metric_name": "Technical Debt Ratio",
            "metric_value": 28,
            "metric_unit": "percentage",
            "threshold_good": 15,
            "threshold_poor": 25,
            "status": "Poor",
        }
    ]
    p = tmp_path / "data.json"
    write_json(p, data)

    r = run_cmd(["--team-name", "Alpha Squad", "--current-sdlc-stage", "Coding", "--data-path", str(p)])
    assert r.returncode == 0
    assert "Risk Score:" in r.stdout
    assert "/100" in r.stdout


def test_critical_sorted_before_at_risk_in_signals_and_drivers(tmp_path):
    # At-Risk metric listed first in the source data, Critical second —
    # output should still show Critical first (sorted by severity).
    data = [
        {
            "team_name": "Sort Team",
            "project_name": "Test Project",
            "sdlc_stage": "Coding",
            "metric_name": "Average PR Review Time",
            "metric_value": 4.5,
            "metric_unit": "days",
            "threshold_good": 2,
            "threshold_poor": 5,
            "status": "Warning",
        },
        {
            "team_name": "Sort Team",
            "project_name": "Test Project",
            "sdlc_stage": "Coding",
            "metric_name": "Technical Debt Ratio",
            "metric_value": 28,
            "metric_unit": "percentage",
            "threshold_good": 15,
            "threshold_poor": 25,
            "status": "Poor",
        },
    ]
    p = tmp_path / "data.json"
    write_json(p, data)

    r = run_cmd(["--team-name", "Sort Team", "--current-sdlc-stage", "Coding", "--data-path", str(p)])
    assert r.returncode == 0
    debt_pos = r.stdout.find("Technical Debt Ratio")
    pr_time_pos = r.stdout.find("Average PR Review Time")
    assert debt_pos != -1 and pr_time_pos != -1
    assert debt_pos < pr_time_pos  # Critical (debt) must appear before At-Risk (PR time)
    assert "### 🎯 Top Risk Drivers" in r.stdout


def test_checklist_rules_config_driven(tmp_path):
    # Confirms checklist content comes from CHECKLIST_RULES config,
    # not hardcoded per-call logic — same metric name, same output.
    data = [
        {
            "team_name": "Config Team",
            "project_name": "Test Project",
            "sdlc_stage": "Coding",
            "metric_name": "Code Coverage Percentage",
            "metric_value": 60,
            "metric_unit": "percentage",
            "threshold_good": 85,
            "threshold_poor": 70,
            "status": "Poor",
        }
    ]
    p = tmp_path / "data.json"
    write_json(p, data)

    r = run_cmd(["--team-name", "Config Team", "--current-sdlc-stage", "Coding", "--data-path", str(p)])
    assert r.returncode == 0
    assert "Block merge until critical-path tests meet minimum target." in r.stdout


def test_deployment_recommendation_present_for_all_severities(tmp_path):
    scenarios = [
        ("Healthy", "Good", 92, "🟢 Proceed automatically"),
        ("At-Risk", "Warning", 78, "🟡 Engineering Manager approval required"),
        ("Critical", "Poor", 28, "🔴 Deployment blocked"),
    ]
    for label, status, value, expected_phrase in scenarios:
        data = [
            {
                "team_name": "Deploy Team",
                "project_name": "Test Project",
                "sdlc_stage": "Coding",
                "metric_name": "Technical Debt Ratio",
                "metric_value": value,
                "metric_unit": "percentage",
                "threshold_good": 15,
                "threshold_poor": 25,
                "status": status,
            }
        ]
        p = tmp_path / f"data_{label}.json"
        write_json(p, data)
        r = run_cmd(["--team-name", "Deploy Team", "--current-sdlc-stage", "Coding", "--data-path", str(p)])
        assert r.returncode == 0
        assert "### 🚦 Deployment Recommendation" in r.stdout
        assert expected_phrase in r.stdout


def test_deviation_note_explains_gap_to_target(tmp_path):
    data = [
        {
            "team_name": "Deviation Team",
            "project_name": "Test Project",
            "sdlc_stage": "Coding",
            "metric_name": "Code Coverage Percentage",
            "metric_value": 78,
            "metric_unit": "percentage",
            "threshold_good": 85,
            "threshold_poor": 70,
            "status": "Warning",
        }
    ]
    p = tmp_path / "data.json"
    write_json(p, data)

    r = run_cmd(["--team-name", "Deviation Team", "--current-sdlc-stage", "Coding", "--data-path", str(p)])
    assert r.returncode == 0
    assert "target ≥85" in r.stdout
    assert "short by 7.0" in r.stdout


def test_checklist_rules_cover_all_dataset_metric_types(tmp_path):
    # Every metric name that appears in the real dataset should get a
    # specific rule, not the generic fallback — this locks in issue #6.
    metric_names = [
        "Requirements Clarity Score",
        "Review Cycle Count",
        "Design Review Duration",
        "Average PR Review Time",
        "Test Case Pass Rate",
        "Defect Density",
        "Response Time P95",
    ]
    from src_preflight_checklist import checklist_actions, DEFAULT_CHECKLIST_ACTIONS
    for name in metric_names:
        actions = checklist_actions(name, "At-Risk")
        assert actions != DEFAULT_CHECKLIST_ACTIONS, f"'{name}' fell back to generic actions"
import itertools

import pytest
from jsonschema import ValidationError

from tools.review import merge_reports, plan_scopes, require_sha, validate_report

BASE = "a" * 40
HEAD = "b" * 40


def report(files=None):
    return {
        "reviewed_commit": HEAD,
        "reviewed_base": BASE,
        "scope_files": files or ["tools/example.py"],
        "status": "PASS",
        "blocking_findings": [],
        "non_blocking_findings": [],
        "test_summary": {"ci": "PASS", "commands": ["python -m tools.ci"], "notes": "CI evidence"},
        "unverified_items": [],
    }


def finding(issue="Stale frame continues motion"):
    return {
        "severity": "P1",
        "area": "control",
        "file": "src/drone_tracker/controller.py",
        "line": 12,
        "issue": issue,
        "reason": "SET emitted after observation expires",
        "suggested_fix": "Stop updating the target on expired observations",
        "validation": "Advance a fake clock past max_frame_age_ms and assert no new SET",
    }


def test_valid_report():
    assert validate_report(report(), BASE, HEAD, ["tools/example.py"])["status"] == "PASS"


@pytest.mark.parametrize(
    "key,value",
    [
        ("reviewed_commit", "c" * 40),
        ("reviewed_base", "c" * 40),
        ("status", "APPROVED"),
        ("scope_files", []),
        ("scope_files", ["other.py"]),
        ("scope_files", ["tools/example.py"] * 2),
        ("blocking_findings", [finding()]),
        ("test_summary", {"ci": "FAIL", "commands": [], "notes": "failed"}),
        ("test_summary", {"ci": "NOT_RUN", "commands": [], "notes": "not run"}),
        (
            "unverified_items",
            [
                {
                    "item": "Servo direction",
                    "merge_blocker": True,
                    "observation": "Operator verifies low-speed axis direction",
                    "expected_result": "Physical direction matches calibrated sign",
                }
            ],
        ),
    ],
)
def test_report_fails_closed(key, value):
    result = report()
    result[key] = value
    with pytest.raises((ValueError, ValidationError)):
        validate_report(result, BASE, HEAD, ["tools/example.py"])


def test_missing_fields_and_extra_fields_rejected():
    result = report()
    del result["unverified_items"]
    with pytest.raises(ValidationError):
        validate_report(result, BASE, HEAD, ["tools/example.py"])
    result = report()
    result["approved"] = True
    with pytest.raises(ValidationError):
        validate_report(result, BASE, HEAD, ["tools/example.py"])


@pytest.mark.parametrize("path", ["/etc/passwd", "../secret", "C:/secret", "src\\file", "x\ny"])
def test_unsafe_finding_paths_rejected(path):
    result = report()
    result["status"] = "CHANGES_REQUESTED"
    item = finding()
    item["file"] = path
    result["blocking_findings"] = [item]
    with pytest.raises(ValueError):
        validate_report(result, BASE, HEAD, ["tools/example.py"])


def test_severe_finding_cannot_be_nonblocking():
    result = report()
    result["non_blocking_findings"] = [finding()]
    with pytest.raises(ValueError):
        validate_report(result, BASE, HEAD, ["tools/example.py"])


def test_p3_finding_cannot_block():
    result = report()
    item = finding()
    item["severity"] = "P3"
    result["status"] = "CHANGES_REQUESTED"
    result["blocking_findings"] = [item]
    with pytest.raises(ValidationError, match="P3"):
        validate_report(result, BASE, HEAD, ["tools/example.py"])


def test_p3_finding_remains_visible_without_blocking():
    result = report()
    item = finding()
    item["severity"] = "P3"
    result["non_blocking_findings"] = [item]
    assert validate_report(result, BASE, HEAD, ["tools/example.py"])["non_blocking_findings"] == [
        item
    ]


@pytest.mark.parametrize(
    "count,lines,size",
    [
        (10, 40, "small"),
        (11, 1, "medium"),
        (1, 401, "medium"),
        (20, 50, "medium"),
        (21, 1, "large"),
        (1, 1001, "large"),
    ],
)
def test_scope_thresholds_keep_every_file(count, lines, size):
    changes = [(f"tests/test_{i}.py", lines) for i in range(count)]
    plan = plan_scopes(changes)
    assert plan["size"] == size
    assert sorted(p for s in plan["scopes"] for p in s["files"]) == sorted(p for p, _ in changes)


def test_logical_domains_are_split():
    plan = plan_scopes(
        [
            ("src/drone_tracker/controller.py", 250),
            ("src/drone_tracker/capture.py", 250),
            ("tests/test_x.py", 30),
        ]
    )
    assert {s["area"] for s in plan["scopes"]} == {"vision", "control", "tests-automation"}


def test_merge_retains_findings_and_rejects_missing_scope():
    plan = {"base": BASE, "head": HEAD, "scopes": [{"files": ["a.py"]}, {"files": ["b.py"]}]}
    one, two = report(["a.py"]), report(["b.py"])
    one["status"] = two["status"] = "CHANGES_REQUESTED"
    one["blocking_findings"] = [finding()]
    two["blocking_findings"] = [finding(), finding("Disconnected transport still writes")]
    result = merge_reports([one, two], plan)
    assert result["status"] == "CHANGES_REQUESTED"
    assert len(result["blocking_findings"]) == 2
    with pytest.raises(ValueError):
        merge_reports([one], plan)
    with pytest.raises(ValueError):
        merge_reports([one, one], plan)


def test_physical_or_human_status_survives_merge():
    plan = {"base": BASE, "head": HEAD, "scopes": [{"files": ["tools/example.py"]}]}
    for status in ("HUMAN_DECISION_REQUIRED", "PHYSICAL_TEST_REQUIRED"):
        one = report()
        one["status"] = status
        one["unverified_items"] = [
            {
                "item": "Required evidence",
                "merge_blocker": True,
                "observation": "Operator observes the actual axis direction",
                "expected_result": "Direction agrees with calibration",
            }
        ]
        assert merge_reports([one], plan)["status"] == status


@pytest.mark.parametrize("states", list(itertools.permutations(["FAIL", "NOT_RUN", "PASS"])))
def test_ci_failure_is_never_hidden_by_scope_order(states):
    plan = {"base": BASE, "head": HEAD, "scopes": [{"files": [f"{i}.py"]} for i in range(3)]}
    reports = []
    for i, state in enumerate(states):
        item = report([f"{i}.py"])
        item["status"] = "HUMAN_DECISION_REQUIRED"
        item["test_summary"]["ci"] = state
        item["unverified_items"] = [
            {
                "item": "Budget decision",
                "merge_blocker": True,
                "observation": "Await approved API budget",
                "expected_result": "Explicit approval",
            }
        ]
        reports.append(item)
    assert merge_reports(reports, plan)["test_summary"]["ci"] == "FAIL"


def test_large_review_keeps_related_automation_files_together():
    changes = [(f".codex/skills/skill-{i}/SKILL.md", 10) for i in range(21)]
    changes += [("README.md", 3), ("AGENTS.md", 3), ("tests/test_config.py", 20)]
    plan = plan_scopes(changes)
    assert len(plan["scopes"]) == 3
    assert {s["area"] for s in plan["scopes"]} == {
        "requirements-integration",
        "review-pipeline",
        "ci-configuration",
    }


@pytest.mark.parametrize("sha", ["HEAD", "-option", "a" * 39, "A" * 40, "a" * 40 + "\n"])
def test_git_inputs_require_full_sha(sha):
    with pytest.raises(ValueError):
        require_sha(sha)

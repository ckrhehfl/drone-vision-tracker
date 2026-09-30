import copy
import itertools

import pytest
from jsonschema import ValidationError

from tools.review import merge_reports, plan_scopes, require_sha, validate_report
from tools.review_context import current_context, eligible, validate_ci_evidence

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


@pytest.fixture
def ci_evidence(github_metadata):
    run, pr = github_metadata
    run.update(id=100, run_attempt=1, html_url="https://github.com/owner/project/actions/runs/100")
    pr.update(number=1, user={"login": "owner"})
    pr["base"]["sha"] = BASE
    latest = {"workflow_runs": [copy.deepcopy(run)]}
    jobs = {
        "total_count": 1,
        "jobs": [
            {
                "name": "software-checks",
                "head_sha": HEAD,
                "status": "completed",
                "conclusion": "success",
                "steps": [
                    {
                        "name": "Run software checks",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
            }
        ],
    }
    return run, latest, jobs, pr


def test_latest_successful_run_and_test_step_are_accepted(ci_evidence):
    run, latest, jobs, _ = ci_evidence
    validate_ci_evidence(run, latest, jobs)


@pytest.mark.parametrize("state", ["in_progress", "failure", "cancelled", "skipped", "success"])
def test_old_success_is_rejected_after_new_run(ci_evidence, state):
    run, latest, jobs, _ = ci_evidence
    newer = copy.deepcopy(run)
    newer.update(
        id=101,
        status="in_progress" if state == "in_progress" else "completed",
        conclusion=None if state == "in_progress" else state,
    )
    latest["workflow_runs"].append(newer)
    with pytest.raises(ValueError, match="supersedes"):
        validate_ci_evidence(run, latest, jobs)


def test_new_attempt_supersedes_old_success(ci_evidence):
    run, latest, jobs, _ = ci_evidence
    latest["workflow_runs"][0].update(run_attempt=2, status="in_progress", conclusion=None)
    with pytest.raises(ValueError, match="supersedes"):
        validate_ci_evidence(run, latest, jobs)


@pytest.mark.parametrize(
    "change",
    [
        "missing-job",
        "missing-step",
        "pending-step",
        "skipped-step",
        "failed-job",
        "stale-job",
        "incomplete-jobs",
    ],
)
def test_incomplete_or_failed_job_cannot_support_pass(ci_evidence, change):
    run, latest, jobs, _ = ci_evidence
    if change == "missing-job":
        jobs["jobs"] = []
        jobs["total_count"] = 0
    elif change == "missing-step":
        jobs["jobs"][0]["steps"] = []
    elif change == "pending-step":
        jobs["jobs"][0]["steps"][0].update(status="pending", conclusion=None)
    elif change == "skipped-step":
        jobs["jobs"][0]["steps"][0]["conclusion"] = "skipped"
    elif change == "failed-job":
        jobs["jobs"][0]["conclusion"] = "failure"
    elif change == "stale-job":
        jobs["jobs"][0]["head_sha"] = BASE
    else:
        jobs["total_count"] = 2
    with pytest.raises(ValueError):
        validate_ci_evidence(run, latest, jobs)


def test_context_rechecks_run_attempt_and_jobs_via_api(ci_evidence, monkeypatch):
    run, latest, jobs, pr = ci_evidence
    responses = {
        "repos/owner/project/actions/runs/100": run,
        (
            "repos/owner/project/actions/workflows/ci.yml/runs"
            f"?event=pull_request&head_sha={HEAD}&per_page=100"
        ): latest,
        "repos/owner/project/actions/runs/100/attempts/1/jobs?per_page=100": jobs,
        f"repos/owner/project/commits/{HEAD}/pulls?per_page=100": [pr],
        "repos/owner/project/pulls/1": pr,
        "repos/owner/project/collaborators/owner/permission": {"permission": "write"},
    }
    monkeypatch.setattr("tools.review_context.api", responses.__getitem__)
    initial = current_context("owner/project", 100)
    assert initial["ci_run_attempt"] == 1
    jobs["jobs"][0]["steps"][0]["status"] = "in_progress"
    with pytest.raises(ValueError, match="step evidence"):
        current_context("owner/project", 100)


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


@pytest.fixture
def github_metadata():
    repo = {"full_name": "owner/project"}
    run = {
        "event": "pull_request",
        "status": "completed",
        "conclusion": "success",
        "path": ".github/workflows/ci.yml",
        "repository": repo,
        "head_repository": repo,
        "head_sha": HEAD,
    }
    pr = {
        "state": "open",
        "draft": False,
        "head": {"repo": repo, "sha": HEAD},
        "base": {"repo": repo, "ref": "main"},
    }
    return copy.deepcopy((run, pr))


def test_current_ready_pr_with_successful_ci_is_eligible(github_metadata):
    assert eligible(*github_metadata, "owner/project", "write")


@pytest.mark.parametrize(
    "target,path,value",
    [
        (0, ("conclusion",), "failure"),
        (0, ("conclusion",), "cancelled"),
        (0, ("conclusion",), "skipped"),
        (0, ("status",), "in_progress"),
        (0, ("event",), "push"),
        (0, ("path",), ".github/workflows/fake.yml"),
        (1, ("draft",), True),
        (1, ("state",), "closed"),
        (1, ("head", "sha"), "c" * 40),
        (1, ("head", "repo", "full_name"), "attacker/fork"),
        (1, ("base", "ref"), "release"),
    ],
)
def test_ineligible_pr_never_reaches_reviewer(github_metadata, target, path, value):
    node = github_metadata[target]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert not eligible(*github_metadata, "owner/project", "write")


def test_read_only_contributor_cannot_spend_review_budget(github_metadata):
    assert not eligible(*github_metadata, "owner/project", "read")

import copy
import json
from pathlib import Path

import pytest

from tools import local_pipeline as pipeline
from tools import review_publication as publication
from tools.decision_gate import decide


@pytest.fixture
def driver(monkeypatch, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/automation.json").write_text(json.dumps({"auto_fix_enabled": True}))
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    state = {"events": [], "used": 0, "statuses": ["CHANGES_REQUESTED", "PASS"]}

    def wait(pr, head):
        state["events"].append(("ci", head))
        state["context"] = {"pr": pr, "head": head, "base": "d" * 40, "ci_run_id": 8}
        return state["context"], {}

    def review(run, directory):
        context = copy.deepcopy(state["context"])
        state["events"].append(("review", context["head"]))
        directory.mkdir()
        (directory / "result.json").write_text(
            json.dumps(
                {
                    "status": state["statuses"].pop(0),
                    "unverified_items": [],
                    "test_summary": {"ci": "PASS"},
                }
            )
        )
        (directory / "evidence.json").write_text(json.dumps({"context": context}))
        return 1  # Structured JSON, not a process return code, determines review status.

    def publish(directory):
        state["events"].append(("publish", state["context"]["head"]))
        return 9

    def fix(directory):
        state["used"] += 1
        state["events"].append(("fix", state["context"]["head"]))
        return {"pr": 4, "previous_head": state["context"]["head"], "head": "b" * 40}

    monkeypatch.setattr(pipeline, "wait_ci", wait)
    monkeypatch.setattr(pipeline, "require_publishing_guards", lambda _: None)
    monkeypatch.setattr(pipeline.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "run_review", review)
    monkeypatch.setattr(pipeline, "publish", publish)
    monkeypatch.setattr(pipeline, "execute", fix)

    def assess(directory, run):
        assert run == 9
        state["events"].append(("gate", state["context"]["head"]))
        report = json.loads((directory / "result.json").read_text())
        status, action, reason = decide(report, state["used"])
        return {
            "status": status,
            "next_action": action,
            "reason": reason,
            "fix_attempts": state["used"],
        }

    monkeypatch.setattr(pipeline, "assess", assess)
    return state


def test_fix_always_receives_new_ci_and_independent_review(driver):
    result = pipeline.drive(4, "a" * 40)
    assert result["status"] == "PASS" and result["merge"] == "DISABLED"
    assert driver["events"] == [
        ("ci", "a" * 40),
        ("review", "a" * 40),
        ("publish", "a" * 40),
        ("gate", "a" * 40),
        ("fix", "a" * 40),
        ("ci", "b" * 40),
        ("review", "b" * 40),
        ("publish", "b" * 40),
        ("gate", "b" * 40),
    ]


@pytest.mark.parametrize("status", ["HUMAN_DECISION_REQUIRED", "PHYSICAL_TEST_REQUIRED", "PASS"])
def test_terminal_report_never_calls_fixer(driver, status):
    driver["statuses"] = [status]
    assert pipeline.drive(4, "a" * 40)["status"] == status
    assert driver["used"] == 0


def test_persistent_attempt_limit_stops_before_third_fix(driver):
    driver["used"] = 2
    result = pipeline.drive(4, "a" * 40)
    assert result["status"] == "HUMAN_DECISION_REQUIRED"
    assert driver["used"] == 2
    assert not any(event[0] == "fix" for event in driver["events"])


def test_failed_ci_is_returned_to_builder_without_ai_or_human_review(driver, monkeypatch):
    monkeypatch.setattr(pipeline, "wait_ci", lambda *a: (None, {"html_url": "ci-url"}))
    assert pipeline.drive(4, "a" * 40)["status"] == "BUILDER_CI_FIX_REQUIRED"
    assert driver["events"] == []


def test_failed_publication_never_starts_fixer(driver, monkeypatch):
    def fail(_):
        raise ValueError("Stale publication")

    monkeypatch.setattr(pipeline, "publish", fail)
    with pytest.raises(ValueError, match="Stale publication"):
        pipeline.drive(4, "a" * 40)
    assert driver["used"] == 0


def test_disabled_fixer_stops_after_review(driver):
    (pipeline.ROOT / "config/automation.json").write_text(json.dumps({"auto_fix_enabled": False}))
    result = pipeline.drive(4, "a" * 40)
    assert result["status"] == "CHANGES_REQUESTED" and driver["used"] == 0


def test_ci_timeout_has_no_review_or_fix(monkeypatch):
    times = iter([0, 0, 901])
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(pipeline.time, "sleep", lambda _: None)
    monkeypatch.setattr(pipeline, "read_pr", lambda *a: None)
    monkeypatch.setattr(pipeline, "latest_ci", lambda _: None)
    with pytest.raises(TimeoutError, match="CI wait"):
        pipeline.wait_ci(4, "a" * 40)


@pytest.mark.parametrize("altered", ["head", "draft", "closed", "fork", "base", "main"])
def test_unsupported_or_changed_pr_is_rejected(monkeypatch, altered):
    pr = {
        "state": "open",
        "draft": False,
        "head": {"sha": "a" * 40, "ref": "codex/test", "repo": {"full_name": pipeline.REPOSITORY}},
        "base": {"ref": "main", "repo": {"full_name": pipeline.REPOSITORY}},
    }
    if altered == "head":
        pr["head"]["sha"] = "b" * 40
    elif altered == "draft":
        pr["draft"] = True
    elif altered == "closed":
        pr["state"] = "closed"
    elif altered == "fork":
        pr["head"]["repo"]["full_name"] = "outside/repository"
    elif altered == "base":
        pr["base"]["ref"] = "other"
    else:
        pr["head"]["ref"] = "main"
    monkeypatch.setattr(pipeline, "api", lambda _: pr)
    with pytest.raises(ValueError, match="PR changed"):
        pipeline.read_pr(4, "a" * 40)


def test_queued_ci_is_followed_by_exact_head_context(monkeypatch):
    head = "a" * 40
    context = {"pr": 4, "head": head}
    runs = iter([None, {"id": 8, "status": "completed", "conclusion": "success"}])
    reads = []
    monkeypatch.setattr(pipeline, "read_pr", lambda pr, sha: reads.append((pr, sha)))
    monkeypatch.setattr(pipeline, "latest_ci", lambda _: next(runs))
    monkeypatch.setattr(pipeline.time, "sleep", lambda _: None)
    monkeypatch.setattr(pipeline, "current_context", lambda *a: context)
    assert pipeline.wait_ci(4, head) == (
        context,
        {"id": 8, "status": "completed", "conclusion": "success"},
    )
    assert reads == [(4, head), (4, head)]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "timed_out"])
def test_nonpassing_ci_never_requests_review_context(monkeypatch, conclusion):
    run = {"status": "completed", "conclusion": conclusion}
    monkeypatch.setattr(pipeline, "read_pr", lambda *a: None)
    monkeypatch.setattr(pipeline, "latest_ci", lambda _: run)
    monkeypatch.setattr(pipeline, "current_context", lambda *a: pytest.fail("Review started"))
    assert pipeline.wait_ci(4, "a" * 40) == (None, run)


def test_rerun_of_lower_id_is_latest(monkeypatch):
    runs = [
        {"id": 2, "run_attempt": 1, "run_started_at": "2026-10-01T01:00:00Z"},
        {"id": 1, "run_attempt": 2, "run_started_at": "2026-10-01T02:00:00Z"},
    ]

    def api(path):
        if "workflows" in path:
            return {"total_count": 2, "workflow_runs": runs}
        return runs[1] if "/runs/1/" in path else runs[0]

    monkeypatch.setattr(pipeline, "api", api)
    assert pipeline.latest_ci("a" * 40)["id"] == 1
    runs[1]["run_started_at"] = runs[0]["run_started_at"]
    with pytest.raises(ValueError, match="Ambiguous"):
        pipeline.latest_ci("a" * 40)


@pytest.mark.parametrize(
    "altered", ["none", "artifact", "status", "context", "actor", "in_progress", "cancelled"]
)
def test_publication_requires_matching_artifact_status_and_current_sha(
    monkeypatch, tmp_path, altered
):
    context = {"head": "a" * 40, "base": "b" * 40, "ci_run_id": 8}
    bundle = {"report": {"status": "PASS"}, "evidence": {"context": context}}
    run = {
        "id": 9,
        "status": "completed",
        "event": "workflow_dispatch",
        "head_sha": context["base"],
        "path": ".github/workflows/codex-review.yml",
        "actor": {"login": "ckrhehfl"},
        "triggering_actor": {"login": "ckrhehfl"},
        "conclusion": "success",
        "html_url": "run-url",
    }
    status = {"context": "codex-review", "target_url": "run-url", "state": "success"}

    def download(args, **kwargs):
        data = bundle if altered != "artifact" else {}
        (Path(args[-1]) / "bundle.json").write_text(json.dumps(data))

    monkeypatch.setattr(publication.subprocess, "run", download)
    monkeypatch.setattr(publication, "api", lambda _: {"statuses": [status]})
    monkeypatch.setattr(
        publication, "current_context", lambda *a: {} if altered == "context" else context
    )
    if altered == "status":
        status["target_url"] = "old-run"
    if altered == "actor":
        run["triggering_actor"]["login"] = "outsider"
    if altered == "in_progress":
        run["status"] = "in_progress"
    if altered == "cancelled":
        run["conclusion"] = "cancelled"
    if altered == "none":
        publication.validate_publication(run, bundle, tmp_path / "download")
    else:
        with pytest.raises(ValueError):
            publication.validate_publication(run, bundle, tmp_path / "download")


def test_failed_gate_never_starts_fixer(driver, monkeypatch):
    def fail(*args):
        raise ValueError("Gate evidence changed")

    monkeypatch.setattr(pipeline, "assess", fail)
    with pytest.raises(ValueError, match="Gate evidence changed"):
        pipeline.drive(4, "a" * 40)
    assert driver["used"] == 0


def test_gate_request_for_reclassification_never_starts_fixer(driver, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "assess",
        lambda *a: {
            "status": "CHANGES_REQUESTED",
            "next_action": "REVIEW",
            "fix_attempts": 0,
        },
    )
    result = pipeline.drive(4, "a" * 40)
    assert result["decision"]["next_action"] == "REVIEW" and driver["used"] == 0

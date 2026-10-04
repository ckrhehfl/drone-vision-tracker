import copy
import json
import subprocess

import pytest

from tools import automation_requests as requests
from tools import automation_worker as worker
from tools import request_journal as journal
from tools.fix_attempts import count_attempts, reserve_attempt


@pytest.fixture
def source(monkeypatch):
    actor = {"login": "ljwoo8942", "id": 233238026}
    run = {
        "id": 30,
        "repository": {"full_name": requests.REPOSITORY},
        "path": requests.WORKFLOW,
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": "a" * 40,
        "run_attempt": 1,
        "actor": actor,
        "triggering_actor": copy.deepcopy(actor),
        "status": "completed",
        "conclusion": "success",
    }
    pr = {
        "number": 12,
        "state": "open",
        "draft": False,
        "head": {"sha": "b" * 40, "ref": "codex/test", "repo": {"full_name": requests.REPOSITORY}},
        "base": {"ref": "main", "repo": {"full_name": requests.REPOSITORY}},
    }
    state = {"run": run, "pr": pr, "permission": "write"}

    def api(path):
        if path.endswith("/permission"):
            return {"permission": state["permission"]}
        return run if "/actions/runs/" in path else pr

    def command(args, **kwargs):
        if args[:3] == ["gh", "run", "download"]:
            destination = kwargs.get("cwd") or args[args.index("--dir") + 1]
            from pathlib import Path

            (Path(destination) / "request.json").write_text(json.dumps(state["artifact"]))

    monkeypatch.setattr(requests, "api", api)
    monkeypatch.setattr(requests.subprocess, "run", command)
    state["artifact"] = requests.build_request(run, pr)
    return state


@pytest.mark.parametrize("login,identifier", list(requests.REQUESTERS.items()))
def test_only_named_current_identities_can_request(source, login, identifier):
    requests.authorize({"login": login, "id": identifier})
    with pytest.raises(ValueError, match="identity list"):
        requests.authorize({"login": login, "id": identifier + 1})
    with pytest.raises(ValueError, match="identity list"):
        requests.authorize({"login": "future-write-user", "id": identifier})


@pytest.mark.parametrize("permission", ["read", "triage", "none"])
def test_revoked_write_access_rejected(source, permission):
    source["permission"] = permission
    with pytest.raises(ValueError, match="write access"):
        requests.validate_run(source["run"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("event", "pull_request"),
        ("head_branch", "codex/feature"),
        ("path", ".github/workflows/other.yml"),
        ("run_attempt", 2),
        ("conclusion", "failure"),
        ("status", "in_progress"),
        ("triggering_actor", requests.OWNER),
        ("repository", {"full_name": "other/repo"}),
    ],
)
def test_untrusted_or_replayed_workflow_rejected(source, field, value):
    source["run"][field] = value
    with pytest.raises(ValueError, match="Untrusted"):
        requests.validate_run(source["run"])


def test_unmerged_workflow_commit_rejected(source, monkeypatch):
    def reject(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(requests.subprocess, "run", reject)
    with pytest.raises(subprocess.CalledProcessError):
        requests.validate_run(source["run"])


def test_authenticated_artifact_matches_current_pr(source, tmp_path):
    assert requests.load_request(30, tmp_path / "download") == source["artifact"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("head", "c" * 40),
        ("requester", requests.OWNER),
        ("request_run", 31),
        ("workflow_commit", "d" * 40),
        ("pr", True),
        ("extra", "injected"),
    ],
)
def test_tampered_or_stale_artifact_cannot_authorize_work(source, tmp_path, field, value):
    source["artifact"][field] = value
    with pytest.raises(ValueError):
        requests.load_request(30, tmp_path / "download")


@pytest.mark.parametrize("change", ["draft", "closed", "fork", "main", "head"])
def test_pr_changes_after_dispatch_rejected(source, tmp_path, change):
    pr = source["pr"]
    if change == "draft":
        pr["draft"] = True
    elif change == "closed":
        pr["state"] = "closed"
    elif change == "fork":
        pr["head"]["repo"]["full_name"] = "other/repo"
    elif change == "main":
        pr["head"]["ref"] = "main"
    else:
        pr["head"]["sha"] = "c" * 40
    with pytest.raises(ValueError):
        requests.load_request(30, tmp_path / "download")


def test_coordinator_requires_owner_and_clean_current_main(monkeypatch):
    state = {"actor": requests.OWNER, "head": "a" * 40, "dirty": ""}
    monkeypatch.setattr(
        requests, "api", lambda p: state["actor"] if p == "user" else {"commit": {"sha": "a" * 40}}
    )
    monkeypatch.setattr(
        requests, "git", lambda *a: state["head"] if a[0] == "rev-parse" else state["dirty"]
    )
    assert requests.require_coordinator() == "a" * 40
    for key, value in [
        ("actor", {"login": "ljwoo8942", "id": 233238026}),
        ("head", "b" * 40),
        ("dirty", " M file.py"),
    ]:
        original = state[key]
        state[key] = value
        with pytest.raises(ValueError):
            requests.require_coordinator()
        state[key] = original


def test_claim_survives_restart_failure_and_does_not_reset_pr_fix_budget(tmp_path):
    path = tmp_path / "requests.sqlite3"
    ledger = tmp_path / "fix-attempts.sqlite3"
    assert not journal.seen(path, 1) and not path.exists()
    for run, requester in enumerate(["ljwoo8942", "chika-breeki"], 1):
        assert journal.claim(path, run)
        reserve_attempt(ledger, requests.REPOSITORY, 12, str(run) * 40)
        journal.finish(path, run, {"status": "ERROR", "requester": requester})
        assert journal.seen(path, run) and not journal.claim(path, run)
    assert count_attempts(ledger, requests.REPOSITORY, 12) == 2
    assert journal.claim(path, 3)  # new request does not create a new fix budget
    assert not journal.claim(path, 3)  # even an interrupted claim remains consumed
    with pytest.raises(ValueError):
        reserve_attempt(ledger, requests.REPOSITORY, 12, "3" * 40)
    with pytest.raises(ValueError, match="unclaimed"):
        journal.finish(path, 99, {"status": "PASS"})


@pytest.fixture
def runner(monkeypatch, tmp_path, source):
    state = {"status": "PASS", "merged": [], "published": [], "enabled": False}
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    (tmp_path / "config").mkdir()
    config = tmp_path / "config/automation.json"
    config.write_text('{"auto_merge_enabled":false}')
    state["config"] = config
    state["journal"] = tmp_path / "requests.sqlite3"
    monkeypatch.setattr(worker, "load_request", lambda *a: source["artifact"])
    monkeypatch.setattr(worker, "authorize", lambda *a: None)

    def drive(*args):
        return {
            "status": state["status"],
            "decision": {},
            "publication_run": 40,
            "review_directory": str(tmp_path),
            "merge": "DISABLED",
        }

    def publish(*args):
        state["published"].append(args)
        return 50, {"decision": {}}

    monkeypatch.setattr(worker, "drive", drive)
    monkeypatch.setattr(worker.gate_publication, "submit", publish)
    monkeypatch.setattr(worker.auto_merge, "merge", lambda *a: state["merged"].append(a))
    return state


@pytest.mark.parametrize(
    "status", ["PASS", "CHANGES_REQUESTED", "HUMAN_DECISION_REQUIRED", "PHYSICAL_TEST_REQUIRED"]
)
def test_worker_publishes_terminal_decision_without_merging_during_preparation(runner, status):
    runner["status"] = status
    result = worker.process(30, runner["journal"])
    assert result["status"] == status and len(runner["published"]) == 1
    assert not runner["merged"]
    assert worker.process(30, runner["journal"])["status"] == "ALREADY_PROCESSED"
    assert len(runner["published"]) == 1


@pytest.mark.parametrize(
    "status", ["PASS", "CHANGES_REQUESTED", "HUMAN_DECISION_REQUIRED", "PHYSICAL_TEST_REQUIRED"]
)
def test_enabled_worker_only_delegates_merge_for_pass(runner, status):
    runner["status"] = status
    runner["config"].write_text('{"auto_merge_enabled":true}')
    worker.process(30, runner["journal"])
    assert len(runner["merged"]) == (1 if status == "PASS" else 0)


def test_publication_failure_is_consumed_without_merge_or_blind_retry(runner, monkeypatch):
    runner["config"].write_text('{"auto_merge_enabled":true}')

    def fail(*args):
        raise ValueError("publication failed")

    monkeypatch.setattr(worker.gate_publication, "submit", fail)
    with pytest.raises(ValueError, match="publication failed"):
        worker.process(30, runner["journal"])
    assert not runner["merged"]
    assert worker.process(30, runner["journal"])["status"] == "ALREADY_PROCESSED"


def test_queue_skips_claimed_runs_and_selects_oldest(monkeypatch, tmp_path):
    path = tmp_path / "requests.sqlite3"
    journal.claim(path, 2)
    monkeypatch.setattr(
        worker, "api", lambda _: {"total_count": 3, "workflow_runs": [{"id": i} for i in [4, 3, 2]]}
    )
    assert worker.pending(path) == 3

import copy
import json
from types import SimpleNamespace

import pytest

from tools import auto_merge as merger


@pytest.fixture
def ready(monkeypatch, tmp_path):
    context = {"base": "a" * 40, "head": "b" * 40, "pr": 12, "ci_run_id": 20}
    decision = {"status": "PASS", "context": context}
    state = {
        "context": context,
        "decision": decision,
        "payload": {"decision": copy.deepcopy(decision)},
        "requester": {"login": "ljwoo8942", "id": 233238026},
        "commands": [],
        "protection": {
            "required_status_checks": {
                "strict": True,
                "checks": [
                    {"context": check, "app_id": 15368}
                    for check in ["software-checks", "codex-review", "decision-gate"]
                ],
            }
        },
        "pr": {
            "state": "open",
            "draft": False,
            "mergeable": True,
            "mergeable_state": "clean",
            "head": {
                "sha": context["head"],
                "ref": "codex/test",
                "repo": {"full_name": merger.REPOSITORY},
            },
            "base": {
                "sha": context["base"],
                "ref": "main",
                "repo": {"full_name": merger.REPOSITORY},
            },
        },
        "publication": {"id": 50},
        "verified": False,
    }
    (tmp_path / "config").mkdir()
    state["config"] = tmp_path / "config/automation.json"
    state["config"].write_text('{"auto_merge_enabled":true}')
    monkeypatch.setattr(merger, "ROOT", tmp_path)
    monkeypatch.setattr(merger, "require_coordinator", lambda: context["base"])
    monkeypatch.setattr(merger, "authorize", lambda _: None)
    monkeypatch.setattr(merger, "assess", lambda *a: state["decision"])
    monkeypatch.setattr(merger, "current_context", lambda *a: state["context"])

    def api(path):
        if path.endswith("/protection"):
            return state["protection"]
        if "/actions/runs/" in path:
            return state["publication"]
        return state["pr"]

    def verify(*args):
        state["verified"] = True

    def command(args, **kwargs):
        state["commands"].append((args, json.loads(kwargs["input"])))
        assert state["verified"]
        state["pr"].update(merged=True, merge_commit_sha="c" * 40)
        return SimpleNamespace(stdout=json.dumps({"merged": True, "sha": "c" * 40}))

    monkeypatch.setattr(merger, "api", api)
    monkeypatch.setattr(merger, "verify_published", verify)
    monkeypatch.setattr(merger.subprocess, "run", command)
    return state


def execute(state):
    return merger.merge("review", 40, 50, state["payload"], state["requester"])


def test_merge_uses_exact_head_squash_once_without_bypass(ready):
    assert execute(ready)["merge_commit"] == "c" * 40
    assert len(ready["commands"]) == 1
    command, body = ready["commands"][0]
    assert command == [
        "gh",
        "api",
        "--method",
        "PUT",
        f"repos/{merger.REPOSITORY}/pulls/12/merge",
        "--input",
        "-",
    ]
    assert body == {"sha": "b" * 40, "merge_method": "squash"}


@pytest.mark.parametrize(
    "condition",
    [
        "disabled",
        "nonpass",
        "newdecision",
        "gateid",
        "missingcheck",
        "wrongapp",
        "nonstrict",
        "base",
        "head",
        "dirty",
        "unknown",
    ],
)
def test_failed_gate_never_reaches_merge_mutation(ready, condition):
    if condition == "disabled":
        ready["config"].write_text('{"auto_merge_enabled":false}')
    elif condition == "nonpass":
        ready["decision"]["status"] = "PHYSICAL_TEST_REQUIRED"
        ready["payload"]["decision"]["status"] = "PHYSICAL_TEST_REQUIRED"
    elif condition == "newdecision":
        ready["decision"]["fix_attempts"] = 1
    elif condition == "gateid":
        ready["publication"]["id"] = 51
    elif condition == "missingcheck":
        ready["protection"]["required_status_checks"]["checks"].pop()
    elif condition == "wrongapp":
        ready["protection"]["required_status_checks"]["checks"][-1]["app_id"] = -1
    elif condition == "nonstrict":
        ready["protection"]["required_status_checks"]["strict"] = False
    elif condition in {"base", "head"}:
        ready["pr"][condition]["sha"] = "d" * 40
    elif condition == "dirty":
        ready["pr"]["mergeable_state"] = "dirty"
    else:
        ready["pr"]["mergeable"] = None
    with pytest.raises(ValueError):
        execute(ready)
    assert not ready["commands"]


def test_full_gate_publication_failure_prevents_merge(ready, monkeypatch):
    def reject(*args):
        raise ValueError("stale published artifact")

    monkeypatch.setattr(merger, "verify_published", reject)
    with pytest.raises(ValueError, match="stale"):
        execute(ready)
    assert not ready["commands"]


def test_ci_changes_at_last_check_prevent_merge(ready, monkeypatch):
    monkeypatch.setattr(merger, "current_context", lambda *a: {**ready["context"], "ci_run_id": 21})
    with pytest.raises(ValueError, match="immediately"):
        execute(ready)
    assert not ready["commands"]


def test_permission_revocation_at_last_check_prevents_merge(ready, monkeypatch):
    calls = []

    def authorize(*args):
        calls.append(args)
        if len(calls) == 2:
            raise ValueError("write access revoked")

    monkeypatch.setattr(merger, "authorize", authorize)
    with pytest.raises(ValueError, match="revoked"):
        execute(ready)
    assert not ready["commands"]


def test_unknown_merge_outcome_is_not_retried(ready, monkeypatch):
    calls = []

    def timeout(*args, **kwargs):
        calls.append(args)
        raise TimeoutError("unknown response")

    monkeypatch.setattr(merger.subprocess, "run", timeout)
    with pytest.raises(TimeoutError):
        execute(ready)
    assert len(calls) == 1

import copy
import json
import sqlite3

import pytest
from jsonschema import ValidationError

from tools import decision_gate as gate
from tools.fix_attempts import count_attempts, reserve_attempt
from tools.publish_review import validate_bundle
from tools.subscription_review import CLI_VERSION


@pytest.fixture
def bundle():
    context = {
        "base": "a" * 40,
        "head": "b" * 40,
        "pr": 10,
        "ci_run_id": 20,
        "ci_run_attempt": 1,
        "ci_started_at": "2026-10-04T10:00:00Z",
        "ci_url": "https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/20",
    }
    return {
        "evidence": {
            "authentication": "chatgpt",
            "cli_version": CLI_VERSION,
            "repository_unchanged": True,
            "context": context,
        },
        "report": {
            "reviewed_base": context["base"],
            "reviewed_commit": context["head"],
            "scope_files": ["tools/example.py"],
            "status": "PASS",
            "blocking_findings": [],
            "non_blocking_findings": [],
            "test_summary": {"ci": "PASS", "commands": ["python -m tools.ci"], "notes": "Fixture"},
            "unverified_items": [
                {
                    "item": "Real camera FPS",
                    "merge_blocker": False,
                    "observation": "Operator measures frame intervals with actual camera",
                    "expected_result": "Record measured FPS; software tests are not physical proof",
                }
            ],
        },
    }


@pytest.fixture
def evidence(monkeypatch, tmp_path, bundle):
    directory = tmp_path / "review"
    directory.mkdir()
    state = {"used": 0, "bundle": bundle, "context": copy.deepcopy(bundle["evidence"]["context"])}

    def save():
        for filename, key in (("result", "report"), ("evidence", "evidence")):
            (directory / f"{filename}.json").write_text(json.dumps(bundle[key]), encoding="utf-8")

    def live(value, run, pr, trusted):
        assert (run, pr, trusted) == (20, 10, True)
        plan = {"scopes": [{"files": ["tools/example.py"]}]}
        return state["context"], validate_bundle(value, state["context"], plan)

    def published(run, value, destination):
        assert run["id"] == 30 and value == bundle
        state["published"] = True

    state.update(directory=directory, save=save)
    save()
    monkeypatch.setattr(gate, "live_bundle", live)
    monkeypatch.setattr(gate, "require_publishing_guards", lambda _: None)
    monkeypatch.setattr(gate, "api", lambda _: {"id": 30})
    monkeypatch.setattr(gate, "validate_publication", published)
    monkeypatch.setattr(gate, "current_context", lambda *a: state["context"])
    monkeypatch.setattr(gate, "ledger_path", lambda: tmp_path / "ledger")
    monkeypatch.setattr(gate, "count_attempts", lambda *a: state["used"])
    return state


def findings(bundle):
    bundle["report"].update(
        status="CHANGES_REQUESTED",
        blocking_findings=[
            {
                "severity": "P1",
                "area": "tests",
                "file": "tools/example.py",
                "line": 1,
                "issue": "Boundary incorrect",
                "reason": "Third attempt allowed",
                "suggested_fix": "Correct boundary",
                "validation": "Check attempts 0, 1, 2",
            }
        ],
    )


@pytest.mark.parametrize("used", [0, 1, 2])
def test_pass_keeps_physical_limitations_and_never_authorizes_merge(evidence, used):
    evidence["used"] = used
    result = gate.assess(evidence["directory"], 30)
    assert evidence["published"]
    assert result["status"] == "PASS" and result["next_action"] == "PROCEED"
    assert result["merge"] == "DISABLED" and result["fix_attempts"] == used
    assert result["unverified_items"] == evidence["bundle"]["report"]["unverified_items"]
    assert result["context"] == evidence["context"]


@pytest.mark.parametrize(
    "used,action,status",
    [
        (0, "FIX", "CHANGES_REQUESTED"),
        (1, "FIX", "CHANGES_REQUESTED"),
        (2, "STOP", "HUMAN_DECISION_REQUIRED"),
    ],
)
def test_unresolved_findings_respect_persistent_attempt_boundary(evidence, used, action, status):
    findings(evidence["bundle"])
    evidence["used"] = used
    evidence["save"]()
    result = gate.assess(evidence["directory"], 30)
    assert (result["status"], result["next_action"]) == (status, action)
    assert result["blocking_findings"] == 1


@pytest.mark.parametrize("status", ["HUMAN_DECISION_REQUIRED", "PHYSICAL_TEST_REQUIRED"])
def test_explicit_gate_preserves_observation_instead_of_starting_fixer(evidence, status):
    report = evidence["bundle"]["report"]
    report["status"] = status
    report["unverified_items"][0]["merge_blocker"] = True
    evidence["save"]()
    result = gate.assess(evidence["directory"], 30)
    assert (result["status"], result["next_action"]) == (status, "STOP")
    assert result["unverified_items"] == report["unverified_items"]


def test_uncategorized_mandatory_evidence_returns_to_reviewer(evidence):
    findings(evidence["bundle"])
    evidence["bundle"]["report"]["unverified_items"][0]["merge_blocker"] = True
    evidence["save"]()
    result = gate.assess(evidence["directory"], 30)
    assert (result["status"], result["next_action"]) == ("CHANGES_REQUESTED", "REVIEW")


@pytest.mark.parametrize(
    "tamper", ["sha", "base", "scope", "blocker", "physical", "ci", "auth", "modified", "attempt"]
)
def test_incomplete_or_contradictory_evidence_cannot_pass(evidence, tamper):
    bundle = evidence["bundle"]
    report = bundle["report"]
    if tamper == "sha":
        report["reviewed_commit"] = "c" * 40
    elif tamper == "base":
        report["reviewed_base"] = "c" * 40
    elif tamper == "scope":
        report["scope_files"] = ["other.py"]
    elif tamper == "blocker":
        findings(bundle)
        report["status"] = "PASS"
    elif tamper == "physical":
        report["unverified_items"][0]["merge_blocker"] = True
    elif tamper == "ci":
        report["test_summary"]["ci"] = "NOT_RUN"
    elif tamper == "auth":
        bundle["evidence"]["authentication"] = "api"
    elif tamper == "modified":
        bundle["evidence"]["repository_unchanged"] = False
    else:
        bundle["evidence"]["context"]["ci_run_attempt"] = 2
    evidence["save"]()
    with pytest.raises((ValueError, ValidationError)):
        gate.assess(evidence["directory"], 30)
    assert "published" not in evidence


@pytest.mark.parametrize("used", [-1, 3, True, "1"])
def test_invalid_attempt_count_cannot_pass(evidence, used):
    evidence["used"] = used
    with pytest.raises(ValueError, match="attempt count"):
        gate.assess(evidence["directory"], 30)


@pytest.mark.parametrize("change", ["head", "base", "ci_run_attempt", "ledger"])
def test_state_change_during_decision_invalidates_result(evidence, monkeypatch, change):
    if change == "ledger":
        counts = iter([0, 1])
        monkeypatch.setattr(gate, "count_attempts", lambda *a: next(counts))
    else:
        updated = copy.deepcopy(evidence["context"])
        updated[change] = 2 if change == "ci_run_attempt" else "c" * 40
        monkeypatch.setattr(gate, "current_context", lambda *a: updated)
    with pytest.raises(ValueError, match="changed during decision"):
        gate.assess(evidence["directory"], 30)


def test_failed_publication_or_trusted_checkout_never_produces_decision(evidence, monkeypatch):
    def reject(*args):
        raise ValueError("Untrusted evidence")

    for name in ("validate_publication", "require_publishing_guards"):
        monkeypatch.setattr(gate, name, reject)
        with pytest.raises(ValueError, match="Untrusted"):
            gate.assess(evidence["directory"], 30)


def test_ledger_read_is_read_only_and_does_not_repair_missing_table(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    assert count_attempts(path, "repo", 1) == 0
    assert not path.exists()
    reserve_attempt(path, "repo", 1, "a" * 40)
    before = path.read_bytes()
    assert count_attempts(path, "repo", 1) == 1
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE attempts")
    with pytest.raises(sqlite3.OperationalError):
        count_attempts(path, "repo", 1)

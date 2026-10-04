import copy
import json
from pathlib import Path

import pytest
import yaml

from tools import gate_publication as publication
from tools.decision_gate import decision_result
from tools.publish_review import validate_bundle
from tools.subscription_review import CLI_VERSION


@pytest.fixture
def payload(monkeypatch):
    context = {
        "base": "a" * 40,
        "head": "b" * 40,
        "pr": 12,
        "ci_run_id": 20,
        "ci_run_attempt": 1,
        "ci_started_at": "2026-10-04T10:00:00Z",
        "ci_url": "https://github.com/ckrhehfl/drone-vision-tracker/actions/runs/20",
    }
    bundle = {
        "evidence": {
            "context": context,
            "authentication": "chatgpt",
            "cli_version": CLI_VERSION,
            "repository_unchanged": True,
        },
        "report": {
            "reviewed_base": context["base"],
            "reviewed_commit": context["head"],
            "scope_files": ["tools/example.py"],
            "status": "PASS",
            "blocking_findings": [],
            "non_blocking_findings": [],
            "unverified_items": [],
            "test_summary": {"ci": "PASS", "commands": ["python -m tools.ci"], "notes": "fixture"},
        },
    }

    def live(value, *args):
        return context, validate_bundle(
            value, context, {"scopes": [{"files": ["tools/example.py"]}]}
        )

    monkeypatch.setattr(publication, "live_bundle", live)
    monkeypatch.setattr(publication, "api", lambda _: {"id": 40})
    monkeypatch.setattr(publication, "current_context", lambda *a: context)
    monkeypatch.setattr(publication, "validate_publication", lambda *a: None)
    return {"review": bundle, "decision": decision_result(bundle, 0, 40)}


def test_server_recomputes_full_decision(payload):
    assert publication.validate_gate(payload) == payload["decision"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("bundle_sha256", "0" * 64),
        ("status", "HUMAN_DECISION_REQUIRED"),
        ("blocking_findings", 1),
        ("fix_attempts", 3),
        ("publication_run", 41),
        ("merge", "ENABLED"),
    ],
)
def test_server_rejects_forged_decision(payload, field, value):
    payload["decision"][field] = value
    with pytest.raises(ValueError):
        publication.validate_gate(payload)


def test_server_rejects_review_mismatch_and_stale_state(payload, monkeypatch):
    altered = copy.deepcopy(payload)
    altered["review"]["report"]["reviewed_commit"] = "c" * 40
    with pytest.raises(ValueError):
        publication.validate_gate(altered)
    monkeypatch.setattr(publication, "current_context", lambda *a: {})
    with pytest.raises(ValueError, match="changed"):
        publication.validate_gate(payload)


def test_server_does_not_replace_failed_review_publication_with_a_gate_pass(payload, monkeypatch):
    def fail(*args):
        raise ValueError("failed review publication")

    monkeypatch.setattr(publication, "validate_publication", fail)
    with pytest.raises(ValueError, match="failed review"):
        publication.validate_gate(payload)


def test_gate_artifact_and_status_are_distinct_from_review(payload, monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(publication, "validate_artifact", lambda *a: captured.append(a))
    publication.verify_published(payload, {"id": 50}, tmp_path)
    assert captured[0][1] == payload and captured[0][3] == "success"
    assert captured[0][-3:] == (publication.WORKFLOW, "validated-decision", "decision-gate")


def test_validate_and_publish_recompute_before_posting(payload, monkeypatch, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"inputs": {"pr_number": "12", "gate_bundle": json.dumps(payload)}})
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    checks = []
    monkeypatch.setattr(publication, "require_trusted_dispatch", lambda: checks.append("owner"))
    posted = []
    monkeypatch.setattr(publication, "post_commit_status", lambda *a: posted.append(a))
    import sys

    for command in ["validate", "publish"]:
        monkeypatch.setattr(
            sys, "argv", ["gate", command, "--directory", str(tmp_path / "validated")]
        )
        assert publication.main() == 0
    assert checks == ["owner", "owner"]
    assert posted == [(payload["decision"]["context"], "PASS", 0, "decision-gate")]


def test_actions_have_only_request_read_and_separate_status_write_permissions():
    root = Path(__file__).resolve().parents[1]
    request = yaml.safe_load((root / ".github/workflows/automation-request.yml").read_text())
    gate = yaml.safe_load((root / publication.WORKFLOW).read_text())
    assert request["permissions"] == {} and gate["permissions"] == {}
    assert set(request["jobs"]["request"]["permissions"].values()) == {"read"}
    assert set(gate["jobs"]["validate"]["permissions"].values()) == {"read"}
    assert gate["jobs"]["publish"]["permissions"] == {
        "contents": "read",
        "actions": "read",
        "pull-requests": "read",
        "statuses": "write",
    }
    assert "github.repository_owner" in gate["jobs"]["validate"]["if"]

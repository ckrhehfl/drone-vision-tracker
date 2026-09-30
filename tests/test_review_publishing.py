import copy
import json
import sys

import pytest

from tools import publish_review as publisher


@pytest.fixture
def bundle():
    context = {"base": "a" * 40, "head": "b" * 40, "pr": 2, "ci_run_id": 10}
    report = {
        "reviewed_base": context["base"],
        "reviewed_commit": context["head"],
        "scope_files": ["tests/sample.py"],
        "status": "PASS",
        "blocking_findings": [],
        "non_blocking_findings": [],
        "unverified_items": [],
        "test_summary": {"ci": "PASS", "commands": [], "notes": "Test fixture"},
    }
    return {
        "report": report,
        "evidence": {
            "authentication": "chatgpt",
            "cli_version": publisher.CLI_VERSION,
            "repository_unchanged": True,
            "context": context,
        },
    }


def test_full_bundle_roundtrip_and_scope_validation(bundle):
    context = bundle["evidence"]["context"]
    plan = {"scopes": [{"files": bundle["report"]["scope_files"]}]}
    assert publisher.validate_bundle(bundle, context, plan)["status"] == "PASS"
    payload, request = publisher.dispatch_payload(bundle)
    decoded = json.loads(payload)
    assert decoded["ref"] == "main"
    assert decoded["inputs"]["request_id"] == request
    assert json.loads(decoded["inputs"]["review_bundle"]) == bundle
    with pytest.raises(ValueError, match="coverage"):
        publisher.validate_bundle(bundle, context, {"scopes": [{"files": ["other.py"]}]})


@pytest.mark.parametrize(
    "field,value",
    [
        ("authentication", "api"),
        ("repository_unchanged", False),
        ("repository_unchanged", 1),
        ("cli_version", "untested"),
    ],
)
def test_unverified_evidence_rejected(bundle, field, value):
    context = copy.deepcopy(bundle["evidence"]["context"])
    bundle["evidence"][field] = value
    with pytest.raises(ValueError, match="evidence"):
        publisher.validate_bundle(bundle, context, {})


def test_stale_ci_attempt_rejected(bundle):
    context = {**bundle["evidence"]["context"], "ci_run_attempt": 2}
    with pytest.raises(ValueError, match="stale"):
        publisher.validate_bundle(bundle, context, {})


def test_oversized_report_is_not_truncated(bundle):
    bundle["report"]["test_summary"]["notes"] = "가" * publisher.MAX_PAYLOAD_BYTES
    with pytest.raises(ValueError, match="never truncate"):
        publisher.dispatch_payload(bundle)
    assert len(bundle["report"]["test_summary"]["notes"]) == publisher.MAX_PAYLOAD_BYTES


@pytest.fixture
def trusted_environment(monkeypatch, tmp_path):
    for key, value in {
        "GITHUB_REPOSITORY": publisher.REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_ACTOR": "ckrhehfl",
        "GITHUB_TRIGGERING_ACTOR": "ckrhehfl",
        "GITHUB_EVENT_PATH": str(tmp_path / "event.json"),
    }.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_REF", "refs/heads/feature"),
        ("GITHUB_ACTOR", "other"),
        ("GITHUB_TRIGGERING_ACTOR", "other"),
        ("GITHUB_REPOSITORY", "other/repo"),
    ],
)
def test_untrusted_dispatch_refused(trusted_environment, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="trusted"):
        publisher.require_trusted_dispatch()


def test_ci_change_between_validation_and_post_never_writes(
    bundle,
    trusted_environment,
    monkeypatch,
    tmp_path,
):
    (tmp_path / "event.json").write_text(json.dumps({"inputs": {"ci_run": "10", "pr_number": "2"}}))
    (tmp_path / "bundle.json").write_text(json.dumps(bundle))
    context = bundle["evidence"]["context"]
    monkeypatch.setattr(publisher, "live_bundle", lambda *a: (context, bundle["report"]))
    monkeypatch.setattr(publisher, "current_context", lambda *a: {**context, "ci_run_id": 11})
    monkeypatch.setattr(publisher, "post_status", lambda *a: pytest.fail("Stale status written"))
    monkeypatch.setattr(sys, "argv", ["publish_review", "publish", "--directory", str(tmp_path)])
    with pytest.raises(ValueError, match="stale"):
        publisher.main()

"""Merge once through protected GitHub PR API after fresh local and published gates."""

import json
import subprocess
import tempfile
from pathlib import Path

from tools.automation_requests import authorize, require_coordinator, validate_pr
from tools.decision_gate import assess
from tools.gate_publication import verify_published
from tools.review import ROOT, require_sha
from tools.review_context import api, current_context
from tools.subscription_review import REPOSITORY
from tools.validate_config import strict_json


def check_ready(directory, review_run, gate_run, payload, requester):
    require_coordinator()
    authorize(requester)
    decision = assess(directory, review_run)
    if decision != payload["decision"] or decision["status"] != "PASS":
        raise ValueError("Fresh decision is not the published PASS decision")
    context = decision["context"]
    run = api(f"repos/{REPOSITORY}/actions/runs/{gate_run}")
    if run["id"] != gate_run:
        raise ValueError("Gate publication ID mismatch")
    with tempfile.TemporaryDirectory(prefix="merge-gate-") as temporary:
        verify_published(payload, run, Path(temporary) / "gate")
    protection = api(f"repos/{REPOSITORY}/branches/main/protection")
    checks = protection.get("required_status_checks") or {}
    required = {(c["context"], c["app_id"]) for c in checks.get("checks", [])}
    if not checks.get("strict") or not {
        ("software-checks", 15368),
        ("codex-review", 15368),
        ("decision-gate", 15368),
    }.issubset(required):
        raise ValueError("Main must require all three checks from the approved Actions app")
    pr = api(f"repos/{REPOSITORY}/pulls/{context['pr']}")
    validate_pr(pr, context["head"])
    if (
        pr["base"]["sha"] != context["base"]
        or pr["mergeable"] is not True
        or pr["mergeable_state"] != "clean"
    ):
        raise ValueError("PR is not cleanly mergeable against the reviewed base")
    return context


def merge(directory, review_run, gate_run, payload, requester):
    settings = strict_json((ROOT / "config/automation.json").read_text(encoding="utf-8"))
    if settings["auto_merge_enabled"] is not True:
        raise ValueError("Automatic merge is disabled in approved main")
    context = check_ready(directory, review_run, gate_run, payload, requester)
    authorize(requester)
    if current_context(REPOSITORY, context["ci_run_id"]) != context:
        raise ValueError("CI or PR changed immediately before merge")
    # No --admin, main push, force, deferred auto-merge, or retry on unknown outcome.
    response = subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "PUT",
            f"repos/{REPOSITORY}/pulls/{context['pr']}/merge",
            "--input",
            "-",
        ],
        input=json.dumps({"sha": context["head"], "merge_method": "squash"}),
        text=True,
        encoding="utf-8",
        check=True,
        capture_output=True,
    )
    result = strict_json(response.stdout)
    if result.get("merged") is not True:
        raise ValueError("GitHub did not confirm merge; inspect PR, never retry blindly")
    merged_sha = require_sha(result["sha"])
    pr = api(f"repos/{REPOSITORY}/pulls/{context['pr']}")
    if (
        pr["merged"] is not True
        or pr["merge_commit_sha"] != merged_sha
        or pr["head"]["sha"] != context["head"]
    ):
        raise ValueError("Merge verification mismatch; inspect PR before any further action")
    return {
        "status": "MERGED",
        "pr": context["pr"],
        "head": context["head"],
        "merge_commit": merged_sha,
    }

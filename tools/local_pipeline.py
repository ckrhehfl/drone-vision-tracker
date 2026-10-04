"""Local subscription review/fix sequence; no merge or paid API fallback."""

import argparse
import json
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from tools.auto_fix import execute, load_bundle, require_publishing_guards
from tools.decision_gate import assess
from tools.fix_attempts import MAX_AUTO_FIX_ATTEMPTS
from tools.publish_review import dispatch_payload, live_bundle
from tools.review import ROOT, require_sha
from tools.review_context import api, current_context, write_json
from tools.review_publication import validate_publication
from tools.subscription_review import REPOSITORY, run_review
from tools.validate_config import strict_json

WAIT_SECONDS = 900
POLL_SECONDS = 15


def read_pr(number, expected_head):
    pr = api(f"repos/{REPOSITORY}/pulls/{number}")
    if (
        pr["state"] != "open"
        or pr["draft"]
        or pr["head"]["sha"] != expected_head
        or pr["head"]["repo"]["full_name"] != REPOSITORY
        or pr["base"]["repo"]["full_name"] != REPOSITORY
        or pr["base"]["ref"] != "main"
        or pr["head"]["ref"] == "main"
    ):
        raise ValueError("PR changed, is draft, is closed, or has an unsupported branch")
    return pr


def latest_ci(head):
    page = api(
        f"repos/{REPOSITORY}/actions/workflows/ci.yml/runs"
        f"?event=pull_request&head_sha={head}&per_page=100"
    )
    if page["total_count"] != len(page["workflow_runs"]):
        raise ValueError("Incomplete CI history")
    attempts = [
        api(f"repos/{REPOSITORY}/actions/runs/{r['id']}/attempts/{r['run_attempt']}")
        for r in page["workflow_runs"]
    ]
    if not attempts:
        return None
    timestamps = [datetime.fromisoformat(run["run_started_at"]) for run in attempts]
    if any(value.tzinfo is None for value in timestamps):
        raise ValueError("CI attempt time must include timezone")
    newest = max(timestamps)
    if timestamps.count(newest) != 1:
        raise ValueError("Ambiguous latest CI attempt")
    return attempts[timestamps.index(newest)]


def wait_ci(pr, head):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        read_pr(pr, head)
        run = latest_ci(head)
        if run and run["status"] == "completed":
            if run["conclusion"] != "success":
                return None, run
            context = current_context(REPOSITORY, run["id"])
            if context["pr"] != pr or context["head"] != head:
                raise ValueError("CI belongs to another PR/head")
            return context, run
        time.sleep(POLL_SECONDS)
    raise TimeoutError("CI wait exceeded 15 minutes; no review/fix/merge was authorized")


def publish(directory):
    bundle = load_bundle(directory)
    context = bundle["evidence"]["context"]
    live_bundle(bundle, context["ci_run_id"], context["pr"])
    payload, request_id = dispatch_payload(bundle)
    subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{REPOSITORY}/actions/workflows/codex-review.yml/dispatches",
            "--input",
            "-",
        ],
        input=payload,
        text=True,
        encoding="utf-8",
        check=True,
    )
    write_json(directory / "publication-request.json", {"request_id": request_id})
    title = f"Review PR {context['pr']} / {request_id}"
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if current_context(REPOSITORY, context["ci_run_id"]) != context:
            raise ValueError("PR/CI changed during publication")
        page = api(f"repos/{REPOSITORY}/actions/workflows/codex-review.yml/runs?per_page=100")
        matches = [run for run in page["workflow_runs"] if run["display_title"] == title]
        if len(matches) > 1:
            raise ValueError("Ambiguous review publication")
        if matches and matches[0]["status"] == "completed":
            run = matches[0]
            validate_publication(run, bundle, directory / "published")
            return run["id"]
        time.sleep(POLL_SECONDS)
    raise TimeoutError("Review publication wait exceeded 15 minutes")


def drive(pr, head):
    """A finite loop; every new head gets a new CI and a fresh independent review."""
    head = require_sha(head)
    settings = strict_json((ROOT / "config/automation.json").read_text(encoding="utf-8"))
    for _ in range(MAX_AUTO_FIX_ATTEMPTS + 1):
        context, run = wait_ci(pr, head)
        if context is None:
            return {
                "status": "BUILDER_CI_FIX_REQUIRED",
                "pr": pr,
                "head": head,
                "ci_url": run["html_url"],
            }
        require_publishing_guards(context)
        subprocess.run(["git", "fetch", "--no-tags", "origin", head], cwd=ROOT, check=True)
        root = ROOT / "artifacts/local-pipeline"
        root.mkdir(parents=True, exist_ok=True)
        operation = Path(tempfile.mkdtemp(prefix=f"pr-{pr}-", dir=root))
        directory = operation / "review"
        run_review(context["ci_run_id"], directory)
        bundle = load_bundle(directory)
        if bundle["evidence"]["context"] != context:
            raise ValueError("Review no longer matches the selected CI")
        publication = publish(directory)
        gate = assess(directory, publication)
        write_json(operation / "decision.json", gate)
        result = {
            "status": gate["status"],
            "pr": pr,
            "head": head,
            "review_directory": str(directory),
            "publication_run": publication,
            "fix_attempts": gate["fix_attempts"],
            "decision": gate,
            "merge": "DISABLED",
        }
        write_json(operation / "result.json", result)
        if gate["next_action"] != "FIX":
            return result
        if not settings["auto_fix_enabled"]:
            result["reason"] = "Automatic fixer is not yet activated"
            write_json(operation / "result.json", result)
            return result
        fixed = execute(directory)
        if fixed["pr"] != pr or fixed["previous_head"] != head:
            raise ValueError("Fixer returned an unrelated commit")
        head = require_sha(fixed["head"])
    raise RuntimeError("Unexpected loop bound; no merge is permitted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    result = drive(args.pr, args.head)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Verify the full published artifact and live status before any next action."""

import subprocess

from tools.review_context import api, current_context
from tools.subscription_review import REPOSITORY
from tools.validate_config import strict_json


def validate_publication(run, bundle, destination):
    context = bundle["evidence"]["context"]
    expected = "success" if bundle["report"]["status"] == "PASS" else "failure"
    validate_artifact(
        run,
        bundle,
        context,
        expected,
        destination,
        ".github/workflows/codex-review.yml",
        "validated-review",
        "codex-review",
    )


def validate_artifact(run, bundle, context, expected, destination, workflow, artifact, check):
    owner = REPOSITORY.split("/")[0]
    if (
        run["status"] != "completed"
        or run["event"] != "workflow_dispatch"
        or run["head_sha"] != context["base"]
        or run["path"] != workflow
        or run["actor"]["login"] != owner
        or run["triggering_actor"]["login"] != owner
        or run["conclusion"] != expected
    ):
        raise ValueError("Publication did not complete from the trusted base/owner")
    destination.mkdir(exist_ok=False)
    subprocess.run(
        [
            "gh",
            "run",
            "download",
            str(run["id"]),
            "--repo",
            REPOSITORY,
            "--name",
            artifact,
            "--dir",
            str(destination),
        ],
        check=True,
        capture_output=True,
    )
    saved = strict_json((destination / "bundle.json").read_text(encoding="utf-8"))
    if saved != bundle:
        raise ValueError("Published structured evidence does not match the local review")
    statuses = api(f"repos/{REPOSITORY}/commits/{context['head']}/status")["statuses"]
    status = next((item for item in statuses if item["context"] == check), None)
    if (
        not status
        or status["target_url"] != run["html_url"]
        or status["state"] != expected
        or current_context(REPOSITORY, context["ci_run_id"]) != context
    ):
        raise ValueError("Missing, stale or failed result publication")

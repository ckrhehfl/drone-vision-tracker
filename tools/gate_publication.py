"""Publish a revalidated decision with status-only Actions permissions."""

import argparse
import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from tools.auto_fix import load_bundle
from tools.decision_gate import assess, decision_result
from tools.publish_review import (
    MAX_PAYLOAD_BYTES,
    live_bundle,
    post_commit_status,
    require_trusted_dispatch,
)
from tools.review_context import api, current_context, write_json
from tools.review_publication import validate_artifact, validate_publication
from tools.subscription_review import REPOSITORY
from tools.validate_config import strict_json

WORKFLOW = ".github/workflows/decision-gate.yml"


def validate_gate(payload):
    if set(payload) != {"review", "decision"}:
        raise ValueError("Unexpected decision payload")
    bundle, decision = payload["review"], payload["decision"]
    saved = bundle["evidence"]["context"]
    context, _ = live_bundle(bundle, saved["ci_run_id"], saved["pr"], True)
    publication = decision["publication_run"]
    if type(publication) is not int or publication < 1:
        raise ValueError("Invalid review publication ID")
    run = api(f"repos/{REPOSITORY}/actions/runs/{publication}")
    if run["id"] != publication:
        raise ValueError("Review publication ID mismatch")
    with tempfile.TemporaryDirectory(prefix="gate-source-") as temporary:
        validate_publication(run, bundle, Path(temporary) / "review")
    # The count is attested only by the owner coordinator, never by a PR author.
    expected = decision_result(bundle, decision["fix_attempts"], publication)
    if decision != expected:
        raise ValueError("Decision contradicts the current structured evidence")
    if current_context(REPOSITORY, context["ci_run_id"]) != context:
        raise ValueError("Decision changed during validation")
    return expected


def verify_published(payload, run, destination):
    context = payload["decision"]["context"]
    expected = "success" if payload["decision"]["status"] == "PASS" else "failure"
    validate_artifact(
        run,
        payload,
        context,
        expected,
        destination,
        WORKFLOW,
        "validated-decision",
        "decision-gate",
    )


def submit(directory, review_run):
    payload = {"review": load_bundle(directory), "decision": assess(directory, review_run)}
    context = payload["decision"]["context"]
    request_id = uuid.uuid4().hex
    body = json.dumps(
        {
            "ref": "main",
            "inputs": {
                "pr_number": str(context["pr"]),
                "request_id": request_id,
                "gate_bundle": json.dumps(payload, ensure_ascii=False),
            },
        },
        ensure_ascii=False,
    )
    if len(body.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("Decision exceeds dispatch limit; never truncate findings")
    subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{REPOSITORY}/actions/workflows/decision-gate.yml/dispatches",
            "--input",
            "-",
        ],
        input=body,
        text=True,
        encoding="utf-8",
        check=True,
    )
    write_json(directory / "gate-request.json", {"request_id": request_id, "payload": payload})
    deadline = time.monotonic() + 900
    title = f"Decision PR {context['pr']} / {request_id}"
    while time.monotonic() < deadline:
        if current_context(REPOSITORY, context["ci_run_id"]) != context:
            raise ValueError("Decision became stale during publication")
        page = api(f"repos/{REPOSITORY}/actions/workflows/decision-gate.yml/runs?per_page=100")
        matches = [r for r in page["workflow_runs"] if r["display_title"] == title]
        if len(matches) > 1:
            raise ValueError("Ambiguous decision publication")
        if matches and matches[0]["status"] == "completed":
            run = matches[0]
            with tempfile.TemporaryDirectory(prefix="gate-published-") as temporary:
                verify_published(payload, run, Path(temporary) / "gate")
            write_json(
                directory / "published-decision.json", {"run": run["id"], "payload": payload}
            )
            return run["id"], payload
        time.sleep(15)
    raise TimeoutError("Decision publication exceeded 15 minutes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "publish", "submit"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--review-run", type=int)
    args = parser.parse_args()
    if args.command == "submit":
        print(submit(args.directory, args.review_run)[0])
        return 0
    require_trusted_dispatch()
    event = strict_json(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    payload = (
        strict_json(event["inputs"]["gate_bundle"])
        if args.command == "validate"
        else strict_json((args.directory / "bundle.json").read_text(encoding="utf-8"))
    )
    decision = validate_gate(payload)
    if decision["context"]["pr"] != int(event["inputs"]["pr_number"]):
        raise ValueError("Decision belongs to another PR")
    if args.command == "validate":
        args.directory.mkdir(parents=True, exist_ok=True)
        write_json(args.directory / "bundle.json", payload)
        write_json(args.directory / "decision.json", decision)
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
            summary.write(
                "### Decision Gate\n\n```json\n"
                + json.dumps(decision, ensure_ascii=False, indent=2)
                + "\n```\n"
            )
        return 0
    if current_context(REPOSITORY, decision["context"]["ci_run_id"]) != decision["context"]:
        raise ValueError("Decision became stale before status publication")
    post_commit_status(
        decision["context"], decision["status"], decision["blocking_findings"], "decision-gate"
    )
    return 0 if decision["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

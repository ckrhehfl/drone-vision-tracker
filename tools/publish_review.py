"""Deliver local review JSON to trusted Actions; only the publisher writes a status."""

import argparse
import json
import os
import subprocess
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from tools.review import ROOT, git, make_plan, validate_report
from tools.review_context import current_context, write_json
from tools.subscription_review import CLI_VERSION, REPOSITORY
from tools.validate_config import strict_json

MAX_PAYLOAD_BYTES = 60000


def validate_bundle(bundle, context, plan):
    if set(bundle) != {"report", "evidence"}:
        raise ValueError("Unexpected bundle fields")
    evidence = bundle["evidence"]
    if (
        set(evidence) != {"authentication", "cli_version", "repository_unchanged", "context"}
        or evidence["authentication"] != "chatgpt"
        or evidence["cli_version"] != CLI_VERSION
        or evidence["repository_unchanged"] is not True
        or evidence["context"] != context
    ):
        raise ValueError("Missing, stale or unsupported local review evidence")
    return validate_report(
        bundle["report"],
        context["base"],
        context["head"],
        [path for scope in plan["scopes"] for path in scope["files"]],
    )


def live_bundle(bundle, run_id, pr_number, trusted_checkout=False):
    context = current_context(REPOSITORY, run_id)
    if context["pr"] != pr_number:
        raise ValueError("Review belongs to a different PR")
    if trusted_checkout and git("rev-parse", "HEAD").strip() != context["base"]:
        raise ValueError("Workflow checkout is not current trusted base")
    subprocess.run(
        ["git", "fetch", "--no-tags", "origin", context["head"]],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    report = validate_bundle(bundle, context, make_plan(context["base"], context["head"]))
    return context, report


def dispatch_payload(bundle):
    context = bundle["evidence"]["context"]
    inputs = {
        "pr_number": str(context["pr"]),
        "ci_run": str(context["ci_run_id"]),
        "request_id": uuid.uuid4().hex,
        "review_bundle": json.dumps(bundle, ensure_ascii=False, separators=(",", ":")),
    }
    payload = json.dumps({"ref": "main", "inputs": inputs}, ensure_ascii=False)
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("Review exceeds dispatch limit; retain full local result, never truncate")
    return payload, inputs["request_id"]


def require_trusted_dispatch():
    owner = REPOSITORY.split("/")[0]
    if (
        os.environ["GITHUB_REPOSITORY"] != REPOSITORY
        or os.environ["GITHUB_REF"] != "refs/heads/main"
        or os.environ["GITHUB_ACTOR"] != owner
        or os.environ["GITHUB_TRIGGERING_ACTOR"] != owner
    ):
        raise ValueError("Only the trusted repository owner may dispatch from main")


def post_status(context, report):
    data = {
        "state": "success" if report["status"] == "PASS" else "failure",
        "context": "codex-review",
        "description": f"{report['status']}; blockers={len(report['blocking_findings'])}",
        "target_url": f"https://github.com/{REPOSITORY}/actions/runs/{int(os.environ['GITHUB_RUN_ID'])}",
    }
    request = Request(
        f"https://api.github.com/repos/{REPOSITORY}/statuses/{context['head']}",
        data=json.dumps(data).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=30) as response:
        json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["submit", "validate", "publish"])
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    directory = args.directory
    if args.command == "submit":
        bundle = {
            "report": strict_json((directory / "result.json").read_text(encoding="utf-8")),
            "evidence": strict_json((directory / "evidence.json").read_text(encoding="utf-8")),
        }
        saved = bundle["evidence"]["context"]
        live_bundle(bundle, saved["ci_run_id"], saved["pr"])
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
        print(f"Submitted review request {request_id}")
        return 0
    require_trusted_dispatch()
    event = strict_json(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    inputs = event["inputs"]
    if args.command == "validate":
        bundle = strict_json(inputs["review_bundle"])
    else:
        bundle = strict_json((directory / "bundle.json").read_text(encoding="utf-8"))
    context, report = live_bundle(bundle, int(inputs["ci_run"]), int(inputs["pr_number"]), True)
    if args.command == "validate":
        directory.mkdir(parents=True, exist_ok=True)
        write_json(directory / "bundle.json", bundle)
        write_json(directory / "result.json", report)
        return 0  # Non-PASS is a valid report, published as failure by the separate job.
    # Recheck after validation, immediately before writing. A different head/base/CI fails closed.
    if current_context(REPOSITORY, context["ci_run_id"]) != context:
        raise ValueError("Review became stale before status publication")
    post_status(context, report)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

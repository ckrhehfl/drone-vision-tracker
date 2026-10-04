"""Accept only approved collaborators' immutable, first-attempt main workflow requests."""

import argparse
import os
import subprocess
from pathlib import Path
from urllib.parse import quote

from tools.review import ROOT, git, require_sha
from tools.review_context import api, write_json
from tools.subscription_review import REPOSITORY
from tools.validate_config import strict_json

# D25: exact current collaborators, not every present/future write user.
OWNER = {"login": "ckrhehfl", "id": 90293220}
REQUESTERS = {
    "ckrhehfl": 90293220,
    "ljwoo8942": 233238026,
    "chika-breeki": 324331900,
    "ddoss2414-max": 324668966,
}
WORKFLOW = ".github/workflows/automation-request.yml"


def authorize(actor):
    login = actor["login"]
    if REQUESTERS.get(login) != actor["id"]:
        raise ValueError("Requester is not on the approved identity list")
    permission = api(f"repos/{REPOSITORY}/collaborators/{quote(login, safe='')}/permission")
    if permission["permission"] not in {"admin", "maintain", "write"}:
        raise ValueError("Requester no longer has write access")


def require_coordinator():
    actor = api("user")
    if {key: actor[key] for key in OWNER} != OWNER:
        raise ValueError("Only the existing owner-authenticated coordinator may execute requests")
    base = api(f"repos/{REPOSITORY}/branches/main")["commit"]["sha"]
    if git("rev-parse", "HEAD").strip() != base or git("status", "--porcelain").strip():
        raise ValueError("Coordinator must use the approved clean current main checkout")
    return base


def validate_pr(pr, head=None):
    if (
        pr["state"] != "open"
        or pr["draft"]
        or pr["head"]["repo"]["full_name"] != REPOSITORY
        or pr["base"]["repo"]["full_name"] != REPOSITORY
        or pr["base"]["ref"] != "main"
        or pr["head"]["ref"] == "main"
        or (head is not None and pr["head"]["sha"] != head)
    ):
        raise ValueError("Request PR is stale, closed, draft, forked or not targeting main")
    return require_sha(pr["head"]["sha"])


def validate_run(run, completed=True):
    if (
        run["repository"]["full_name"] != REPOSITORY
        or run["path"] != WORKFLOW
        or run["event"] != "workflow_dispatch"
        or run["head_branch"] != "main"
        or run["run_attempt"] != 1
        or run["actor"]["id"] != run["triggering_actor"]["id"]
        or (completed and (run["status"] != "completed" or run["conclusion"] != "success"))
    ):
        raise ValueError("Untrusted request workflow or rerun")
    authorize(run["actor"])
    authorize(run["triggering_actor"])
    # Old main is allowed; unmerged feature workflow code is never trusted.
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", require_sha(run["head_sha"]), "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )


def build_request(run, pr):
    return {
        "schema_version": 1,
        "repository": REPOSITORY,
        "request_run": run["id"],
        "request_attempt": 1,
        "workflow_commit": run["head_sha"],
        "requester": {key: run["actor"][key] for key in OWNER},
        "pr": pr["number"],
        "head": validate_pr(pr),
    }


def load_request(run_id, destination):
    run = api(f"repos/{REPOSITORY}/actions/runs/{run_id}")
    if run["id"] != run_id:
        raise ValueError("Request run ID mismatch")
    validate_run(run)
    destination.mkdir(exist_ok=False)
    subprocess.run(
        [
            "gh",
            "run",
            "download",
            str(run_id),
            "--repo",
            REPOSITORY,
            "--name",
            "automation-request",
            "--dir",
            str(destination),
        ],
        check=True,
        capture_output=True,
    )
    request = strict_json((destination / "request.json").read_text(encoding="utf-8"))
    number = request["pr"]
    if type(number) is not int or number < 1:
        raise ValueError("Invalid request PR number")
    pr = api(f"repos/{REPOSITORY}/pulls/{number}")
    if request != build_request(run, pr):
        raise ValueError("Request artifact differs from authenticated metadata/current PR head")
    return request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        os.environ["GITHUB_REPOSITORY"] != REPOSITORY
        or os.environ["GITHUB_REF"] != "refs/heads/main"
    ):
        raise ValueError("Request must execute the default branch workflow")
    run = api(f"repos/{REPOSITORY}/actions/runs/{int(os.environ['GITHUB_RUN_ID'])}")
    validate_run(run, completed=False)
    if git("rev-parse", "HEAD").strip() != run["head_sha"]:
        raise ValueError("Request checkout differs from workflow commit")
    event = strict_json(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    number = int(event["inputs"]["pr_number"])
    if number < 1:
        raise ValueError("PR number must be positive")
    request = build_request(run, api(f"repos/{REPOSITORY}/pulls/{number}"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, request)
    print(f"QUEUED: PR {number}, head {request['head']}; local coordinator must process it")


if __name__ == "__main__":
    main()

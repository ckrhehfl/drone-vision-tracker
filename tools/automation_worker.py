"""Process one approved request on the owner's existing local coordinator."""

import argparse
import json
import tempfile
from pathlib import Path

from tools import auto_merge, gate_publication, request_journal
from tools.automation_requests import authorize, load_request, require_coordinator
from tools.fix_attempts import ledger_path, pr_lock
from tools.local_pipeline import drive
from tools.review import ROOT
from tools.review_context import api, write_json
from tools.subscription_review import REPOSITORY
from tools.validate_config import strict_json


def pending(journal):
    runs = []
    page_number = 1
    while True:
        page = api(
            f"repos/{REPOSITORY}/actions/workflows/automation-request.yml/runs"
            f"?event=workflow_dispatch&status=success&per_page=100&page={page_number}"
        )
        runs.extend(page["workflow_runs"])
        if len(runs) >= page["total_count"]:
            break
        if not page["workflow_runs"]:
            raise ValueError("Incomplete request history")
        page_number += 1
    if len({r["id"] for r in runs}) != len(runs):
        raise ValueError("Request history changed while paging; retry next poll")
    return next(
        (
            r["id"]
            for r in sorted(runs, key=lambda r: r["id"])
            if not request_journal.seen(journal, r["id"])
        ),
        None,
    )


def process(run_id, journal):
    if type(run_id) is not int or run_id < 1:
        raise ValueError("Request run ID must be positive")
    if not request_journal.claim(journal, run_id):
        return {"status": "ALREADY_PROCESSED", "request_run": run_id}
    root = ROOT / "artifacts/automation-requests"
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=f"run-{run_id}-", dir=root))
    try:
        request = load_request(run_id, directory / "request")
        result = drive(request["pr"], request["head"])
        result.update(request_run=run_id, requester=request["requester"])
        if "decision" in result:
            authorize(request["requester"])
            review_directory = Path(result["review_directory"])
            gate_run, payload = gate_publication.submit(review_directory, result["publication_run"])
            result["gate_publication_run"] = gate_run
            settings = strict_json((ROOT / "config/automation.json").read_text(encoding="utf-8"))
            if result["status"] == "PASS" and settings["auto_merge_enabled"]:
                result["merge"] = auto_merge.merge(
                    review_directory,
                    result["publication_run"],
                    gate_run,
                    payload,
                    request["requester"],
                )
        write_json(directory / "result.json", result)
        request_journal.finish(journal, run_id, result)
        return result
    except Exception as error:
        result = {
            "status": "ERROR",
            "request_run": run_id,
            "error": str(error),
            "merge": "NOT_CONFIRMED",
        }
        write_json(directory / "result.json", result)
        request_journal.finish(journal, run_id, result)
        raise


def run_once(run_id=None):
    require_coordinator()
    ledger = ledger_path()
    # A single coordinator serializes all collaborator requests and shares the
    # existing PR-level attempt ledger. No collaborator gets an owner credential.
    with pr_lock(ledger, REPOSITORY, 0):
        journal = ledger.with_name("requests.sqlite3")
        selected = run_id if run_id is not None else pending(journal)
        if selected is None:
            return {"status": "IDLE"}
        return process(selected, journal)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--once", action="store_true")
    selection.add_argument("--request-run", type=int)
    args = parser.parse_args()
    result = run_once(args.request_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"IDLE", "ALREADY_PROCESSED", "PASS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Read-only decision from current CI, published review and persistent fix history."""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator

from tools.auto_fix import load_bundle, require_publishing_guards
from tools.fix_attempts import MAX_AUTO_FIX_ATTEMPTS, count_attempts, ledger_path
from tools.publish_review import live_bundle
from tools.review import ROOT
from tools.review_context import api, current_context
from tools.review_publication import validate_publication
from tools.subscription_review import REPOSITORY
from tools.validate_config import strict_json


def decide(report, used):
    """Classify an already validated report; never infer approval from free text."""
    if type(used) is not int or not 0 <= used <= MAX_AUTO_FIX_ATTEMPTS:
        raise ValueError("Invalid persistent fix attempt count")
    if report["test_summary"]["ci"] != "PASS":
        raise ValueError("Review does not confirm the validated CI")
    status = report["status"]
    if status in {"HUMAN_DECISION_REQUIRED", "PHYSICAL_TEST_REQUIRED"}:
        return status, "STOP", "Review requires human decision or physical observation"
    if status == "CHANGES_REQUESTED":
        if used == MAX_AUTO_FIX_ATTEMPTS:
            return "HUMAN_DECISION_REQUIRED", "STOP", "Maximum two automatic fix attempts exhausted"
        if any(item["merge_blocker"] for item in report["unverified_items"]):
            # The review schema has no category on each unverified item. Do not
            # guess hardware/human approval from words or let a fixer bypass it.
            return status, "REVIEW", "Reviewer must classify unresolved mandatory evidence"
        return status, "FIX", "Blocking software findings remain within the fix limit"
    if status != "PASS":
        raise ValueError("Unsupported review status")
    return "PASS", "PROCEED", "Current software evidence passes; merge remains disabled"


def assess(directory, publication_id):
    """Revalidate live evidence; this function never runs an AI, fixer or merge."""
    if type(publication_id) is not int or publication_id < 1:
        raise ValueError("Expected a positive publication run ID")
    bundle = load_bundle(directory)
    saved = bundle["evidence"]["context"]
    context, report = live_bundle(bundle, saved["ci_run_id"], saved["pr"], True)
    require_publishing_guards(context)
    run = api(f"repos/{REPOSITORY}/actions/runs/{publication_id}")
    if run["id"] != publication_id:
        raise ValueError("Publication run ID mismatch")
    with tempfile.TemporaryDirectory(prefix="drone-gate-") as temporary:
        validate_publication(run, bundle, Path(temporary) / "published")
    used = count_attempts(ledger_path(), REPOSITORY, context["pr"])
    status, action, reason = decide(report, used)
    result = {
        "schema_version": 1,
        "context": context,
        "publication_run": publication_id,
        "bundle_sha256": hashlib.sha256(
            json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "review_status": report["status"],
        "status": status,
        "next_action": action,
        "reason": reason,
        "blocking_findings": len(report["blocking_findings"]),
        "non_blocking_findings": len(report["non_blocking_findings"]),
        "fix_attempts": used,
        "max_auto_fix_attempts": MAX_AUTO_FIX_ATTEMPTS,
        "unverified_items": report["unverified_items"],
        "merge": "DISABLED",
    }
    schema = strict_json((ROOT / "schemas/decision-gate.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(result)
    if (
        current_context(REPOSITORY, context["ci_run_id"]) != context
        or count_attempts(ledger_path(), REPOSITORY, context["pr"]) != used
    ):
        raise ValueError("PR/CI or fix history changed during decision")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--publication-run", type=int, required=True)
    args = parser.parse_args()
    result = assess(args.directory, args.publication_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

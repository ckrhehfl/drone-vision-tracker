"""Deterministic scope planning and fail-closed structured review validation."""

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

from jsonschema import Draft202012Validator

from tools.validate_config import strict_json

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}\Z")


def require_sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError("Expected a complete lowercase commit SHA")
    return value


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8")


def area_for(path):
    parts = set(PurePosixPath(path).parts) | {PurePosixPath(path).stem}
    if parts & {
        "capture",
        "perception",
        "vision",
        "detection",
        "tracking",
        "target_manager",
        "data",
        "models",
    }:
        return "vision"
    if parts & {"controller", "control", "transport", "serial", "firmware", "state_machine"}:
        return "control"
    if parts & {"tests", "config", "tools", "schemas", ".github", ".codex", ".agents"}:
        return "tests-automation"
    return "requirements-integration"


def plan_scopes(changes):
    """changes are (path, added+deleted lines); binary files count as one file."""
    if not changes:
        raise ValueError("Empty diff cannot be reviewed")
    total = sum(lines for _, lines in changes)
    size = (
        "small"
        if len(changes) <= 10 and total <= 400
        else ("large" if len(changes) > 20 or total > 1000 else "medium")
    )
    groups = {}
    for path, _ in changes:
        area = area_for(path)
        key = "full" if size == "small" else area
        if size == "large" and area == "tests-automation":
            # Keep tiny root/config documents together, split substantive automation domains.
            is_review = PurePosixPath(path).stem.startswith(
                ("review", "test_review", "codex-review")
            ) or PurePosixPath(path).parts[0] in {".codex", ".agents", "schemas"}
            key = "review-pipeline" if is_review else "ci-configuration"
        elif size == "large" and area in {"vision", "control"}:
            # Use the first actual package/module under drone_tracker, not the shared src root.
            parts = list(PurePosixPath(path).parts)
            if "drone_tracker" in parts:
                subsystem = parts[parts.index("drone_tracker") + 1]
            else:
                subsystem = parts[0]
            key = f"{area}:{subsystem}"
        groups.setdefault(key, []).append(path)
    scopes = [
        {"id": f"scope-{i}", "area": area, "files": sorted(paths)}
        for i, (area, paths) in enumerate(sorted(groups.items()))
    ]
    return {"size": size, "changed_files": len(changes), "changed_lines": total, "scopes": scopes}


def make_plan(base, head):
    require_sha(base)
    require_sha(head)
    merge_base = git("merge-base", base, head).strip()
    require_sha(merge_base)
    raw = git("diff", "--numstat", "-z", "--no-renames", merge_base, head, "--")
    changes = []
    for entry in raw.split("\0"):
        if entry:
            added, deleted, path = entry.split("\t", 2)
            changes.append((path, 0 if added == "-" else int(added) + int(deleted)))
    return {"base": base, "head": head, "merge_base": merge_base, **plan_scopes(changes)}


def validate_report(report, base, head, scope_files):
    schema = strict_json((ROOT / "schemas/review.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(report)
    if report["reviewed_commit"] != require_sha(head) or report["reviewed_base"] != require_sha(
        base
    ):
        raise ValueError("Review belongs to a different base/head commit")
    if sorted(report["scope_files"]) != sorted(scope_files):
        raise ValueError("Review coverage does not match its assigned files")
    for finding in report["blocking_findings"] + report["non_blocking_findings"]:
        path = finding["file"]
        if (
            path.startswith("/")
            or "\\" in path
            or ".." in PurePosixPath(path).parts
            or ":" in path
            or any(ord(c) < 32 for c in path)
        ):
            raise ValueError("Finding file must be a repository-relative path")
    if any(f["severity"] != "P3" for f in report["non_blocking_findings"]):
        raise ValueError("P0/P1/P2 findings must be blocking")
    if report["status"] == "PASS" and (
        report["blocking_findings"]
        or report["test_summary"]["ci"] != "PASS"
        or any(item["merge_blocker"] for item in report["unverified_items"])
    ):
        raise ValueError(
            "PASS conflicts with blockers, incomplete CI or required physical evidence"
        )
    if report["status"] == "CHANGES_REQUESTED" and not report["blocking_findings"]:
        raise ValueError("CHANGES_REQUESTED requires an actionable blocking finding")
    if (
        report["status"] in {"HUMAN_DECISION_REQUIRED", "PHYSICAL_TEST_REQUIRED"}
        and not report["unverified_items"]
    ):
        raise ValueError("Human or physical gate requires an explanation and observable evidence")
    return report


def merge_reports(reports, plan):
    if len(reports) != len(plan["scopes"]):
        raise ValueError("Missing or extra reviewer result")
    for report, scope in zip(reports, plan["scopes"], strict=True):
        validate_report(report, plan["base"], plan["head"], scope["files"])
    result = {
        "reviewed_commit": plan["head"],
        "reviewed_base": plan["base"],
        "scope_files": sorted({p for s in plan["scopes"] for p in s["files"]}),
        "status": "PASS",
        "blocking_findings": [],
        "non_blocking_findings": [],
        "test_summary": {
            "ci": "PASS",
            "commands": [],
            "notes": "Independent scope reports combined.",
        },
        "unverified_items": [],
    }
    # Only exact duplicates merge. Similar-looking findings may describe distinct bugs.
    for key in ("blocking_findings", "non_blocking_findings", "unverified_items"):
        seen = set()
        for report in reports:
            for item in report[key]:
                identity = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if identity not in seen:
                    result[key].append(item)
                    seen.add(identity)
    blockers = {json.dumps(f, sort_keys=True) for f in result["blocking_findings"]}
    result["non_blocking_findings"] = [
        f for f in result["non_blocking_findings"] if json.dumps(f, sort_keys=True) not in blockers
    ]
    for report in reports:
        result["test_summary"]["commands"].extend(report["test_summary"]["commands"])
        result["test_summary"]["notes"] += "\n" + report["test_summary"]["notes"]
        ci_rank = {"PASS": 0, "NOT_RUN": 1, "FAIL": 2}
        result["test_summary"]["ci"] = max(
            result["test_summary"]["ci"], report["test_summary"]["ci"], key=ci_rank.get
        )
    statuses = {r["status"] for r in reports}
    for status in ("HUMAN_DECISION_REQUIRED", "PHYSICAL_TEST_REQUIRED", "CHANGES_REQUESTED"):
        if status in statuses:
            result["status"] = status
            break
    return validate_report(result, plan["base"], plan["head"], result["scope_files"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["plan", "validate", "merge"])
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--scope")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--reports-dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    plan = make_plan(args.base, args.head)
    if args.command == "plan":
        result = plan
    elif args.command == "validate":
        scope = next(s for s in plan["scopes"] if s["id"] == args.scope)
        result = validate_report(
            strict_json(args.report.read_text(encoding="utf-8")),
            args.base,
            args.head,
            scope["files"],
        )
    else:
        reports = [
            strict_json((args.reports_dir / f"{s['id']}.json").read_text(encoding="utf-8"))
            for s in plan["scopes"]
        ]
        result = merge_reports(reports, plan)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(result.get("status", result.get("size")))
    return 0 if result.get("status", "PASS") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

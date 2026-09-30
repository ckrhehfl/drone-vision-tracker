"""Display complete JSON as escaped text, with no PR comment write permission."""

import html
import os
import sys
from pathlib import Path

from tools.validate_config import strict_json


def main():
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    report = strict_json(text)
    summary = (
        f"Review: {report['status']}\n\n"
        f"Commit: {report['reviewed_commit']}\n\n"
        f"Blocking findings: {len(report['blocking_findings'])}\n\n"
        f"<pre>{html.escape(text)}</pre>\n"
    )
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
        stream.write(summary)


if __name__ == "__main__":
    main()

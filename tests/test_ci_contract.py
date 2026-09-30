import importlib
import re
from pathlib import Path

import pytest
import yaml

from tools.check_design_package import markdown_files
from tools.ci import check_firmware

ROOT = Path(__file__).resolve().parents[1]


def test_document_scan_includes_hidden_skills_but_not_installed_tools(tmp_path):
    included = ["README.md", "docs/design.md", ".codex/skills/review/SKILL.md"]
    excluded = [".venv/dependency/README.md", "artifacts/tool/README.md", ".git/internal.md"]
    for name in included + excluded:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("text", encoding="utf-8")
    assert {p.relative_to(tmp_path).as_posix() for p in markdown_files(tmp_path)} == set(included)


def test_new_firmware_cannot_silently_skip_compile(tmp_path):
    check_firmware(tmp_path)
    (tmp_path / "firmware").mkdir()
    (tmp_path / "firmware/tracker.ino").write_text("void setup() {}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="compile-only"):
        check_firmware(tmp_path)


def test_tools_import_without_network_or_hardware(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Import tried to access the network")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    for name in (
        "ci",
        "check_design_package",
        "validate_config",
        "review",
        "review_context",
        "review_summary",
        "subscription_review",
        "publish_review",
        "auto_fix",
        "fix_attempts",
    ):
        importlib.reload(importlib.import_module(f"tools.{name}"))


def test_ci_has_no_secrets_or_write_token_and_uses_local_command():
    path = ROOT / ".github/workflows/ci.yml"
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "read"}
    assert "secrets." not in path.read_text(encoding="utf-8")
    assert any(s.get("run") == "python -m tools.ci" for s in workflow["jobs"]["ci"]["steps"])


def test_review_publisher_has_only_approved_write_permission():
    workflow = yaml.load(
        (ROOT / ".github/workflows/codex-review.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {}
    assert all(v == "read" for v in workflow["jobs"]["validate"]["permissions"].values())
    writes = {k for k, v in workflow["jobs"]["publish"]["permissions"].items() if v == "write"}
    assert writes == {"statuses"}
    assert workflow["jobs"]["publish"]["needs"] == "validate"
    assert "github.ref == 'refs/heads/main'" in workflow["jobs"]["validate"]["if"]
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            assert "${{ inputs." not in step.get("run", "")
            if "uses" in step:
                assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", step["uses"])
                assert not step["uses"].startswith("openai/")
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["ref"] == "${{ github.sha }}"
                assert step["with"]["persist-credentials"] == "false"
    assert "secrets." not in str(workflow)

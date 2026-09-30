import subprocess

import pytest

from tools import subscription_review as review
from tools.review_context import api, fingerprint


def test_child_environment_excludes_credentials(monkeypatch):
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "GH_TOKEN", "GITHUB_TOKEN", "AWS_SECRET_KEY"):
        monkeypatch.setenv(name, "must-not-reach-reviewer")
    monkeypatch.setenv("CODEX_HOME", "local-auth-store")
    env = review.reviewer_environment()
    assert "must-not-reach-reviewer" not in env.values()
    assert env["CODEX_HOME"] == "local-auth-store"


def test_child_is_chatgpt_only_readonly_and_independent(tmp_path):
    command = review.codex_command("codex", tmp_path, tmp_path / "s.json", tmp_path / "r.json")
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert 'forced_login_method="chatgpt"' in command
    assert 'approval_policy="never"' in command
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "resume" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command


@pytest.mark.parametrize("login,code", [("Logged in using an API key", 0), ("Not logged in", 1)])
def test_preflight_rejects_non_subscription_auth(monkeypatch, login, code):
    monkeypatch.setattr(review.subprocess, "check_output", lambda *a, **k: review.CLI_VERSION)
    monkeypatch.setattr(
        review.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], code, "", login),
    )
    with pytest.raises(ValueError, match="ChatGPT login"):
        review.preflight("codex", {})


def test_untested_cli_version_fails_before_login(monkeypatch):
    monkeypatch.setattr(review.subprocess, "check_output", lambda *a, **k: "codex-cli 0.1")
    with pytest.raises(ValueError, match="Expected tested"):
        review.preflight("codex", {})


def test_public_ci_refuses_account_auth_before_starting_process(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(review.subprocess, "run", lambda *a, **k: pytest.fail("Started process"))
    with pytest.raises(ValueError, match="locally"):
        review.run_review(1)


def test_local_api_uses_existing_gh_login_without_extracting_token(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    commands = []

    def respond(command, **kwargs):
        commands.append(command)
        return '{"state":"open"}'

    monkeypatch.setattr(review.subprocess, "check_output", respond)
    assert api("repos/example/repo/pulls/1") == {"state": "open"}
    assert commands == [
        ["gh", "api", "-H", "X-GitHub-Api-Version: 2022-11-28", "repos/example/repo/pulls/1"]
    ]


def test_immutability_check_detects_tracked_and_untracked_edits(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    path = tmp_path / "sample.txt"
    path.write_text("before", encoding="utf-8")
    git("add", "sample.txt")
    git("commit", "-m", "fixture")
    original = fingerprint(tmp_path)
    path.write_text("after", encoding="utf-8")
    assert fingerprint(tmp_path) != original
    path.write_text("before", encoding="utf-8")
    assert fingerprint(tmp_path) == original
    (tmp_path / "unexpected.txt").write_text("new", encoding="utf-8")
    assert fingerprint(tmp_path) != original

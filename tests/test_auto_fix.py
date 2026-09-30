import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from tools import auto_fix
from tools.fix_attempts import count_attempts, pr_lock, reserve_attempt


def test_attempts_survive_new_head_and_new_process_connection(tmp_path):
    ledger = tmp_path / "state/attempts.sqlite3"
    assert count_attempts(ledger, "repo", 8) == 0
    assert not ledger.exists()
    assert reserve_attempt(ledger, "repo", 8, "a" * 40) == 1
    assert reserve_attempt(ledger, "repo", 8, "b" * 40) == 2
    assert count_attempts(ledger, "repo", 8) == 2
    with pytest.raises(ValueError, match="HUMAN_DECISION_REQUIRED"):
        reserve_attempt(ledger, "repo", 8, "c" * 40)
    assert reserve_attempt(ledger, "repo", 9, "c" * 40) == 1


def test_concurrent_reservations_cannot_exceed_two(tmp_path):
    ledger = tmp_path / "attempts.sqlite3"

    def reserve(index):
        try:
            return reserve_attempt(ledger, "repo", 8, str(index) * 40)
        except ValueError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, range(4)))
    assert sorted(item for item in results if isinstance(item, int)) == [1, 2]
    assert results.count("blocked") == 2


def test_only_one_fixer_can_run_for_a_pr(tmp_path):
    path = tmp_path / "state.sqlite3"
    with pr_lock(path, "repo", 8):
        with pytest.raises(RuntimeError, match="already running"), pr_lock(path, "repo", 8):
            pytest.fail("Second writer acquired the lock")
        with pr_lock(path, "repo", 9):
            pass
    with pr_lock(path, "repo", 8):
        pass


def test_disabled_fixer_starts_no_network_process_or_ledger(monkeypatch, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/automation.json").write_text(json.dumps({"auto_fix_enabled": False}))
    monkeypatch.setattr(auto_fix, "ROOT", tmp_path)
    monkeypatch.setattr(auto_fix, "prepare", lambda *a: pytest.fail("Preparation started"))
    monkeypatch.setattr(auto_fix, "reserve_attempt", lambda *a: pytest.fail("Reserved attempt"))
    monkeypatch.setattr(auto_fix, "run_scope", lambda *a: pytest.fail("AI started"))
    with pytest.raises(ValueError, match="not activated"):
        auto_fix.execute(tmp_path)


@pytest.mark.parametrize("status", ["PASS", "HUMAN_DECISION_REQUIRED", "PHYSICAL_TEST_REQUIRED"])
def test_fixer_refuses_other_review_states(status):
    with pytest.raises(ValueError, match="blocking findings"):
        auto_fix.require_fixable({"status": status, "blocking_findings": []})


def test_fixer_refuses_physical_blockers_and_synthetic_reports():
    report = {
        "status": "CHANGES_REQUESTED",
        "blocking_findings": [{"area": "control"}],
        "unverified_items": [{"merge_blocker": True}],
    }
    with pytest.raises(ValueError, match="physical"):
        auto_fix.require_fixable(report)
    report["unverified_items"] = []
    auto_fix.require_fixable(report)
    report["blocking_findings"][0]["area"] = "synthetic-publisher-test"
    with pytest.raises(ValueError, match="fixture"):
        auto_fix.require_fixable(report)


def test_fixer_uses_separate_subscription_session_with_no_network(tmp_path):
    command = auto_fix.fixer_command("codex", tmp_path, tmp_path / "summary.txt")
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert "--ephemeral" in command
    assert 'forced_login_method="chatgpt"' in command
    assert "sandbox_workspace_write.network_access=false" in command
    assert "--output-schema" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    if os.name == "nt":
        assert 'windows.sandbox="elevated"' in command


@pytest.fixture
def candidate(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, stderr=subprocess.DEVNULL)

    git("init")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_example.py").write_text("def test_value():\n    assert 2 == 2\n")
    git("add", ".")
    git("commit", "-m", "fixture")
    return tmp_path, git("rev-parse", "HEAD").decode().strip(), git


def test_candidate_cannot_remove_or_weaken_existing_assertions(candidate):
    checkout, head, _ = candidate
    path = checkout / "tests/test_example.py"
    path.write_text("def test_value():\n    assert True\n")
    with pytest.raises(ValueError, match="assertions"):
        auto_fix.validate_changes(checkout, head)
    path.unlink()
    with pytest.raises(ValueError, match="delete"):
        auto_fix.validate_changes(checkout, head)


def test_candidate_accepts_new_regression_tests_including_staged_ones(candidate):
    checkout, head, git = candidate
    path = checkout / "tests/test_regression.py"
    path.write_text("def test_boundary():\n    assert 3 == 3\n")
    auto_fix.validate_changes(checkout, head)
    git("add", ".")
    auto_fix.validate_changes(checkout, head)


@pytest.mark.parametrize(
    "source",
    [
        "def test_value():\n    if False:\n        assert 2 == 2\n",
        "def value_not_collected():\n    assert 2 == 2\n",
        "def test_value():\n    return\n    assert 2 == 2\n",
    ],
)
def test_existing_test_execution_cannot_be_removed(candidate, source):
    checkout, head, _ = candidate
    (checkout / "tests/test_example.py").write_text(source)
    with pytest.raises(ValueError, match="preserve existing tests"):
        auto_fix.validate_changes(checkout, head, ["tests/test_example.py"])


@pytest.mark.parametrize(
    "name",
    [
        "AGENTS.md",
        "docs/extra.md",
        ".github/workflows/extra.yml",
        "config/automation.json",
        "unrelated.py",
        "tests/conftest.py",
    ],
)
def test_unrelated_files_cannot_be_published(candidate, name):
    checkout, head, _ = candidate
    path = checkout / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("unrelated = True\n")
    with pytest.raises(ValueError, match="finding scope"):
        auto_fix.validate_changes(checkout, head, ["finding.py"])


def test_candidate_cannot_add_skip_or_change_history(candidate):
    checkout, head, git = candidate
    path = checkout / "tests/test_regression.py"
    path.write_text("import pytest\n@pytest.mark.skip\ndef test_boundary():\n    assert False\n")
    with pytest.raises(ValueError, match="skip"):
        auto_fix.validate_changes(checkout, head)
    git("add", ".")
    git("commit", "-m", "unexpected")
    with pytest.raises(ValueError, match="history"):
        auto_fix.validate_changes(checkout, head)


def test_prepare_refuses_main_branch(monkeypatch, tmp_path):
    context = {"pr": 8, "head": "a" * 40, "ci_run_id": 9}
    report = {
        "status": "CHANGES_REQUESTED",
        "blocking_findings": [{"area": "tests"}],
        "unverified_items": [],
    }
    monkeypatch.setattr(auto_fix, "load_bundle", lambda _: {"evidence": {"context": context}})
    monkeypatch.setattr(auto_fix, "live_bundle", lambda *a: (context, report))
    monkeypatch.setattr(
        auto_fix, "api", lambda _: {"head": {"ref": "main", "sha": context["head"]}}
    )
    with pytest.raises(ValueError, match="feature branch"):
        auto_fix.prepare(tmp_path)


def protection():
    return {
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
        "required_status_checks": {"strict": True, "contexts": ["software-checks", "codex-review"]},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
    }


@pytest.mark.parametrize(
    "missing",
    [
        "enforce_admins",
        "required_pull_request_reviews",
        "required_status_checks",
        "allow_force_pushes",
        "allow_deletions",
    ],
)
def test_publisher_requires_server_side_main_guards(monkeypatch, missing):
    settings = protection()
    monkeypatch.setattr(auto_fix, "git", lambda *a: "a" * 40 if a[0] == "rev-parse" else "")
    monkeypatch.setattr(auto_fix, "api", lambda _: settings)
    auto_fix.require_publishing_guards({"base": "a" * 40})
    del settings[missing]
    with pytest.raises(ValueError, match="Main must enforce"):
        auto_fix.require_publishing_guards({"base": "a" * 40})


def test_execute_failure_never_pushes_and_consumes_attempt(monkeypatch, tmp_path):
    operator = tmp_path / "operator"
    (operator / "config").mkdir(parents=True)
    (operator / "config/automation.json").write_text(json.dumps({"auto_fix_enabled": True}))
    skill = operator / ".codex/skills/fix-findings/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Mock policy")
    ledger = tmp_path / "attempts.sqlite3"
    context = {"pr": 8, "head": "a" * 40, "base": "b" * 40, "ci_run_id": 9}
    monkeypatch.setattr(auto_fix, "ROOT", operator)
    monkeypatch.setattr(
        auto_fix, "prepare", lambda _: (context, {"blocking_findings": []}, "feature")
    )
    monkeypatch.setattr(auto_fix, "require_publishing_guards", lambda _: None)
    monkeypatch.setattr(auto_fix.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(auto_fix, "preflight", lambda *a: None)
    monkeypatch.setattr(auto_fix, "ledger_path", lambda: ledger)
    monkeypatch.setattr(auto_fix, "git_state", lambda _: (b"unchanged",))
    monkeypatch.setattr(auto_fix, "current_context", lambda *a: context)
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        assert "push" not in args

    def failed_fixer(*args):
        raise RuntimeError("Simulated model failure")

    monkeypatch.setattr(auto_fix.subprocess, "run", command)
    monkeypatch.setattr(auto_fix, "run_scope", failed_fixer)
    with pytest.raises(RuntimeError, match="model failure"):
        auto_fix.execute(tmp_path)
    assert count_attempts(ledger, auto_fix.REPOSITORY, 8) == 1
    assert not any("commit" in call or "push" in call for call in calls)


@pytest.mark.parametrize("failure", ["none", "ci", "pre-commit", "pre-push"])
def test_candidate_flow_requires_local_ci_before_feature_push(monkeypatch, tmp_path, failure):
    seed = tmp_path / "seed"
    seed.mkdir()
    real_run = subprocess.run

    def seed_git(*args):
        return real_run(["git", *args], cwd=seed, check=True, capture_output=True)

    seed_git("init")
    seed_git("config", "user.name", "Test")
    seed_git("config", "user.email", "test@example.invalid")
    (seed / "value.py").write_text("VALUE = 1\n")
    seed_git("add", ".")
    seed_git("commit", "-m", "seed")
    head = seed_git("rev-parse", "HEAD").stdout.decode().strip()
    context = {"pr": 8, "head": head, "base": "b" * 40, "ci_run_id": 9}
    operator = tmp_path / "operator"
    (operator / "config").mkdir(parents=True)
    (operator / "config/automation.json").write_text(json.dumps({"auto_fix_enabled": True}))
    skill = operator / ".codex/skills/fix-findings/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Test policy")
    ledger = tmp_path / "attempts.sqlite3"
    monkeypatch.setattr(auto_fix, "ROOT", operator)
    monkeypatch.setattr(
        auto_fix,
        "prepare",
        lambda _: (context, {"blocking_findings": [{"file": "value.py"}]}, "feature"),
    )
    monkeypatch.setattr(auto_fix, "require_publishing_guards", lambda _: None)
    monkeypatch.setattr(auto_fix, "current_context", lambda *a: context)
    monkeypatch.setattr(auto_fix.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(auto_fix, "preflight", lambda *a: None)
    monkeypatch.setattr(auto_fix, "ledger_path", lambda: ledger)
    pushed = []
    ci_checked = []
    parent_commits = []

    def run(args, **kwargs):
        if args[:2] == ["git", "clone"]:
            result = real_run(["git", "clone", "--no-checkout", str(seed), args[-1]], **kwargs)
            for key, value in [("user.name", "Test"), ("user.email", "test@example.invalid")]:
                real_run(["git", "config", key, value], cwd=args[-1], check=True)
            return result
        if "tools.ci" in args:
            ci_checked.append(True)
            assert "sandbox" in args
            assert any(item.endswith("network.enabled=false") for item in args)
            if failure == "ci":
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0)
        if args[0] == "git" and "commit" in args:
            parent_commits.append(args)
            assert any(item.startswith("core.hooksPath=") for item in args)
        if args[0] == "git" and "push" in args:
            assert ci_checked
            pushed.append(args)
            return subprocess.CompletedProcess(args, 0)
        return real_run(args, **kwargs)

    def fix(command, *args, **kwargs):
        from pathlib import Path

        checkout = Path(command[command.index("--cd") + 1])
        if "sandbox" in command:
            return run(command)
        (checkout / "value.py").write_text("VALUE = 2\n")
        if failure in {"pre-commit", "pre-push"}:
            marker = (tmp_path / "hook-executed").as_posix()
            hook = checkout / ".git/hooks" / failure
            hook.write_text(f'#!/bin/sh\necho ran > "{marker}"\n')
            hook.chmod(0o755)

    monkeypatch.setattr(auto_fix.subprocess, "run", run)
    monkeypatch.setattr(auto_fix, "run_scope", fix)
    if failure == "ci":
        with pytest.raises(subprocess.CalledProcessError):
            auto_fix.execute(tmp_path)
        assert not pushed
    elif failure in {"pre-commit", "pre-push"}:
        with pytest.raises(ValueError, match="Git configuration"):
            auto_fix.execute(tmp_path)
        assert not pushed and not parent_commits and not ci_checked
        assert not (tmp_path / "hook-executed").exists()
    else:
        result = auto_fix.execute(tmp_path)
        assert result["status"] == "CI_AND_INDEPENDENT_REVIEW_REQUIRED"
        assert result["head"] != head and result["previous_head"] == head
        assert len(pushed) == 1 and pushed[0][-1] == "HEAD:refs/heads/feature"
        assert "--force" not in pushed[0]
    assert count_attempts(ledger, auto_fix.REPOSITORY, 8) == 1

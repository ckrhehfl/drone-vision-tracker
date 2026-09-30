import subprocess

import pytest

from tools.auto_fix import write_push_guard


def test_exact_old_sha_guard_rejects_rewind_deletion_and_other_branch(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    remote = tmp_path / "remote.git"
    hooks = tmp_path / "trusted hooks"
    hooks.mkdir()

    def git(*args, cwd=checkout, check=True):
        return subprocess.run(["git", *map(str, args)], cwd=cwd, check=check, capture_output=True)

    git("init")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    commits = []
    for value in ("base", "reviewed", "fixed"):
        (checkout / "value.txt").write_text(value)
        git("add", ".")
        git("commit", "-m", value)
        commits.append(git("rev-parse", "HEAD").stdout.decode().strip())
    ancestor, reviewed, fixed = commits
    git("init", "--bare", remote)
    git("push", remote, f"{ancestor}:refs/heads/feature")
    write_push_guard(hooks, "feature", reviewed, fixed)

    def push(branch="feature"):
        return git(
            "-c",
            f"core.hooksPath={hooks}",
            "push",
            remote,
            f"HEAD:refs/heads/{branch}",
            check=False,
        )

    assert push().returncode != 0  # Normal fast-forward to fixed would otherwise succeed.
    assert git("rev-parse", "refs/heads/feature", cwd=remote).stdout.decode().strip() == ancestor
    git("update-ref", "-d", "refs/heads/feature", cwd=remote)
    assert push().returncode != 0  # Deleted branch must not be recreated.
    assert push("other").returncode != 0
    git("push", remote, f"{reviewed}:refs/heads/feature")
    assert push().returncode == 0
    assert git("rev-parse", "refs/heads/feature", cwd=remote).stdout.decode().strip() == fixed


def test_push_guard_refuses_main(tmp_path):
    with pytest.raises(ValueError, match="main"):
        write_push_guard(tmp_path, "main", "a" * 40, "b" * 40)

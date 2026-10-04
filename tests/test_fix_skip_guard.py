import subprocess

import pytest

from tools.auto_fix import validate_changes


@pytest.fixture
def checkout(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, stderr=subprocess.DEVNULL)

    git("init")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (tmp_path / "tests").mkdir()
    # Existing, approved optional tests must remain unchanged and usable.
    (tmp_path / "tests/test_existing.py").write_text(
        'import pytest\n@pytest.mark.skipif(True, reason="optional hardware")\n'
        "def test_existing():\n    assert False\n",
        encoding="utf-8",
    )
    git("add", ".")
    git("commit", "-m", "fixture")
    return tmp_path, git("rev-parse", "HEAD").decode().strip(), git


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize(
    "source",
    [
        'import pytest\npytest.skip("hidden", allow_module_level=True)\n',
        'import pytest\npytest.xfail("hidden")\n',
        'import pytest\npytest.importorskip("absent_dependency")\n',
        'import pytest as pt\npt.importorskip("absent_dependency")\n',
        'from pytest import skip\nskip("hidden", allow_module_level=True)\n',
        'from pytest import skip as ignore\nignore("hidden", allow_module_level=True)\n',
        'from pytest import xfail\nxfail("hidden")\n',
        'from pytest import xfail as ignore\nignore("hidden")\n',
        'from pytest import importorskip\nimportorskip("absent_dependency")\n',
        'from pytest import importorskip as require\nrequire("absent_dependency")\n',
        'from pytest import *\nskip("hidden", allow_module_level=True)\n',
        'from _pytest.outcomes import skip as ignore\nignore("hidden")\n',
        'from _pytest.outcomes import *\nskip("hidden")\n',
        'import pytest\n@pytest.mark.skip(reason="hidden")\n'
        "def test_disabled():\n    assert False\n",
        'from pytest import mark as markers\n@markers.skipif(True, reason="hidden")\n'
        "def test_disabled():\n    assert False\n",
        "import pytest as pt\n@pt.mark.xfail\ndef test_disabled():\n    assert False\n",
    ],
)
def test_skipping_new_regression_is_rejected_before_publication(checkout, staged, source):
    path, head, git = checkout
    (path / "tests/test_regression.py").write_text(
        source + "\ndef test_required_failure():\n    assert False\n", encoding="utf-8"
    )
    if staged:
        git("add", ".")
    with pytest.raises(ValueError, match="cannot add skip or xfail"):
        validate_changes(path, head)
    assert git("rev-parse", "HEAD").decode().strip() == head


def test_existing_optional_test_and_normal_new_regression_remain_allowed(checkout):
    path, head, git = checkout
    existing = (path / "tests/test_existing.py").read_bytes()
    (path / "tests/test_regression.py").write_text(
        "from pytest import raises as raises_error\n\n"
        "def test_invalid_number():\n"
        "    with raises_error(ValueError):\n        int('invalid')\n",
        encoding="utf-8",
    )
    validate_changes(path, head)
    git("add", ".")
    validate_changes(path, head)
    assert (path / "tests/test_existing.py").read_bytes() == existing

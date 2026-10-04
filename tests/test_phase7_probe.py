import pytest

from tools.phase7_probe import may_start_fix


@pytest.mark.parametrize("used,expected", [(0, True), (1, True), (3, False)])
def test_probe_initial_and_over_limit_counts(used, expected):
    assert may_start_fix(used) is expected


@pytest.mark.parametrize("used", [-1, True, 1.5, "1", None])
def test_probe_rejects_invalid_counts(used):
    with pytest.raises(ValueError, match="nonnegative integer"):
        may_start_fix(used)

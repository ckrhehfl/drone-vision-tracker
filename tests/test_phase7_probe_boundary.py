from tools.phase7_probe import may_start_fix


def test_probe_rejects_exact_attempt_limit():
    assert may_start_fix(2) is False

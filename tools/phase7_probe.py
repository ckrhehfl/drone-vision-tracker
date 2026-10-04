"""Isolated Phase 7 verification probe; never reserves attempts or controls hardware."""

from tools.fix_attempts import MAX_AUTO_FIX_ATTEMPTS


def may_start_fix(attempts_used: int) -> bool:
    """Allow a new attempt only while fewer than two have already been used."""
    if type(attempts_used) is not int or attempts_used < 0:
        raise ValueError("attempts_used must be a nonnegative integer")
    return attempts_used <= MAX_AUTO_FIX_ATTEMPTS

"""Persistent, atomic PR-level attempt reservations. Never reset on a new commit."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

MAX_AUTO_FIX_ATTEMPTS = 2


def ledger_path():
    parent = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
    return parent / "drone-vision-tracker/automation/fix-attempts.sqlite3"


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS attempts ("
        "repository TEXT NOT NULL, pr INTEGER NOT NULL, attempt INTEGER NOT NULL, "
        "head TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY(repository, pr, attempt))"
    )
    return connection


def count_attempts(path, repository, pr):
    if not path.exists():
        return 0
    with closing(connect(path)) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM attempts WHERE repository=? AND pr=?", (repository, pr)
        ).fetchone()[0]


def reserve_attempt(path, repository, pr, head):
    connection = connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        used = connection.execute(
            "SELECT COUNT(*) FROM attempts WHERE repository=? AND pr=?", (repository, pr)
        ).fetchone()[0]
        if used >= MAX_AUTO_FIX_ATTEMPTS:
            raise ValueError("HUMAN_DECISION_REQUIRED: two automatic fix attempts are exhausted")
        attempt = used + 1
        connection.execute(
            "INSERT INTO attempts(repository, pr, attempt, head) VALUES (?, ?, ?, ?)",
            (repository, pr, attempt, head),
        )
        # Consume BEFORE starting the AI. Never refund failures or cancellations.
        connection.commit()
        return attempt
    finally:
        connection.close()

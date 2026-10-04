"""Durable once-only dispatch claims, separate from the PR-wide fix ledger."""

import json
import sqlite3
from contextlib import closing


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute(
        "CREATE TABLE IF NOT EXISTS requests (run_id INTEGER PRIMARY KEY, result TEXT NOT NULL)"
    )
    return db


def seen(path, run_id):
    if not path.exists():
        return False
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        return db.execute("SELECT 1 FROM requests WHERE run_id=?", (run_id,)).fetchone() is not None


def claim(path, run_id):
    with closing(connect(path)) as db, db:
        try:
            db.execute("INSERT INTO requests VALUES (?, ?)", (run_id, '{"status":"CLAIMED"}'))
        except sqlite3.IntegrityError:
            return False
    return True


def finish(path, run_id, result):
    with closing(connect(path)) as db, db:
        changed = db.execute(
            "UPDATE requests SET result=? WHERE run_id=?", (json.dumps(result), run_id)
        )
        if changed.rowcount != 1:
            raise ValueError("Cannot complete an unclaimed request")

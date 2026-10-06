"""
fact_store.py — proof-of-core for an AI-memory "freshness engine".

Core idea: currency (what is TRUE right now) is decided by EVENT TIME
(when the fact became true in the real world), never by RECORDED TIME
(when some note happened to mention it). There is exactly one "current"
row per (domain, fact_key) at all times, enforced by the database itself
via a partial unique index — not by application logic that could be
forgotten or buggy.

Stdlib only: sqlite3. No external dependencies.
"""

import sqlite3
from datetime import datetime, timezone


SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    domain      TEXT NOT NULL,
    fact_key    TEXT NOT NULL,
    value       TEXT NOT NULL,
    event_time  TEXT NOT NULL,   -- ISO 8601: when the fact became TRUE
    recorded_at TEXT NOT NULL,   -- ISO 8601: when this row was inserted
    valid_to    TEXT,            -- NULL = still current; else superseded-at event_time
    source      TEXT
);

-- The single most important line in this file: at most ONE current
-- (valid_to IS NULL) row per (domain, fact_key). SQLite partial unique
-- indexes only index rows matching the WHERE clause, so historical
-- (superseded) rows are completely exempt from this constraint.
CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_one_current
    ON facts(domain, fact_key)
    WHERE valid_to IS NULL;

-- Speeds up current()/history() lookups; the UNIQUE index above already
-- covers the "current row" case, this covers full-history scans.
CREATE INDEX IF NOT EXISTS idx_facts_domain_key
    ON facts(domain, fact_key);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FactStore:
    """A tiny event-time-aware fact store backed by SQLite."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        # check_same_thread=False keeps this usable from simple scripts/tests;
        # this proof-of-core is single-writer so no extra locking is added.
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._init_schema()

    def _init_schema(self):
        with self._conn:
            self._conn.executescript(SCHEMA)

    def close(self):
        self._conn.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def assert_fact(self, domain: str, key: str, value: str, event_time: str,
                     source: str = None):
        """
        Record a fact, resolving currency by EVENT TIME, not recording order.

        Rules:
        - No current row yet            -> insert as current.
        - new event_time >= current     -> supersede current row (set its
                                            valid_to = new event_time), insert
                                            new value as the new current row.
        - new event_time <  current     -> STALE re-mention. Insert as a
                                            historical (non-current) row with
                                            valid_to = current row's event_time.
                                            The current row is left untouched.
        - identical value, same/older
          event vs current              -> no-op (current row's recorded_at
                                            is bumped to note we saw it again).

        Never deletes anything.
        """
        recorded_at = _now_iso()

        with self._conn:
            cur = self._conn.execute(
                """SELECT id, value, event_time FROM facts
                   WHERE domain = ? AND fact_key = ? AND valid_to IS NULL""",
                (domain, key),
            )
            current_row = cur.fetchone()

            if current_row is None:
                # First time we've ever heard of this fact.
                self._conn.execute(
                    """INSERT INTO facts
                       (domain, fact_key, value, event_time, recorded_at, valid_to, source)
                       VALUES (?, ?, ?, ?, ?, NULL, ?)""",
                    (domain, key, value, event_time, recorded_at, source),
                )
                return

            current_id = current_row["id"]
            current_value = current_row["value"]
            current_event_time = current_row["event_time"]

            # Identical value + not newer -> no-op, just bump recorded_at
            # as a "re-confirmed at this time" breadcrumb.
            if value == current_value and event_time <= current_event_time:
                self._conn.execute(
                    "UPDATE facts SET recorded_at = ? WHERE id = ?",
                    (recorded_at, current_id),
                )
                return

            if event_time >= current_event_time:
                # Newer (or equal-time correction) event wins: supersede.
                self._conn.execute(
                    "UPDATE facts SET valid_to = ? WHERE id = ?",
                    (event_time, current_id),
                )
                self._conn.execute(
                    """INSERT INTO facts
                       (domain, fact_key, value, event_time, recorded_at, valid_to, source)
                       VALUES (?, ?, ?, ?, ?, NULL, ?)""",
                    (domain, key, value, event_time, recorded_at, source),
                )
            else:
                # THE ANTI-$12.69 CASE: this is an older-event-time fact being
                # mentioned late (e.g. a stale note surfacing after the fact
                # was already superseded). Record it for provenance/history,
                # but it is NOT current — the real current row is untouched.
                self._conn.execute(
                    """INSERT INTO facts
                       (domain, fact_key, value, event_time, recorded_at, valid_to, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (domain, key, value, event_time, recorded_at,
                     current_event_time, source),
                )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def current(self, domain: str, key: str):
        """Return the single current value for (domain,key), or None."""
        cur = self._conn.execute(
            """SELECT value FROM facts
               WHERE domain = ? AND fact_key = ? AND valid_to IS NULL""",
            (domain, key),
        )
        row = cur.fetchone()
        return row["value"] if row else None

    def current_row(self, domain: str, key: str):
        """Return the full current row (dict) for (domain,key), or None."""
        cur = self._conn.execute(
            """SELECT * FROM facts
               WHERE domain = ? AND fact_key = ? AND valid_to IS NULL""",
            (domain, key),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def history(self, domain: str, key: str):
        """All versions of a fact, ordered by event_time, oldest first."""
        cur = self._conn.execute(
            """SELECT id, value, event_time, recorded_at, valid_to, source
               FROM facts
               WHERE domain = ? AND fact_key = ?
               ORDER BY event_time ASC, id ASC""",
            (domain, key),
        )
        return [dict(r) for r in cur.fetchall()]

    def audit(self):
        """
        Safety-net health check. Even though the partial unique index
        makes it structurally impossible to have >1 current row per
        (domain, fact_key), this function checks anyway — belt & suspenders
        for anyone who edits the schema later.
        """
        cur = self._conn.execute(
            """SELECT domain, fact_key, COUNT(*) AS current_count
               FROM facts
               WHERE valid_to IS NULL
               GROUP BY domain, fact_key
               HAVING COUNT(*) > 1"""
        )
        violations = [dict(r) for r in cur.fetchall()]

        totals = self._conn.execute("SELECT COUNT(*) AS n FROM facts").fetchone()["n"]
        current_totals = self._conn.execute(
            "SELECT COUNT(*) AS n FROM facts WHERE valid_to IS NULL"
        ).fetchone()["n"]
        distinct_keys = self._conn.execute(
            "SELECT COUNT(DISTINCT domain || '::' || fact_key) AS n FROM facts"
        ).fetchone()["n"]

        return {
            "ok": len(violations) == 0,
            "violations": violations,
            "total_facts": totals,
            "current_facts": current_totals,
            "distinct_keys": distinct_keys,
        }

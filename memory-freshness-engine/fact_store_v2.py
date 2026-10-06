"""
fact_store_v2.py — freshness engine, hardened.

v1 (fact_store.py) proved the core: currency is decided by EVENT TIME, never
recording order; exactly one current row per (domain, fact_key) enforced by a
partial unique index. v1 remains untouched as logged evidence.

v2 fixes the three weaknesses that multi-model review + the qwen extraction
eval exposed:

  F7 (date-format bug, Grok):
      "2026-07-01" >= "2026-07-01T00:00:00" is FALSE under string compare, so
      mixed date formats silently broke supersession. v2 PARSES every
      event_time to a real datetime and stores a CANONICAL ISO string, so
      comparison is temporal, not lexical.

  F2 (equal-event-time lottery, Fable):
      v1 treated event_time >= current as "supersede", so two facts with the
      SAME event_time but DIFFERENT values resolved by ingestion order (a
      silent lottery — the shipped v1 test even encoded this as "passing").
      v2 splits it: strictly-newer supersedes; equal-time-different-value goes
      to a CONFLICT QUEUE (no silent override); equal-time-same-value is a
      no-op; an explicit correction_of overrides on purpose.

  Contestable event_time (from the extraction eval):
      the qwen eval mis-dated a June fact as February (case 5). A doubtful
      date must NEVER auto-supersede a good current fact. Callers pass
      contestable=True and the fact is queued for human review instead of
      overriding current.

Stdlib only: sqlite3. No external dependencies.
"""

import contextlib
import json
import sqlite3
from datetime import datetime, timezone


SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    domain      TEXT NOT NULL,
    fact_key    TEXT NOT NULL,
    value       TEXT NOT NULL,
    event_time  TEXT NOT NULL,   -- CANONICAL ISO 8601: when the fact became TRUE
    recorded_at TEXT NOT NULL,   -- ISO 8601: when this row was inserted
    valid_to    TEXT,            -- NULL = still current; else superseded-at event_time
    source      TEXT,
    quote       TEXT             -- Phase ① (G1-G4): verbatim source excerpt backing
                                  -- this value. Nullable/default NULL so pre-Phase①
                                  -- rows and legacy callers stay schema-compatible.
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_one_current
    ON facts(domain, fact_key)
    WHERE valid_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_facts_domain_key
    ON facts(domain, fact_key);

-- Facts that could NOT be auto-resolved and need a human glance:
--   * equal event_time, different value  (F2)
--   * a caller-flagged contestable date   (extraction uncertainty)
-- Nothing here has touched the current fact; it is a parking lot, not a truth.
CREATE TABLE IF NOT EXISTS conflicts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    domain              TEXT NOT NULL,
    fact_key            TEXT NOT NULL,
    value               TEXT NOT NULL,
    event_time          TEXT NOT NULL,
    recorded_at         TEXT NOT NULL,
    source              TEXT,
    reason              TEXT NOT NULL,
    current_value       TEXT,
    current_event_time  TEXT,
    resolved            INTEGER NOT NULL DEFAULT 0,
    sightings           INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_conflicts_open
    ON conflicts(domain, fact_key)
    WHERE resolved = 0;

-- HEARSAY lane (Fable B1): an undated mention has a value + a source + the date
-- of the NOTE that mentioned it, but NO event_time. It must NEVER enter `facts`
-- (a fabricated event_time there participates in supersession forever and
-- resurrects the $12.69 burn). Hearsay is served as UNVERIFIED with provenance
-- and is excluded from currency entirely until a human/second-source promotes it.
CREATE TABLE IF NOT EXISTS hearsay (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    domain        TEXT NOT NULL,
    fact_key      TEXT NOT NULL,
    value         TEXT NOT NULL,
    mention_date  TEXT,            -- when the NOTE was written (NOT an event_time)
    recorded_at   TEXT NOT NULL,
    source        TEXT,
    status        TEXT NOT NULL DEFAULT 'unverified',  -- unverified/rejected/promoted
    quote         TEXT             -- Phase ① (G1-G4): verbatim source excerpt. See
                                    -- facts.quote comment; same backward-compat rule.
);

CREATE INDEX IF NOT EXISTS idx_hearsay_open
    ON hearsay(domain, fact_key)
    WHERE status = 'unverified';
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_event_time(s: str) -> datetime:
    """
    Parse an event_time string into a NAIVE datetime for comparison.
    Accepts date-only ("2026-07-01"), full ISO ("2026-07-01T00:00:00"),
    a trailing 'Z', and a few common human formats. Raises ValueError if
    the string cannot be parsed — v2 REFUSES to store an un-datable event
    (the v1 store accepted any string, which is how F7 hid).
    """
    if s is None:
        raise ValueError("event_time is required (got None)")
    s = str(s).strip()
    if not s:
        raise ValueError("event_time is empty")
    iso = s
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso)  # handles date-only and datetime
        if dt.tzinfo is not None:
            # Convert to UTC before dropping tz — stripping the offset without
            # converting inverted ordering across time zones (Claude Code R1).
            dt = dt.astimezone(timezone.utc)
        return dt.replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%Y/%m/%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"unparseable event_time: {s!r}")


def canonical_event_time(s: str) -> str:
    """Canonical ISO string for storage, so lexical order == temporal order."""
    return parse_event_time(s).isoformat()


import re as _re


def normalize_value(value, value_type=None) -> str:
    """
    Canonicalize a value BEFORE any equality/membership comparison, so format
    drift ("$12.69" vs "12.69", "$30" vs "30.00") can't fake a change or slip a
    stale echo past the guard (Fable B3). value_type comes from the registry;
    unknown types fall back to whitespace/case-normalized string.
    """
    if value is None:
        return ""
    s = str(value).strip()
    if value_type in ("money", "percent"):
        # Strip currency/percent/commas FIRST so a leading sign survives
        # ("-$5" -> "-5", not "5"), then canonicalize EVERY number found and
        # join them. Comparing only the first number silently swallowed real
        # multi-number changes ("$100 + $30/mo" vs "$100 + $75/mo") — Claude
        # Code R3. Joining keeps distinct compound values distinct.
        # Strip approx markers (~ ≈) and map the Unicode minus (U+2212) to ASCII
        # so a real negative sign survives. Strip ONLY the symbol that matches the
        # type ($ for money, % for percent) — never both, or "30% off" (money)
        # would collapse into "$30 off" (Claude Code round-4).
        cleaned = (s.replace("~", "").replace("≈", "").replace("−", "-"))
        cleaned = (cleaned.replace("$", "") if value_type == "money"
                   else cleaned.replace("%", ""))
        # N5 (2026-07-05): DO NOT strip every comma as a thousands separator —
        # a European comma-decimal ("12,69" == 12.69) was silently read as 1269,
        # a test-passing money poison (12.69 -> $1,269). Disambiguate instead:
        #   • confident DECIMAL comma  = "<digit>,<1-2 digits><boundary>" -> "."
        #     ("12,69"->"12.69", "1,5"->"1.5"); NOT if more digits/commas follow.
        #   • confident THOUSANDS comma = "<digit>,<exactly 3 digits><boundary>" -> ""
        #     ("1,299"->"1299", "1,234,567"->"1234567", "1,297.50"->"1297.50").
        # Anything left with a surviving comma (mixed/odd grouping) keeps the
        # comma, so the residue whitelist below rejects it into the string path —
        # an ambiguous value can never mint a wrong number.
        cleaned = _re.sub(r"(?<=\d),(\d{1,2})(?![\d,])", r".\1", cleaned)
        cleaned = _re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", cleaned)
        nums = _re.findall(r"-?(?:\d+(?:\.\d+)?|\.\d+)", cleaned)
        residue = _re.sub(r"-?(?:\d+(?:\.\d+)?|\.\d+)", "", cleaned)
        # Numeric path ONLY if the residue is pure formatting (space, dot, slash,
        # ASCII dash). A letter, paren, or foreign currency symbol (€ £ ¥ …) means
        # "not a plain number of this type" -> fall back to string, so "($30)" !=
        # "$30", "€30" != "$30", "30 days" != "$30". This whitelist (vs the old
        # "reject only [a-zA-Z]") is what closes the ~$0.30/mo tilde-drift poison
        # (Claude Code RR11 + RR13) and the sign/paren/currency false-equals.
        if nums and _re.fullmatch(r"[\s./\-]*", residue):
            fmt = "{:.2f}" if value_type == "money" else "{:.4f}"
            try:
                return "|".join(fmt.format(float(n)) for n in nums)
            except ValueError:
                pass
        # Fall back on the stripped string (keeps "$297/mo" == "297/mo").
        return " ".join(cleaned.lower().split())
    return " ".join(s.lower().split())


class FactStore:
    """Event-time-aware fact store, hardened (v2)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
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
                    source: str = None, correction_of: str = None,
                    contestable: bool = False, value_type: str = None,
                    quote: str = None,
                    _hold_txn: bool = False) -> str:
        """
        Record a fact, resolving currency by EVENT TIME. Returns a status
        string describing what happened, so callers/tests can assert on it:

            'inserted'            first-ever fact for this key -> current
            'superseded'          strictly-newer event replaced current
            'corrected'           explicit correction_of forced the replacement
            'stale_history'       older-event different value -> stored as history
            'noop'                same value, not newer -> just re-confirmed
            'conflict_queued'     equal event_time, different value (F2)
            'contestable_queued'  caller flagged the date as doubtful

        Never deletes. Never silently overrides on an equal or doubtful date.
        """
        recorded_at = _now_iso()
        norm_event = canonical_event_time(event_time)   # raises on garbage (F7)
        new_dt = parse_event_time(event_time)

        # When a resolver already holds a transaction, don't open our own (whose
        # early commit is the R6/R12 atomicity bug) — run inside theirs.
        txn = contextlib.nullcontext() if _hold_txn else self._conn
        with txn:
            current_row = self._conn.execute(
                """SELECT id, value, event_time FROM facts
                   WHERE domain = ? AND fact_key = ? AND valid_to IS NULL""",
                (domain, key),
            ).fetchone()

            if current_row is None:
                self._conn.execute(
                    """INSERT INTO facts
                       (domain, fact_key, value, event_time, recorded_at, valid_to, source, quote)
                       VALUES (?, ?, ?, ?, ?, NULL, ?, ?)""",
                    (domain, key, value, norm_event, recorded_at, source, quote),
                )
                if contestable:
                    # A doubtful FIRST fact is provisional: keep it as the only
                    # value we have, but flag it so serve discloses CONTESTED
                    # instead of presenting an untrusted anchor as settled truth
                    # (Grok A1 — first-insert must not silently anchor).
                    queued = self._queue_conflict(domain, key, value, norm_event,
                                                  recorded_at, source,
                                                  "contestable_first_insert",
                                                  None, None, value_type)
                    if queued:
                        return "inserted_contestable"
                    # Dedup hit = a twin conflict already exists — the human very
                    # likely already DISMISSED this exact doubtful value. Do not
                    # resurrect a rejected value as current (Claude Code RR3:
                    # dismissal + retry was blessing the anchor). Retract this
                    # provisional row so serve stays UNKNOWN, honoring the prior
                    # "don't trust this."
                    self._conn.execute(
                        """UPDATE facts SET valid_to = ?
                           WHERE domain = ? AND fact_key = ? AND valid_to IS NULL""",
                        (recorded_at, domain, key),
                    )
                    return "inserted_contestable_retracted"
                return "inserted"

            current_id = current_row["id"]
            current_value = current_row["value"]
            current_event_raw = current_row["event_time"]
            current_dt = parse_event_time(current_event_raw)

            # Explicit, intentional correction wins regardless of timing.
            if correction_of is not None:
                new_valid_to = max(norm_event, current_event_raw)
                self._conn.execute(
                    "UPDATE facts SET valid_to = ? WHERE id = ?",
                    (new_valid_to, current_id),
                )
                self._conn.execute(
                    """INSERT INTO facts
                       (domain, fact_key, value, event_time, recorded_at, valid_to, source, quote)
                       VALUES (?, ?, ?, ?, ?, NULL, ?, ?)""",
                    (domain, key, value, norm_event, recorded_at,
                     (source or "") + f" [correction_of:{correction_of}]", quote),
                )
                return "corrected"

            same_value = (normalize_value(value, value_type)
                          == normalize_value(current_value, value_type))

            # A DOUBTFUL input must never move currency OR the freshness clock.
            # This check now runs BEFORE the refresh branch (Claude Code RR4: the
            # refresh used to fire first, so a low-confidence same-value mention
            # with a mis-extracted newer date silently moved event_time and
            # masked staleness — a timestamp-sized hole in "doubtful never wins").
            if contestable:
                if same_value:
                    # doubtful re-confirmation: a breadcrumb only, NEVER a refresh
                    self._conn.execute(
                        "UPDATE facts SET recorded_at = ? WHERE id = ?",
                        (recorded_at, current_id),
                    )
                    return "noop"
                queued = self._queue_conflict(domain, key, value, norm_event,
                                              recorded_at, source,
                                              "contestable_event_time",
                                              current_value, current_event_raw,
                                              value_type)
                return "contestable_queued" if queued else "contestable_deduped"

            # CONFIDENT same value (NORMALIZED) -> re-confirmation. Refresh the
            # freshness clock only when strictly newer (Fable B3 + Claude Code R2).
            if same_value:
                if new_dt > current_dt:
                    self._conn.execute(
                        "UPDATE facts SET event_time = ?, recorded_at = ? WHERE id = ?",
                        (norm_event, recorded_at, current_id),
                    )
                    return "refreshed"
                self._conn.execute(
                    "UPDATE facts SET recorded_at = ? WHERE id = ?",
                    (recorded_at, current_id),
                )
                return "noop"

            if new_dt > current_dt:
                # Strictly newer -> supersede.
                self._conn.execute(
                    "UPDATE facts SET valid_to = ? WHERE id = ?",
                    (norm_event, current_id),
                )
                self._conn.execute(
                    """INSERT INTO facts
                       (domain, fact_key, value, event_time, recorded_at, valid_to, source, quote)
                       VALUES (?, ?, ?, ?, ?, NULL, ?, ?)""",
                    (domain, key, value, norm_event, recorded_at, source, quote),
                )
                return "superseded"

            if new_dt == current_dt:
                # F2: equal event_time, DIFFERENT value -> do NOT guess. Queue it,
                # honoring the dedup bool (Claude Code RR7) so ingest never claims
                # a contest that serve can't see.
                queued = self._queue_conflict(domain, key, value, norm_event,
                                              recorded_at, source,
                                              "equal_event_time_conflict",
                                              current_value, current_event_raw,
                                              value_type)
                return "conflict_queued" if queued else "conflict_deduped"

            # new_dt < current_dt, different value: stale re-mention -> history.
            # Dedup (Claude Code R5): an identical stale mention re-ingested must
            # not pile up duplicate history rows.
            dup = self._conn.execute(
                """SELECT id FROM facts
                   WHERE domain = ? AND fact_key = ? AND value = ?
                     AND event_time = ? AND valid_to IS NOT NULL""",
                (domain, key, value, norm_event),
            ).fetchone()
            if dup:
                return "stale_history"
            self._conn.execute(
                """INSERT INTO facts
                   (domain, fact_key, value, event_time, recorded_at, valid_to, source, quote)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (domain, key, value, norm_event, recorded_at,
                 current_event_raw, source, quote),
            )
            return "stale_history"

    def _queue_conflict(self, domain, key, value, event_time, recorded_at,
                        source, reason, current_value, current_event_time,
                        value_type=None):
        # Dedup on the NORMALIZED value + event_time (Claude Code RR6: keying on
        # the RAW value let "12.69" re-open a dismissed "$12.69" because the echo
        # guard matches normalized). Compare against every conflict for this key
        # at this event_time, in ANY resolution state.
        nv = normalize_value(value, value_type)
        rows = self._conn.execute(
            """SELECT id, value, resolved, source FROM conflicts
               WHERE domain = ? AND fact_key = ? AND event_time = ?""",
            (domain, key, event_time),
        ).fetchall()
        for r in rows:
            if normalize_value(r["value"], value_type) == nv:
                # Corroboration (Claude Code RR8): a NEW independent source for an
                # OPEN contest is RETAINED — its source is appended and sightings
                # bumped, instead of vanishing (the signal S3 containment / the
                # auto-confirm ladder needs). A same-source re-ingest (retry /
                # backfill) is deliberately NOT counted as corroboration.
                if r["resolved"] == 0 and source:
                    # Sources are a JSON ARRAY, not a comma-joined string (Claude
                    # Code RR12): splitting on "," double-counted a source whose
                    # own name contains a comma ("notes, page 2"), inflating the
                    # corroboration count the S3 auto-confirm ladder will trust.
                    try:
                        srcs = json.loads(r["source"]) if r["source"] else []
                        if not isinstance(srcs, list):
                            srcs = [r["source"]]
                    except (ValueError, TypeError):
                        srcs = [r["source"]] if r["source"] else []
                    if source not in srcs:
                        srcs.append(source)
                        self._conn.execute(
                            "UPDATE conflicts SET source = ?, sightings = sightings + 1 "
                            "WHERE id = ?",
                            (json.dumps(srcs), r["id"]),
                        )
                # Deduped — return False so callers report an HONEST status
                # ("echo_deduped") instead of claiming a contest serve can't see
                # (Grok #14).
                return False
        self._conn.execute(
            """INSERT INTO conflicts
               (domain, fact_key, value, event_time, recorded_at, source,
                reason, current_value, current_event_time, resolved)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (domain, key, value, event_time, recorded_at,
             json.dumps([source] if source else []), reason,
             current_value, current_event_time),
        )
        return True

    def resolve_conflict(self, conflict_id: int, accept: bool = False) -> str:
        """
        Human decision on a queued conflict. If accept=True, the queued value
        becomes current via an explicit correction; otherwise it is dismissed.
        Either way the conflict is marked resolved.
        """
        row = self._conn.execute(
            "SELECT * FROM conflicts WHERE id = ? AND resolved = 0", (conflict_id,)
        ).fetchone()
        if row is None:
            return "not_found_or_already_resolved"
        with self._conn:
            if accept:
                # _hold_txn=True: the correction and the resolved-flag update
                # commit together, so a crash can't double-apply on retry (R6).
                self.assert_fact(
                    row["domain"], row["fact_key"], row["value"],
                    row["event_time"], source=row["source"],
                    correction_of=f"conflict:{conflict_id}", _hold_txn=True,
                )
            elif row["reason"] == "contestable_first_insert":
                # Dismissing a DOUBTFUL first value means "don't trust this" — it
                # must NOT leave that value as the only current fact (serve would
                # flip CONTESTED -> CURRENT). Retract the provisional row — but
                # ONLY if the current row is STILL that exact doubtful value
                # (Claude Code RR5: the old blanket retract destroyed a good fact
                # that had legitimately superseded the doubtful one).
                cur = self._conn.execute(
                    """SELECT id, value, event_time FROM facts
                       WHERE domain = ? AND fact_key = ? AND valid_to IS NULL""",
                    (row["domain"], row["fact_key"]),
                ).fetchone()
                if (cur and cur["value"] == row["value"]
                        and cur["event_time"] == row["event_time"]):
                    self._conn.execute(
                        "UPDATE facts SET valid_to = ? WHERE id = ?",
                        (_now_iso(), cur["id"]),
                    )
            self._conn.execute(
                "UPDATE conflicts SET resolved = 1 WHERE id = ?", (conflict_id,)
            )
        return "accepted" if accept else "dismissed"

    # ------------------------------------------------------------------
    # Hearsay lane (Fable B1) — undated mentions, never in `facts`
    # ------------------------------------------------------------------

    def add_hearsay(self, domain, key, value, mention_date=None, source=None,
                    quote=None):
        """Record an undated mention. It NEVER touches `facts` / currency."""
        with self._conn:
            self._conn.execute(
                """INSERT INTO hearsay
                   (domain, fact_key, value, mention_date, recorded_at, source, status, quote)
                   VALUES (?, ?, ?, ?, ?, ?, 'unverified', ?)""",
                (domain, key, value, mention_date, _now_iso(), source, quote),
            )
        return "hearsay"

    def get_hearsay(self, domain, key, only_unverified=True):
        q = "SELECT * FROM hearsay WHERE domain = ? AND fact_key = ?"
        params = [domain, key]
        if only_unverified:
            q += " AND status = 'unverified'"
        q += " ORDER BY id ASC"
        return [dict(r) for r in self._conn.execute(q, params).fetchall()]

    def resolve_hearsay(self, hearsay_id, action, event_time=None,
                        value_type=None):
        """
        Three-way resolution (Fable B1): a dismiss must never bless an anchor.
          action='accept'       -> requires an explicit event_time; promotes the
                                    value into `facts` as a real dated fact.
          action='reject'       -> mark rejected; nothing enters facts.
          action='keep_unknown' -> leave it as unverified hearsay (value kept,
                                    date still unknown; served UNVERIFIED).
        """
        row = self._conn.execute(
            "SELECT * FROM hearsay WHERE id = ? AND status = 'unverified'",
            (hearsay_id,),
        ).fetchone()
        if row is None:
            return "not_found_or_already_resolved"
        if action == "accept":
            if not event_time:
                return "accept_requires_event_time"
            # Guard (Grok #15): an undated mention refers to something that was
            # ALREADY true when mentioned, so a promoted event_time on/after the
            # mention date is the doc-date landmine, human-driven — reject it.
            md = row["mention_date"]
            if md:
                # Fail CLOSED on a garbage mention_date (Claude Code RR10):
                # whole_loop passes doc_date verbatim, so an unparseable date is
                # real input — skipping the guard would let an arbitrary event
                # date anchor. Reject rather than silently allow.
                try:
                    md_dt = parse_event_time(md)
                except ValueError:
                    return "reject_unparseable_mention_date"
                if parse_event_time(event_time) >= md_dt:
                    return "reject_event_time_not_before_mention"
            # Promote via NORMAL event-time rules (NOT a forced correction), so a
            # correctly-older date files as history instead of leapfrogging a
            # legitimately-newer current fact (Grok #15). Single transaction so
            # the promote + status flip are atomic (R12).
            with self._conn:
                self.assert_fact(row["domain"], row["fact_key"], row["value"],
                                 event_time, source=row["source"],
                                 value_type=value_type, quote=row["quote"],
                                 _hold_txn=True)
                self._conn.execute(
                    "UPDATE hearsay SET status = 'promoted' WHERE id = ?",
                    (hearsay_id,))
            return "promoted"
        if action == "reject":
            with self._conn:
                self._conn.execute(
                    "UPDATE hearsay SET status = 'rejected' WHERE id = ?",
                    (hearsay_id,))
            return "rejected"
        if action == "keep_unknown":
            return "kept_unverified"
        return "unknown_action"

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def current(self, domain: str, key: str):
        row = self._conn.execute(
            """SELECT value FROM facts
               WHERE domain = ? AND fact_key = ? AND valid_to IS NULL""",
            (domain, key),
        ).fetchone()
        return row["value"] if row else None

    def current_row(self, domain: str, key: str):
        row = self._conn.execute(
            """SELECT * FROM facts
               WHERE domain = ? AND fact_key = ? AND valid_to IS NULL""",
            (domain, key),
        ).fetchone()
        return dict(row) if row else None

    def history(self, domain: str, key: str):
        cur = self._conn.execute(
            """SELECT id, value, event_time, recorded_at, valid_to, source
               FROM facts
               WHERE domain = ? AND fact_key = ?
               ORDER BY event_time ASC, id ASC""",
            (domain, key),
        )
        return [dict(r) for r in cur.fetchall()]

    def open_conflicts(self, domain: str = None, key: str = None):
        q = "SELECT * FROM conflicts WHERE resolved = 0"
        params = []
        if domain is not None:
            q += " AND domain = ?"
            params.append(domain)
        if key is not None:
            q += " AND fact_key = ?"
            params.append(key)
        q += " ORDER BY id ASC"
        return [dict(r) for r in self._conn.execute(q, params).fetchall()]

    def audit(self):
        violations = [dict(r) for r in self._conn.execute(
            """SELECT domain, fact_key, COUNT(*) AS current_count
               FROM facts WHERE valid_to IS NULL
               GROUP BY domain, fact_key HAVING COUNT(*) > 1"""
        ).fetchall()]
        totals = self._conn.execute("SELECT COUNT(*) AS n FROM facts").fetchone()["n"]
        current_totals = self._conn.execute(
            "SELECT COUNT(*) AS n FROM facts WHERE valid_to IS NULL"
        ).fetchone()["n"]
        distinct_keys = self._conn.execute(
            "SELECT COUNT(DISTINCT domain || '::' || fact_key) AS n FROM facts"
        ).fetchone()["n"]
        open_conflicts = self._conn.execute(
            "SELECT COUNT(*) AS n FROM conflicts WHERE resolved = 0"
        ).fetchone()["n"]
        return {
            "ok": len(violations) == 0,
            "violations": violations,
            "total_facts": totals,
            "current_facts": current_totals,
            "distinct_keys": distinct_keys,
            "open_conflicts": open_conflicts,
        }

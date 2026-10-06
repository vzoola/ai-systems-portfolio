"""
test_deprecation.py — Phase ③ deprecation serve/ingest semantics proof
(SPEC_SOAK_AND_HARDENING.md PART 2 Sec 2.2, SKILL hq-memory-soak-campaign
§3 Phase ③).

Covers: (a) deprecate a key -> serve returns DEPRECATED + old value +
superseded_by pointer; (b) a direct write to a deprecated key lands in the
EXISTING conflicts queue (fact_store_v2's `conflicts` table via
`_queue_conflict`), reason=deprecated_target, NOT silently accepted, NOT
auto-migrated; (c) a write to an ALIAS of a deprecated key resolves to the
successor key as the write TARGET (old value NOT copied — the successor's
keyspace starts empty); (d) retirement: deprecated + 90d + zero serve hits
(driven by serve()'s injectable `as_of` clock, never a wall-clock call) ->
retired -> serve returns UNKNOWN + pointer, and a key that WAS served during
the deprecation window does NOT auto-retire; (e) deprecation touches ZERO
rows in the `facts` table — asserted directly against the table.

Registry fixtures for this file are injected directly into registry.py's
loaded `_BY_KEY` dict (never into registry.json — the real 73-key sheet is
Vahe-frozen and untouched by this file; nothing here is counted in
test_registry.py's 73-key total, which runs in its own process). This is the
same seam (`registry.get_key` / `registry.find_key_by_alias`) whole_loop.py
consults at ingest/serve time, so the test exercises the real code path.

House convention: module-level check()/FAILS, main() prints a scorecard,
sys.exit(0/1). Stdlib only.
"""

import os
import sys
import tempfile

import registry
from whole_loop import MemoryLoop

FAILS = []


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILS.append(label)


def fresh():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(p)
    return MemoryLoop(p), p


def ext(value, event_time, kind="event", confident=True):
    return {"value": value, "event_time": event_time, "kind": kind,
            "confident": confident}


def facts_rows(m, domain, key):
    """Raw read of the `facts` table for (domain, key) — bypasses every
    accessor, so a deprecation that mutated a row (it must not) can't hide
    behind a friendly wrapper."""
    cur = m.store._conn.execute(
        "SELECT id, value, event_time, valid_to, source, quote FROM facts "
        "WHERE domain = ? AND fact_key = ? ORDER BY id ASC",
        (domain, key),
    )
    return [dict(r) for r in cur.fetchall()]


def _inject_key(domain, key, status="active", deprecated_at=None,
                superseded_by=None, deprecation_reason=None, aliases=None,
                value_type="string"):
    """Register a synthetic test-only key directly into registry._BY_KEY
    (registry.json is NEVER touched). Mirrors the real registry entry shape
    (registry.json's per-key schema) so registry.get_key() / find_key_by_alias()
    behave exactly as they would for a real frozen key."""
    entry = {
        "key": key, "domain": domain, "value_type": value_type,
        "stakes": "normal", "perishable_days": None, "guarded": False,
        "definition": f"test-only fixture key {domain}/{key}.",
        "scope_excludes": [], "source_types": ["convo_log"],
        "anchor_terms": [], "exclusion_terms": [],
        "aliases": aliases or [], "status": status,
        "created_at": "2026-01-01", "deprecated_at": deprecated_at,
        "superseded_by": superseded_by or [], "deprecation_reason": deprecation_reason,
        "grade_class": "golden", "tolerance": None, "as_of": None,
    }
    registry._BY_KEY[(domain, key)] = entry
    return entry


# ---------------------------------------------------------------------------
# (a) Deprecate a key -> serve returns DEPRECATED + old value + pointer
# ---------------------------------------------------------------------------

def t_serve_deprecated():
    print("\n-- (a) serve on a deprecated key: DEPRECATED + old value + pointer --")
    m, p = fresh()
    try:
        _inject_key("dep", "old_key", status="active")
        st = m.ingest("dep", "old_key", ext("v1-original", "2026-01-01"), "2026-01-01")
        check("initial insert while active -> 'inserted'", st == "inserted")
        pre = m.serve("dep", "old_key")
        check("pre-deprecation serve is CURRENT", pre["status"] == "CURRENT")

        # Deprecate — registry metadata ONLY, no store call at all.
        registry._BY_KEY[("dep", "old_key")]["status"] = "deprecated"
        registry._BY_KEY[("dep", "old_key")]["deprecated_at"] = "2026-01-15"
        registry._BY_KEY[("dep", "old_key")]["superseded_by"] = ["new_key"]
        registry._BY_KEY[("dep", "old_key")]["deprecation_reason"] = "test: split into new_key"

        served = m.serve("dep", "old_key")
        check("status == DEPRECATED", served["status"] == "DEPRECATED")
        check("old value still shown (history not falsified)",
              served["value"] == "v1-original")
        check("event_time carried through", served["event_time"] is not None)
        check("superseded_by pointer == ['new_key']",
              served.get("superseded_by") == ["new_key"])
        check("deprecation_reason surfaced",
              served.get("deprecation_reason") == "test: split into new_key")
        check("registry_status == deprecated", served.get("registry_status") == "deprecated")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


# ---------------------------------------------------------------------------
# (b) direct write to a deprecated key -> conflicts queue, never silent,
#     never auto-migrated
# ---------------------------------------------------------------------------

def t_ingest_deprecated_target_queues():
    print("\n-- (b) direct write to a deprecated key -> conflicts queue --")
    m, p = fresh()
    try:
        _inject_key("dep", "b_key", status="active")
        m.ingest("dep", "b_key", ext("v1-original", "2026-01-01"), "2026-01-01")
        pre_rows = facts_rows(m, "dep", "b_key")
        check("exactly 1 facts row before deprecation", len(pre_rows) == 1)

        registry._BY_KEY[("dep", "b_key")]["status"] = "deprecated"
        registry._BY_KEY[("dep", "b_key")]["deprecated_at"] = "2026-01-15"
        registry._BY_KEY[("dep", "b_key")]["superseded_by"] = ["b_successor"]

        pre_conflicts = len(m.store.open_conflicts("dep", "b_key"))
        st = m.ingest("dep", "b_key", ext("SNEAKY-NEW-VALUE", "2026-02-01"), "2026-02-01")
        check("ingest status == deprecated_target_queued", st == "deprecated_target_queued")

        post_conflicts = m.store.open_conflicts("dep", "b_key")
        check("exactly one NEW open conflict queued",
              len(post_conflicts) == pre_conflicts + 1)
        c = post_conflicts[-1]
        check("conflict reason == deprecated_target", c["reason"] == "deprecated_target")
        check("conflict carries the attempted (rejected) value",
              c["value"] == "SNEAKY-NEW-VALUE")

        # Never silently accepted, never auto-migrated: the facts table must
        # be untouched by this write.
        post_rows = facts_rows(m, "dep", "b_key")
        check("facts table UNCHANGED by the deprecated-target write",
              post_rows == pre_rows)

        served = m.serve("dep", "b_key")
        check("serve still shows the OLD value, not the sneaky new one",
              served["value"] == "v1-original")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


# ---------------------------------------------------------------------------
# (c) write to an ALIAS of a deprecated key -> resolves to successor;
#     old value NOT copied
# ---------------------------------------------------------------------------

def t_ingest_alias_redirects_to_successor():
    print("\n-- (c) write to an alias of a deprecated key -> successor, no copy --")
    m, p = fresh()
    try:
        _inject_key("dep", "c_old", status="active", aliases=["c_alias"])
        m.ingest("dep", "c_old", ext("OLD-VALUE-NEVER-COPY", "2026-01-01"), "2026-01-01")

        registry._BY_KEY[("dep", "c_old")]["status"] = "deprecated"
        registry._BY_KEY[("dep", "c_old")]["deprecated_at"] = "2026-01-15"
        registry._BY_KEY[("dep", "c_old")]["superseded_by"] = ["c_new"]

        # Successor starts completely unregistered/empty — no fixture needed;
        # ingest must create it fresh via the redirect.
        pre_successor = m.serve("dep", "c_new")
        check("successor is UNKNOWN before any write", pre_successor["status"] == "UNKNOWN")

        alias_owner = registry.find_key_by_alias("dep", "c_alias")
        check("find_key_by_alias resolves c_alias -> c_old",
              alias_owner is not None and alias_owner["key"] == "c_old")

        pre_alias_metric = m.metrics["alias_redirect_to_successor"]
        st = m.ingest("dep", "c_alias", ext("FRESH-SUCCESSOR-VALUE", "2026-02-02"), "2026-02-02")
        check("alias write returns a normal insert status (redirected, not queued)",
              st == "inserted")
        check("alias_redirect_to_successor metric incremented",
              m.metrics["alias_redirect_to_successor"] == pre_alias_metric + 1)

        successor_served = m.serve("dep", "c_new")
        check("successor now CURRENT with the FRESH value",
              successor_served["status"] == "CURRENT"
              and successor_served["value"] == "FRESH-SUCCESSOR-VALUE")

        # The old key's own facts must be completely untouched — the write
        # landed on c_new, never on c_old, and the old value was never copied.
        old_rows = facts_rows(m, "dep", "c_old")
        check("c_old still has exactly its original 1 row",
              len(old_rows) == 1 and old_rows[0]["value"] == "OLD-VALUE-NEVER-COPY")
        new_rows = facts_rows(m, "dep", "c_new")
        check("c_new has exactly 1 row (the fresh write, not a copy of c_old)",
              len(new_rows) == 1 and new_rows[0]["value"] == "FRESH-SUCCESSOR-VALUE")

        old_served = m.serve("dep", "c_old")
        check("c_old still serves DEPRECATED with its OWN old value",
              old_served["status"] == "DEPRECATED"
              and old_served["value"] == "OLD-VALUE-NEVER-COPY")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


# ---------------------------------------------------------------------------
# (d) retirement: deprecated + 90d + zero serve hits -> retired
# ---------------------------------------------------------------------------

def t_retirement_zero_hits():
    print("\n-- (d1) deprecated + >90d + ZERO prior serve hits -> retired --")
    m, p = fresh()
    try:
        _inject_key("dep", "d1_key", status="active")
        m.ingest("dep", "d1_key", ext("d1-original", "2025-12-01"), "2025-12-01")
        registry._BY_KEY[("dep", "d1_key")]["status"] = "deprecated"
        registry._BY_KEY[("dep", "d1_key")]["deprecated_at"] = "2026-01-01"
        registry._BY_KEY[("dep", "d1_key")]["superseded_by"] = ["d1_successor"]
        registry._BY_KEY[("dep", "d1_key")]["deprecation_reason"] = "test retirement"

        # First-ever serve() call on this key happens AFTER the 90-day
        # horizon, with zero prior hits -> retires immediately.
        served = m.serve("dep", "d1_key", as_of="2026-04-02")  # 91 days after deprecated_at
        check("status == UNKNOWN (retired)", served["status"] == "UNKNOWN")
        check("value withheld", served["value"] is None)
        check("registry_status == retired", served.get("registry_status") == "retired")
        check("superseded_by pointer preserved", served.get("superseded_by") == ["d1_successor"])

        # Sticky: a later call stays retired even though it's technically the
        # "second" call now (must not flip back to DEPRECATED).
        served2 = m.serve("dep", "d1_key", as_of="2026-04-03")
        check("stays retired on a subsequent call (sticky)", served2["status"] == "UNKNOWN")

        # facts table completely untouched by the whole retirement process.
        rows = facts_rows(m, "dep", "d1_key")
        check("facts table unchanged (1 original row) after retirement",
              len(rows) == 1 and rows[0]["value"] == "d1-original")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


def t_retirement_skipped_if_hit_during_window():
    print("\n-- (d2) served WITHIN the window -> stays DEPRECATED past 90d, not retired --")
    m, p = fresh()
    try:
        _inject_key("dep", "d2_key", status="active")
        m.ingest("dep", "d2_key", ext("d2-original", "2025-12-01"), "2025-12-01")
        registry._BY_KEY[("dep", "d2_key")]["status"] = "deprecated"
        registry._BY_KEY[("dep", "d2_key")]["deprecated_at"] = "2026-01-01"
        registry._BY_KEY[("dep", "d2_key")]["superseded_by"] = ["d2_successor"]

        # Served ONCE, well inside the 90-day window -> registers a hit.
        early = m.serve("dep", "d2_key", as_of="2026-01-10")
        check("early serve (inside window) is DEPRECATED, not retired",
              early["status"] == "DEPRECATED")

        # Now past the 90-day horizon — hits != 0, so it must NOT retire.
        later = m.serve("dep", "d2_key", as_of="2026-04-05")
        check("still DEPRECATED past 90d because it WAS hit during the window",
              later["status"] == "DEPRECATED")
        check("old value still served", later["value"] == "d2-original")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


def t_persisted_retired_status():
    print("\n-- (d3) a key persisted as status=retired directly -> UNKNOWN + pointer --")
    m, p = fresh()
    try:
        _inject_key("dep", "d3_key", status="active")
        m.ingest("dep", "d3_key", ext("d3-original", "2025-12-01"), "2025-12-01")
        registry._BY_KEY[("dep", "d3_key")]["status"] = "retired"
        registry._BY_KEY[("dep", "d3_key")]["deprecated_at"] = "2025-12-15"
        registry._BY_KEY[("dep", "d3_key")]["superseded_by"] = ["d3_successor"]

        served = m.serve("dep", "d3_key")  # no as_of needed — status is retired outright
        check("persisted-retired serve == UNKNOWN", served["status"] == "UNKNOWN")
        check("value withheld", served["value"] is None)
        check("pointer preserved", served.get("superseded_by") == ["d3_successor"])
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


# ---------------------------------------------------------------------------
# (e) deprecation touches ZERO rows in `facts` — direct table assertion
#     across the FULL lifecycle (deprecate -> rejected direct write ->
#     alias redirect -> retirement)
# ---------------------------------------------------------------------------

def t_facts_table_untouched_end_to_end():
    print("\n-- (e) facts table touched ZERO times by deprecation itself, full lifecycle --")
    m, p = fresh()
    try:
        _inject_key("dep", "e_key", status="active", aliases=["e_alias"])
        m.ingest("dep", "e_key", ext("e-original", "2026-01-01"), "2026-01-01")
        snapshot_before = facts_rows(m, "dep", "e_key")
        check("1 row exists before any deprecation activity", len(snapshot_before) == 1)

        # Deprecate: PURE registry mutation, zero store calls.
        registry._BY_KEY[("dep", "e_key")]["status"] = "deprecated"
        registry._BY_KEY[("dep", "e_key")]["deprecated_at"] = "2026-01-02"
        registry._BY_KEY[("dep", "e_key")]["superseded_by"] = ["e_successor"]
        snapshot_after_deprecate = facts_rows(m, "dep", "e_key")
        check("facts row for e_key byte-identical immediately after deprecation",
              snapshot_after_deprecate == snapshot_before)

        # Serve (DEPRECATED) — read-only, must not touch facts.
        m.serve("dep", "e_key")
        check("facts row unchanged after a DEPRECATED serve",
              facts_rows(m, "dep", "e_key") == snapshot_before)

        # Attempted direct write — must queue, not touch facts.
        m.ingest("dep", "e_key", ext("REJECTED", "2026-01-03"), "2026-01-03")
        check("facts row unchanged after a rejected direct write",
              facts_rows(m, "dep", "e_key") == snapshot_before)

        # Alias write — lands on the SUCCESSOR, e_key's own row must still
        # be untouched.
        m.ingest("dep", "e_alias", ext("SUCCESSOR-VAL", "2026-01-04"), "2026-01-04")
        check("facts row for e_key STILL unchanged after an alias/successor write",
              facts_rows(m, "dep", "e_key") == snapshot_before)

        # Retirement — read-only registry+in-memory computation, must not
        # touch facts either.
        m.serve("dep", "e_key", as_of="2026-05-01")
        check("facts row for e_key unchanged even after auto-retirement",
              facts_rows(m, "dep", "e_key") == snapshot_before)

        check("no row was ever added/removed for e_key across the whole lifecycle",
              len(facts_rows(m, "dep", "e_key")) == 1)
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


def main():
    print("=" * 60)
    print("test_deprecation — Phase ③ deprecation serve/ingest semantics")
    print("=" * 60)

    t_serve_deprecated()
    t_ingest_deprecated_target_queues()
    t_ingest_alias_redirects_to_successor()
    t_retirement_zero_hits()
    t_retirement_skipped_if_hit_during_window()
    t_persisted_retired_status()
    t_facts_table_untouched_end_to_end()

    print("\n" + "=" * 60)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILED")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL PASSED — deprecation is a registry-metadata join at "
          "ingest/serve time; the `facts` table is never touched, no value "
          "is ever auto-migrated onto a successor, and the existing "
          "`conflicts` queue (fact_store_v2._queue_conflict) is reused for "
          "deprecated-target writes with zero side doors.")
    sys.exit(0)


if __name__ == "__main__":
    main()

"""
test_freshness_v2.py — adversarial test for fact_store_v2.

Every scenario asserts a REAL end-state (the current value / the conflict
queue), not "it ran". Exit 0 only if every check passes. Scenario 3 is the
HONEST rewrite of the case v1's test encoded as "passing" while it was
actually a silent bug (equal-event-time lottery, F2).
"""

import os
import sys
import tempfile

from fact_store_v2 import FactStore, parse_event_time, canonical_event_time

FAILURES = []


def check(label, cond):
    mark = "PASS" if cond else "FAIL"
    if not cond:
        FAILURES.append(label)
    print(f"  [{mark}] {label}")


def fresh_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # let sqlite create it fresh
    return FactStore(path), path


def scenario_1_anti_stale():
    """The $12.69 trap: a stale OLD-event value mentioned LATE must not win."""
    print("\nScenario 1 — anti-stale ($12.69 trap):")
    s, path = fresh_store()
    try:
        # Real event: balance became $30 on 2026-06-20 (topped up).
        s.assert_fact("vapi", "credit_balance", "$30", "2026-06-20")
        # Later note re-mentions the OLD $12.69 (event date 2026-06-14) on 6/23.
        st = s.assert_fact("vapi", "credit_balance", "$12.69", "2026-06-14")
        check("stale older-event re-mention stored as history, not current",
              st == "stale_history")
        check("current is still $30 (stale $12.69 did NOT win)",
              s.current("vapi", "credit_balance") == "$30")
        check("history retains both values", len(s.history("vapi", "credit_balance")) == 2)
    finally:
        s.close(); os.path.exists(path) and os.unlink(path)


def scenario_2_f7_date_formats():
    """F7: mixed date formats must compare temporally, not lexically."""
    print("\nScenario 2 — F7 mixed date formats:")
    s, path = fresh_store()
    try:
        # current at date-only 2026-07-01
        s.assert_fact("otm", "price", "$297", "2026-07-01")
        # newer fact expressed as full datetime — lexically "<" the date-only
        # string in the naive v1 compare, but temporally NEWER.
        st = s.assert_fact("otm", "price", "$347", "2026-07-02T09:30:00")
        check("full-datetime newer value supersedes date-only current",
              st == "superseded")
        check("current is $347", s.current("otm", "price") == "$347")
        # And the reverse: an OLDER full-datetime must NOT override.
        st2 = s.assert_fact("otm", "price", "$199", "2026-06-30T23:59:59")
        check("older full-datetime stored as history, current unchanged",
              st2 == "stale_history" and s.current("otm", "price") == "$347")
        # canonical storage: date-only got normalized to an ISO datetime string
        hist = s.history("otm", "price")
        check("event_time stored canonically (all parseable)",
              all(parse_event_time(h["event_time"]) is not None for h in hist))
    finally:
        s.close(); os.path.exists(path) and os.unlink(path)


def scenario_3_f2_equal_time_conflict():
    """
    F2 — the honest version. Two DIFFERENT values with the SAME event_time
    must NOT resolve by ingestion order. v1's test asserted the lottery winner
    as 'correct'; v2 must instead REFUSE to guess and queue a conflict, leaving
    current untouched — AND the result must be identical regardless of order.
    """
    print("\nScenario 3 — F2 equal-event-time conflict (order independence):")
    for order in (("A", "B"), ("B", "A")):
        s, path = fresh_store()
        try:
            first, second = order
            s.assert_fact("infra", "region", first, "2026-06-15")
            st = s.assert_fact("infra", "region", second, "2026-06-15")
            check(f"[ingest {order}] equal-time different value -> conflict_queued",
                  st == "conflict_queued")
            check(f"[ingest {order}] current stays the FIRST-seen value ({first}), not a lottery",
                  s.current("infra", "region") == first)
            check(f"[ingest {order}] exactly one open conflict recorded",
                  len(s.open_conflicts("infra", "region")) == 1)
        finally:
            s.close(); os.path.exists(path) and os.unlink(path)


def scenario_4_explicit_correction():
    """An explicit correction_of overrides even at equal/older time."""
    print("\nScenario 4 — explicit correction:")
    s, path = fresh_store()
    try:
        s.assert_fact("infra", "region", "us-east", "2026-06-15")
        st = s.assert_fact("infra", "region", "us-west", "2026-06-15",
                           correction_of="typo")
        check("correction_of forces supersede at equal time", st == "corrected")
        check("current is corrected value us-west",
              s.current("infra", "region") == "us-west")
    finally:
        s.close(); os.path.exists(path) and os.unlink(path)


def scenario_5_contestable_date():
    """
    From the extraction eval: a doubtful date (like qwen dating a June fact as
    February) must NEVER auto-supersede. It queues for a human instead.
    """
    print("\nScenario 5 — contestable event_time (extraction uncertainty):")
    s, path = fresh_store()
    try:
        s.assert_fact("infra", "x_api_price", "~$0.30/mo", "2026-06-16")
        # extractor returns a CONTRADICTING value dated Feb (likely wrong date)
        st = s.assert_fact("infra", "x_api_price", "~$100/mo", "2026-02-01",
                           contestable=True)
        check("contestable fact is queued, not applied", st == "contestable_queued")
        check("current unchanged (~$0.30/mo held)",
              s.current("infra", "x_api_price") == "~$0.30/mo")
        check("one open conflict with reason=contestable_event_time",
              any(c["reason"] == "contestable_event_time"
                  for c in s.open_conflicts("infra", "x_api_price")))
    finally:
        s.close(); os.path.exists(path) and os.unlink(path)


def scenario_6_resolve_conflict():
    """A human accepting a queued conflict makes it current via correction."""
    print("\nScenario 6 — human resolves a queued conflict:")
    s, path = fresh_store()
    try:
        s.assert_fact("infra", "region", "us-east", "2026-06-15")
        s.assert_fact("infra", "region", "eu-west", "2026-06-15")  # -> conflict
        conflicts = s.open_conflicts("infra", "region")
        check("a conflict exists to resolve", len(conflicts) == 1)
        res = s.resolve_conflict(conflicts[0]["id"], accept=True)
        check("resolve accepted", res == "accepted")
        check("current is now the accepted value eu-west",
              s.current("infra", "region") == "eu-west")
        check("no open conflicts remain", len(s.open_conflicts("infra", "region")) == 0)
    finally:
        s.close(); os.path.exists(path) and os.unlink(path)


def scenario_7_garbage_date_rejected():
    """v2 refuses an un-datable event_time (v1 accepted any string = F7 hid)."""
    print("\nScenario 7 — garbage date rejected:")
    s, path = fresh_store()
    try:
        raised = False
        try:
            s.assert_fact("x", "y", "v", "not-a-date")
        except ValueError:
            raised = True
        check("unparseable event_time raises ValueError", raised)
        check("nothing was stored", s.current("x", "y") is None)
    finally:
        s.close(); os.path.exists(path) and os.unlink(path)


def scenario_8_audit_integrity():
    """The one-current-row invariant holds and conflicts are counted."""
    print("\nScenario 8 — audit integrity:")
    s, path = fresh_store()
    try:
        s.assert_fact("a", "k", "1", "2026-01-01")
        s.assert_fact("a", "k", "2", "2026-02-01")  # supersede
        s.assert_fact("a", "k", "3", "2026-02-01")  # equal-time conflict
        a = s.audit()
        check("audit ok (no duplicate current rows)", a["ok"] is True)
        check("exactly one current fact for a::k", a["current_facts"] == 1)
        check("one open conflict counted", a["open_conflicts"] == 1)
    finally:
        s.close(); os.path.exists(path) and os.unlink(path)


def main():
    print("=" * 60)
    print("fact_store_v2 adversarial test")
    print("=" * 60)
    scenario_1_anti_stale()
    scenario_2_f7_date_formats()
    scenario_3_f2_equal_time_conflict()
    scenario_4_explicit_correction()
    scenario_5_contestable_date()
    scenario_6_resolve_conflict()
    scenario_7_garbage_date_rejected()
    scenario_8_audit_integrity()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} CHECK(S) FAILED")
        for f in FAILURES:
            print(f"  - {f}")
        print("=" * 60)
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    print("=" * 60)
    sys.exit(0)


if __name__ == "__main__":
    main()

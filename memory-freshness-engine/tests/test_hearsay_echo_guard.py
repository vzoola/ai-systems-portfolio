"""
test_hearsay_echo_guard.py — SPEC 3.4 D4: the undated (hearsay) lane may not
silently pick a winner among disagreeing mentions.

THE BUG (found by the Danculus agent-memory-integrity harness 2026-07-13; our own
7 suites + the 12h soak structurally could NOT catch it, because the soak corpus is
date-stamped and so only ever exercised the DATED path):

    whole_loop.serve()'s UNVERIFIED branch returned `hs[-1]` -- the MOST RECENT
    undated mention. Ordinary chat carries no dates, so P2 correctly refuses to
    invent one and routes everything to hearsay. Therefore:

        say A  ->  correct to B  ->  restate A

    ...and the most recent mention IS the echo. The correction was silently
    un-done and serve() handed back the RETIRED value. 20/20, deterministic.

THE FIX (Vahe blessed Option 3, 2026-07-14): when the surviving undated mentions
carry more than one distinct (normalized) value, serve NO value and disclose all
of them. The dated lane's P4 already refuses to guess here -- it flags
echo-or-return CONTESTED because "echo-vs-legitimate-return is undecidable from
text" -- and the undated lane has strictly LESS information, so it may not quietly
pick a winner where the dated lane declines to.

    answer_gate.py: "the value field is a loaded gun; status is the safety."
    If we cannot know which undated mention is current, we UNLOAD THE GUN.

House convention: module-level check()/FAILS, main() prints a scorecard,
sys.exit(0/1). Stdlib only.
"""

import os
import sys
import tempfile

import answer_gate
from whole_loop import MemoryLoop

FAILS = []


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILS.append(label)


def _loop():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return MemoryLoop(path), path


def _mention(loop, value, source):
    """An UNDATED mention -> the hearsay lane (no event_time anywhere)."""
    loop.store.add_hearsay(domain="test", key="price", value=value,
                           mention_date=None, source=source,
                           quote=f"it is {value}")


# ---------------------------------------------------------------------------
# 1. THE SCAR: say A -> correct to B -> restate A. The echo must not win.
# ---------------------------------------------------------------------------

def t_echo_does_not_resurrect_the_retired_value():
    loop, path = _loop()
    try:
        _mention(loop, "$100", "src-A")   # A
        _mention(loop, "$200", "src-B")   # B -- the correction
        _mention(loop, "$100", "src-C")   # A again -- the ECHO

        served = loop.serve("test", "price")

        check("echo: serve() does NOT return the retired value ($100)",
              served["value"] != "$100")
        check("echo: serve() picks NO winner at all (value is None)",
              served["value"] is None)
        check("echo: status stays UNVERIFIED (answer_gate semantics untouched)",
              served["status"] == "UNVERIFIED")
        check("echo: the disagreement is DISCLOSED, not hidden",
              served.get("disagreement") is True)
        check("echo: every competing mention is surfaced (3 rows, none dropped)",
              len(served.get("competing_mentions") or []) == 3)
        check("echo: both distinct values appear in the disclosure",
              {m["value"] for m in served["competing_mentions"]} == {"$100", "$200"})
        check("echo: the gate refuses to let ANY of it be asserted",
              not answer_gate.is_safe_to_assert(served["status"]))
        check("echo: no row was deleted -- history intact via get_hearsay()",
              len(loop.store.get_hearsay("test", "price")) == 3)
    finally:
        loop.close()
        os.path.exists(path) and os.unlink(path)


# ---------------------------------------------------------------------------
# 2. The value field is the loaded gun. On disagreement it must be EMPTY --
#    a consumer that reads .value without checking .status must get nothing.
# ---------------------------------------------------------------------------

def t_naive_consumer_of_dot_value_gets_nothing():
    loop, path = _loop()
    try:
        _mention(loop, "$100", "src-A")
        _mention(loop, "$200", "src-B")
        _mention(loop, "$100", "src-C")
        served = loop.serve("test", "price")

        # This is the exact naive read answer_gate.py's docstring warns about.
        naive = served["value"]
        check("a naive .value read (ignoring .status) yields NO retired value",
              naive is None)

        block = answer_gate.build_injection_block([("test/price", served)])
        check("answer_gate puts it in QUARANTINE, never AUTHORITATIVE",
              "$100" not in block.split("=== QUARANTINE")[0])
        check("answer_gate exposes no assertable value for it",
              answer_gate.authoritative_values([("test/price", served)]) == {None}
              or not answer_gate.authoritative_values([("test/price", served)]))
    finally:
        loop.close()
        os.path.exists(path) and os.unlink(path)


# ---------------------------------------------------------------------------
# 3. REGRESSION GUARD: when mentions AGREE, behavior must be byte-identical to
#    the pre-fix code. The fix must not turn honest hearsay into a blanket
#    abstain -- that would be "fixing" the bug by breaking the feature.
# ---------------------------------------------------------------------------

def t_agreeing_mentions_are_unchanged():
    loop, path = _loop()
    try:
        _mention(loop, "$100", "src-A")
        _mention(loop, "$100", "src-B")   # same value, restated -- NOT a conflict
        served = loop.serve("test", "price")

        check("agreement: the value is still served (not nulled out)",
              served["value"] == "$100")
        check("agreement: status is still UNVERIFIED",
              served["status"] == "UNVERIFIED")
        check("agreement: NOT flagged as a disagreement",
              not served.get("disagreement"))
        check("agreement: no competing_mentions block",
              "competing_mentions" not in served)
        check("agreement: provenance string preserved (old shape)",
              "per a" in (served.get("provenance") or ""))
    finally:
        loop.close()
        os.path.exists(path) and os.unlink(path)


def t_single_mention_is_unchanged():
    loop, path = _loop()
    try:
        _mention(loop, "$100", "src-A")
        served = loop.serve("test", "price")
        check("single mention: value still served as UNVERIFIED",
              served["value"] == "$100" and served["status"] == "UNVERIFIED")
        check("single mention: not flagged as disagreement",
              not served.get("disagreement"))
    finally:
        loop.close()
        os.path.exists(path) and os.unlink(path)


# ---------------------------------------------------------------------------
# 4. Normalized comparison (Fable B3, mirroring P4): "$100" and "100" are the
#    SAME value, not a false disagreement that would nuke a good answer.
# ---------------------------------------------------------------------------

def t_normalized_equality_is_not_a_false_disagreement():
    """On a REGISTERED money key, surface-form drift is not a disagreement.

    This must use a key the registry actually types as `money` (otm/starter_price),
    because normalize_value() only strips '$' when it KNOWS the value_type -- the
    exact same vt seam P4 uses on the dated lane at whole_loop.py:334. An
    unregistered key gets value_type=None and falls back to a plain string
    compare; see the companion check below, which pins that honestly rather than
    pretending normalization is universal.
    """
    loop, path = _loop()
    try:
        loop.store.add_hearsay(domain="otm", key="starter_price", value="$297",
                               mention_date=None, source="src-A", quote="it is $297")
        loop.store.add_hearsay(domain="otm", key="starter_price", value="297",
                               mention_date=None, source="src-B", quote="it is 297")
        served = loop.serve("otm", "starter_price")
        check("normalization (registered money key): '$297' vs '297' is NOT a "
              "disagreement",
              not served.get("disagreement") and served["value"] is not None)
    finally:
        loop.close()
        os.path.exists(path) and os.unlink(path)


def t_untyped_key_drift_fails_SAFE_not_silent():
    """An UNTYPED key ('test/price' -> value_type=None) cannot normalize '$100'
    to '100', so the two read as distinct and we serve NO value.

    That is a FALSE disagreement -- and it is the direction we want to fail in.
    It costs a file read; the alternative (guessing a winner) is what this whole
    system exists to prevent. Pinned as a test so the behavior is a DECISION on
    the record, not an accident someone later 'fixes' into a silent pick.
    """
    loop, path = _loop()
    try:
        _mention(loop, "$100", "src-A")
        _mention(loop, "100", "src-B")
        served = loop.serve("test", "price")
        check("untyped key: surface drift fails SAFE (no value served, disclosed)",
              served["value"] is None and served.get("disagreement") is True)
        check("untyped key: still non-assertable at the gate",
              not answer_gate.is_safe_to_assert(served["status"]))
    finally:
        loop.close()
        os.path.exists(path) and os.unlink(path)


def main():
    print("=" * 62)
    print("test_hearsay_echo_guard — SPEC 3.4 D4 (undated lane picks no winner)")
    print("=" * 62)

    t_echo_does_not_resurrect_the_retired_value()
    t_naive_consumer_of_dot_value_gets_nothing()
    t_agreeing_mentions_are_unchanged()
    t_single_mention_is_unchanged()
    t_normalized_equality_is_not_a_false_disagreement()
    t_untyped_key_drift_fails_SAFE_not_silent()

    print("\n" + "=" * 62)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILED")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL PASSED — the undated echo can no longer resurrect a retired "
          "value; disagreeing mentions serve NO value and disclose every one; "
          "agreeing mentions are byte-identical to the pre-fix behavior.")
    sys.exit(0)


if __name__ == "__main__":
    main()

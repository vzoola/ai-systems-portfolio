"""
test_whole_loop.py — the REAL gate Grok demanded.

Runs prose-representing extractor outputs through the REAL ingest -> store ->
serve policy (whole_loop.py), NOT hand-called assert_fact. Metrics are the ones
Grok said actually matter — wrong-current-after-ingest and stale-current-served
— NOT "invented facts". Several scenarios are DESIGNED TO FAIL: they encode
holes we cannot close yet, and the harness REPORTS them as measured failures
instead of hiding them. A scenario marked EXPECT_HOLE failing "gracefully" (the
value is wrong but the system did not silently assert it as CURRENT) is scored
differently from a silent-poison failure.

Exit 0 only if: (a) every SAFETY invariant holds, and (b) the measured
silent-poison count equals the known/expected count (no NEW silent poison).
"""

import os
import sys
import tempfile

from whole_loop import MemoryLoop

RESULTS = []          # (scenario, label, ok, note)
SILENT_POISON = []    # cases where serve returned CURRENT with a wrong value


def rec(scenario, label, ok, note=""):
    RESULTS.append((scenario, label, ok, note))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f"  ({note})" if note else ""))


def fresh():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return MemoryLoop(path), path


def ext(value, event_time, kind="event", confident=True):
    return {"value": value, "event_time": event_time, "kind": kind,
            "confident": confident}


# ---------------------------------------------------------------------------
# 1. The $12.69 counter-case, extractor ABSTAINS (matches our eval evidence:
#    both real stale-remention cases -> qwen returned empty). Should be SAFE.
# ---------------------------------------------------------------------------
def s1_counter_abstain():
    print("\nS1 — $12.69 stale note, extractor ABSTAINS (our observed qwen behavior):")
    m, p = fresh()
    try:
        # history: was $12.69 (6/14), topped up to $30 (7/1, correct current)
        m.ingest("vapi", "credit_balance", ext("$12.69", "2026-06-14"), "2026-06-14")
        m.ingest("vapi", "credit_balance", ext("$30", "2026-07-01"), "2026-07-01")
        # 7/3 note re-mentions old value; extractor abstains (empty)
        st = m.ingest("vapi", "credit_balance", ext("", ""), "2026-07-03")
        rec("S1", "abstained on stale note", st == "abstained")
        served = m.serve("vapi", "credit_balance", as_of="2026-07-03")
        rec("S1", "serve still $30 CURRENT (no poison)",
            served["value"] == "$30" and served["status"] == "CURRENT")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


# ---------------------------------------------------------------------------
# 2. Counter-case, extractor CONFIDENTLY forward-dates the echo (Grok's poison).
#    History HAS $12.69 -> echo guard must fire -> CONTESTED, current held.
# ---------------------------------------------------------------------------
def s2_counter_echo_guard():
    print("\nS2 — $12.69 forward-dated echo, history known -> echo guard:")
    m, p = fresh()
    try:
        m.ingest("vapi", "credit_balance", ext("$12.69", "2026-06-14"), "2026-06-14")
        m.ingest("vapi", "credit_balance", ext("$30", "2026-07-01"), "2026-07-01")
        st = m.ingest("vapi", "credit_balance", ext("$12.69", "2026-07-03"), "2026-07-03")
        rec("S2", "forward-dated echo -> echo_contested", st == "echo_contested")
        served = m.serve("vapi", "credit_balance", as_of="2026-07-03")
        rec("S2", "serve holds $30 but flags CONTESTED (not silent)",
            served["value"] == "$30" and served["status"] == "CONTESTED")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


# ---------------------------------------------------------------------------
# 3. EXPECT_HOLE: same poison but NO prior history ($12.69 never recorded).
#    Echo guard cannot fire; a CONFIDENT forward-dated wrong value supersedes.
#    This is a REAL residual hole -> we measure it as SILENT POISON, honestly.
# ---------------------------------------------------------------------------
def s3_counter_no_history_HOLE():
    print("\nS3 — [EXPECT_HOLE] $12.69 echo but NO history to catch it:")
    m, p = fresh()
    try:
        m.ingest("vapi", "credit_balance", ext("$30", "2026-07-01"), "2026-07-01")
        st = m.ingest("vapi", "credit_balance", ext("$12.69", "2026-07-03"), "2026-07-03")
        served = m.serve("vapi", "credit_balance", as_of="2026-07-03")
        poisoned = served["value"] == "$12.69" and served["status"] == "CURRENT"
        if poisoned:
            SILENT_POISON.append("S3")
        # This scenario is EXPECTED to poison today -> record it, do not PASS it.
        rec("S3", "KNOWN HOLE: confident forward echo w/o history poisons",
            poisoned, "expected silent-poison (residual hole, needs corroboration signal)")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


# ---------------------------------------------------------------------------
# 4. kind mislabel must NOT change the stored event_time (Grok A2 / demand #4).
# ---------------------------------------------------------------------------
def s4_kind_not_used_for_date():
    print("\nS4 — kind flip does NOT move the timestamp:")
    m1, p1 = fresh()
    m2, p2 = fresh()
    try:
        m1.ingest("otm", "price", ext("$297", "2026-06-23", kind="event"), "2026-07-03")
        m2.ingest("otm", "price", ext("$297", "2026-06-23", kind="mention"), "2026-07-03")
        et1 = m1.serve("otm", "price")["event_time"]
        et2 = m2.serve("otm", "price")["event_time"]
        rec("S4", "same stated date -> same stored event_time regardless of kind",
            et1 == et2 and et1 is not None, f"{et1} == {et2}")
    finally:
        m1.close(); m2.close()
        os.path.exists(p1) and os.unlink(p1); os.path.exists(p2) and os.unlink(p2)


# ---------------------------------------------------------------------------
# 5. Legitimate return-to-old-value -> CONTESTED (safe, not silently applied).
# ---------------------------------------------------------------------------
def s5_legit_return_contested():
    print("\nS5 — legitimate return to an old value -> CONTESTED (asks, not guesses):")
    m, p = fresh()
    try:
        m.ingest("vapi", "credit_balance", ext("$12.69", "2026-06-14"), "2026-06-14")
        m.ingest("vapi", "credit_balance", ext("$30", "2026-07-01"), "2026-07-01")
        # genuinely spent back down to $12.69 on 7/5 — indistinguishable from an echo
        st = m.ingest("vapi", "credit_balance", ext("$12.69", "2026-07-05"), "2026-07-05")
        served = m.serve("vapi", "credit_balance", as_of="2026-07-05")
        rec("S5", "ambiguous return flagged CONTESTED, not silently wrong",
            st == "echo_contested" and served["status"] == "CONTESTED",
            "safe-but-imperfect: needs human resolve")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


# ---------------------------------------------------------------------------
# 6. EXPECT_HOLE: multi-fact paragraph key-bleed (extractor mis-binds B into A).
#    We can't stop a mis-bind here; we measure it as silent poison.
# ---------------------------------------------------------------------------
def s6_key_bleed_HOLE():
    print("\nS6 — [EXPECT_HOLE] multi-fact paragraph: extractor binds wrong value to key:")
    m, p = fresh()
    try:
        # asked for otm/language_support; extractor confidently returns a sister
        # product's value ("4 languages") — the blind-run contamination.
        st = m.ingest("otm", "language_support",
                      ext("4 languages", "2026-06-19", confident=True), "2026-06-19")
        served = m.serve("otm", "language_support")
        poisoned = served["value"] == "4 languages" and served["status"] == "CURRENT"
        if poisoned:
            SILENT_POISON.append("S6")
        rec("S6", "KNOWN HOLE: confident mis-bind poisons on first insert",
            poisoned, "expected silent-poison (needs cross-product guard / quote-grounding)")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


# ---------------------------------------------------------------------------
# 7. Empty date must NEVER default to doc_date (P2 / F1 landmine).
# ---------------------------------------------------------------------------
def s7_no_date_not_docdate():
    print("\nS7 — empty extracted date must NOT become the doc date silently:")
    m, p = fresh()
    try:
        st = m.ingest("infra", "thing", ext("v1", ""), "2026-07-03")
        served = m.serve("infra", "thing")
        rec("S7", "no-date fact -> HEARSAY, served UNVERIFIED not CURRENT",
            st == "hearsay" and served["status"] == "UNVERIFIED")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


# ---------------------------------------------------------------------------
# B1 — Fable's P2 kill-chain, now defused by the hearsay lane.
# ---------------------------------------------------------------------------
def sB1_hearsay_killchain():
    print("\nB1 — undated $12.69 kill-chain defused (hearsay lane, no fabricated date):")
    m, p = fresh()
    try:
        st1 = m.ingest("vapi", "credit_balance", ext("$12.69", ""), "2026-07-03")
        rec("B1", "undated mention -> hearsay (never enters facts)", st1 == "hearsay")
        pre = m.serve("vapi", "credit_balance")
        rec("B1", "before truth: served UNVERIFIED w/ provenance, not CURRENT",
            pre["status"] == "UNVERIFIED" and pre["value"] == "$12.69")
        m.ingest("vapi", "credit_balance", ext("$30", "2026-07-01"), "2026-07-01")
        post = m.serve("vapi", "credit_balance", as_of="2026-07-03")
        rec("B1", "true $30 wins (no fabricated 7/3 anchor to lose to)",
            post["value"] == "$30" and post["status"] == "CURRENT")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


# ---------------------------------------------------------------------------
# B2 — far-future date cannot silently lock a key.
# ---------------------------------------------------------------------------
def sB2_future_lockin():
    print("\nB2 — far-future date cannot lock a key:")
    m, p = fresh()
    try:
        st = m.ingest("otm", "price", ext("$199", "2062-01-01"), "2026-07-03")
        rec("B2", "future-dated typo -> future_contested (not current)",
            st == "future_contested")
        served = m.serve("otm", "price")
        rec("B2", "2062 typo never silently CURRENT",
            not (served["status"] == "CURRENT" and served["value"] == "$199"))
        m.ingest("otm", "price", ext("$347", "2026-06-20"), "2026-06-20")
        s2 = m.serve("otm", "price")
        rec("B2", "real (past-dated) change is the served value (2062 never wins)",
            s2["value"] == "$347")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


# ---------------------------------------------------------------------------
# B3 — echo guard normalizes: "12.69" cannot slip past "$12.69".
# ---------------------------------------------------------------------------
def sB3_echo_normalization():
    print("\nB3 — echo guard normalizes by value type (format drift caught):")
    m, p = fresh()
    try:
        m.ingest("vapi", "credit_balance", ext("$12.69", "2026-06-14"), "2026-06-14")
        m.ingest("vapi", "credit_balance", ext("$30", "2026-07-01"), "2026-07-01")
        st = m.ingest("vapi", "credit_balance", ext("12.69", "2026-07-03"), "2026-07-03")
        rec("B3", "format-drift echo ('12.69' no $) caught -> echo_contested",
            st == "echo_contested")
        served = m.serve("vapi", "credit_balance", as_of="2026-07-03")
        rec("B3", "current holds $30, not superseded by '12.69'",
            served["value"] == "$30" and served["status"] == "CONTESTED")
        st2 = m.ingest("vapi", "credit_balance", ext("30.00", "2026-07-02"), "2026-07-02")
        rec("B3", "'30.00' == '$30' -> noop/refreshed, no spurious supersession",
            st2 in ("noop", "refreshed"))
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


# ---------------------------------------------------------------------------
# 8. Perishable key aged past horizon -> serve flags STALE (abstention contract).
# ---------------------------------------------------------------------------
def s8_stale_by_age():
    print("\nS8 — aged perishable key served STALE, not silently fresh:")
    m, p = fresh()
    try:
        m.ingest("vapi", "credit_balance", ext("$30", "2026-06-01"), "2026-06-01")
        fresh_serve = m.serve("vapi", "credit_balance", as_of="2026-06-05")
        stale_serve = m.serve("vapi", "credit_balance", as_of="2026-07-04")  # >14d
        rec("S8", "within horizon -> CURRENT", fresh_serve["status"] == "CURRENT")
        rec("S8", "past horizon -> STALE (disclosed, value still shown)",
            stale_serve["status"] == "STALE" and stale_serve["value"] == "$30")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


# ---------------------------------------------------------------------------
# 9. UNKNOWN key -> serve says UNKNOWN (never a silent empty that invites a guess).
# ---------------------------------------------------------------------------
def s9_unknown():
    print("\nS9 — never-seen key served UNKNOWN:")
    m, p = fresh()
    try:
        served = m.serve("otm", "never_seen")
        rec("S9", "unknown key -> UNKNOWN", served["status"] == "UNKNOWN")
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)


EXPECTED_SILENT_POISON = {"S3", "S6"}   # the two known residual holes, on purpose


def main():
    print("=" * 64)
    print("test_whole_loop — ingest->store->serve, Grok-metrics, holes measured")
    print("=" * 64)
    s1_counter_abstain()
    s2_counter_echo_guard()
    s3_counter_no_history_HOLE()
    s4_kind_not_used_for_date()
    s5_legit_return_contested()
    s6_key_bleed_HOLE()
    s7_no_date_not_docdate()
    s8_stale_by_age()
    s9_unknown()
    sB1_hearsay_killchain()
    sB2_future_lockin()
    sB3_echo_normalization()

    # Safety invariants = every non-HOLE check must pass.
    hole_labels = {"S3", "S6"}
    safety_fails = [r for r in RESULTS
                    if not r[2] and r[0] not in hole_labels]

    print("\n" + "=" * 64)
    print("SCORECARD (Grok metrics)")
    print("=" * 64)
    print(f"  safety-invariant checks failed:   {len(safety_fails)}")
    print(f"  silent-poison cases (serve=CURRENT, wrong value): "
          f"{sorted(SILENT_POISON)}")
    print(f"  expected (known) silent-poison:   {sorted(EXPECTED_SILENT_POISON)}")
    new_poison = set(SILENT_POISON) - EXPECTED_SILENT_POISON
    missing_poison = EXPECTED_SILENT_POISON - set(SILENT_POISON)
    print(f"  NEW/unexpected silent-poison:     {sorted(new_poison)}")
    print(f"  holes now closed (expected but didn't poison): {sorted(missing_poison)}")
    print("=" * 64)

    ok = (len(safety_fails) == 0 and not new_poison)
    if ok:
        print("RESULT: SAFETY INVARIANTS HOLD; only the 2 known holes poison.")
        print("  -> honest state: safe-failing loop with 2 documented residual")
        print("     holes (S3 no-history echo, S6 key-bleed) that need a")
        print("     corroboration/quote-grounding signal to close.")
        if missing_poison:
            print(f"  NOTE: {sorted(missing_poison)} unexpectedly did NOT poison — "
                  "re-check whether a hole was actually closed.")
        sys.exit(0)
    else:
        print("RESULT: FAIL")
        for s, label, okk, note in safety_fails:
            print(f"  SAFETY FAIL [{s}] {label}")
        if new_poison:
            print(f"  NEW SILENT POISON: {sorted(new_poison)} — a regression.")
        sys.exit(1)


if __name__ == "__main__":
    main()

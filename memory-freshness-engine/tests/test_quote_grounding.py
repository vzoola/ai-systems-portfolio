"""
test_quote_grounding.py — Phase ① G1-G4 quote-grounding proof (SPEC_SOAK_AND_
HARDENING.md Part 1.1-1.2, SKILL §3 Phase ①).

Covers each of the four checks' FIRE and NON-FIRE case, the canonicalization
rules (NFC, whitespace collapse, curly-quote straightening, G1 case-
sensitivity), the NO_QUOTE discard, the registry seam (get_key_meta), and an
end-to-end whole_loop.ingest() wiring proof (quote persists, is served back,
and legacy no-source_doc callers are byte-for-byte unaffected).

House convention: module-level check()/FAILS, main() prints a scorecard,
sys.exit(0/1). Stdlib only.
"""

import os
import sys
import tempfile

from quote_grounding import (
    ground_extraction, get_key_meta, canonical_text, canonical_ci,
    NO_QUOTE, REJECT_QUOTE_NOT_FOUND, REJECT_VALUE_NOT_IN_QUOTE,
    ROUTE_CANDIDATES, DOWNGRADE_DATE_UNGROUNDED, PASS as GROUNDING_PASS,
)
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


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

DOC_WITH_DATE = ("Unlike Patient Catch, OTM's shared Vapi account currently "
                  "holds a credit balance of $30. This was topped up on July 1.")
DOC_NO_DATE = ("Unlike Patient Catch, OTM's shared Vapi account currently "
                "holds a credit balance of $30. Nothing else changed recently.")
QUOTE_OK = "credit balance of $30"

META_ANCHOR_VAPI = {"value_type": "money", "anchor_terms": ["vapi"],
                     "exclusion_terms": []}
META_NO_ANCHOR = {"value_type": "money", "anchor_terms": ["zzz_nonexistent"],
                   "exclusion_terms": []}
META_ANCHOR_VAPI_EXCL = {"value_type": "money", "anchor_terms": ["vapi"],
                          "exclusion_terms": ["patient catch"]}


def code_of(**kw):
    return ground_extraction(**kw)["code"]


# ---------------------------------------------------------------------------
# Canonicalization rules
# ---------------------------------------------------------------------------

def t_canonicalization():
    print("\n-- canonical_text: NFC / whitespace / curly-quotes / case --")
    # NFC: precomposed vs combining-character forms of the same visible text
    # must canonicalize identically.
    precomposed = "café"
    decomposed = "café"  # e + combining acute accent
    check("NFC normalizes decomposed == precomposed",
          canonical_text(precomposed) == canonical_text(decomposed))

    # Whitespace runs (spaces, tabs, newlines) collapse to a single space,
    # and leading/trailing whitespace is stripped.
    check("whitespace runs collapse to single space",
          canonical_text("  a   b\n\tc  ") == "a b c")

    # Curly/smart quotes and apostrophes straighten on both sides.
    check("curly double quotes -> straight",
          canonical_text("“Hello”") == '"Hello"')
    check("curly apostrophe -> straight",
          canonical_text("it’s") == "it's")

    # G1 is case-SENSITIVE: canonical_text itself preserves case (it does
    # NOT lowercase); only canonical_ci (used by G2-G4) lowercases.
    check("canonical_text preserves case (G1 needs this)",
          canonical_text("Hello World") == "Hello World")
    check("canonical_ci lowercases (G2-G4 need this)",
          canonical_ci("Hello World") == "hello world")


# ---------------------------------------------------------------------------
# NO_QUOTE — discarded before G1, before policy
# ---------------------------------------------------------------------------

def t_no_quote():
    print("\n-- NO_QUOTE (discarded before G1) --")
    check("empty string quote -> no_quote",
          code_of(value="$30", quote="", source_doc=DOC_WITH_DATE,
                  value_type="money", key_meta=META_ANCHOR_VAPI) == NO_QUOTE)
    check("None quote -> no_quote",
          code_of(value="$30", quote=None, source_doc=DOC_WITH_DATE,
                  value_type="money", key_meta=META_ANCHOR_VAPI) == NO_QUOTE)
    check("whitespace-only quote -> no_quote",
          code_of(value="$30", quote="   \n  ", source_doc=DOC_WITH_DATE,
                  value_type="money", key_meta=META_ANCHOR_VAPI) == NO_QUOTE)


# ---------------------------------------------------------------------------
# G1 SUBSTRING — case-sensitive canonical substring match
# ---------------------------------------------------------------------------

def t_g1():
    print("\n-- G1 SUBSTRING --")
    # FIRE: quote text is simply not in the source at all (fabrication).
    check("G1 FIRE: quote not in source -> REJECT_QUOTE_NOT_FOUND",
          code_of(value="$30", quote="credit balance of $99",
                  source_doc=DOC_WITH_DATE, value_type="money",
                  key_meta=META_ANCHOR_VAPI) == REJECT_QUOTE_NOT_FOUND)
    # FIRE: G1 is CASE-SENSITIVE — a case-mismatched quote must reject even
    # though it matches case-insensitively (the stronger tripwire the spec
    # calls out explicitly).
    check("G1 FIRE (case-sensitivity): wrong case -> REJECT_QUOTE_NOT_FOUND",
          code_of(value="$30", quote="Credit Balance of $30",
                  source_doc=DOC_WITH_DATE, value_type="money",
                  key_meta=META_ANCHOR_VAPI) == REJECT_QUOTE_NOT_FOUND)
    # NON-FIRE: exact-case quote that is literally in the source passes G1
    # (and, with a good anchor + no date requirement, the whole pipeline).
    check("G1 NON-FIRE: exact-case quote found -> proceeds past G1",
          code_of(value="$30", quote=QUOTE_OK, source_doc=DOC_WITH_DATE,
                  value_type="money", key_meta=META_ANCHOR_VAPI) != REJECT_QUOTE_NOT_FOUND)
    # NON-FIRE + canonicalization: curly-quoted source / straight-quoted
    # extractor quote (or vice versa) must still match after canonicalization.
    curly_doc = "The plan’s credit balance of $30 is current."
    check("G1 NON-FIRE: curly source vs straight quote matches via canonical form",
          code_of(value="$30", quote="plan's credit balance of $30",
                  source_doc=curly_doc, value_type="money",
                  key_meta=META_ANCHOR_VAPI) != REJECT_QUOTE_NOT_FOUND)


# ---------------------------------------------------------------------------
# G2 VALUE-IN-QUOTE — value normalized per value_type must appear in the quote
# ---------------------------------------------------------------------------

def t_g2():
    print("\n-- G2 VALUE-IN-QUOTE --")
    # FIRE: the extractor bound a value that isn't the number in its own quote
    # (read-vs-bind drift).
    check("G2 FIRE: value not in quote -> REJECT_VALUE_NOT_IN_QUOTE",
          code_of(value="$45", quote=QUOTE_OK, source_doc=DOC_WITH_DATE,
                  value_type="money", key_meta=META_ANCHOR_VAPI) == REJECT_VALUE_NOT_IN_QUOTE)
    # NON-FIRE: the value's number is present in the quote (format drift
    # tolerated: "$30" value against a quote containing "$30").
    check("G2 NON-FIRE: value present in quote -> proceeds past G2",
          code_of(value="$30", quote=QUOTE_OK, source_doc=DOC_WITH_DATE,
                  value_type="money", key_meta=META_ANCHOR_VAPI) != REJECT_VALUE_NOT_IN_QUOTE)
    # NON-FIRE + format drift: normalized comparison (money) tolerates
    # "$30.00" value against a quote spelled "$30".
    check("G2 NON-FIRE: money format drift ($30.00 vs $30) still matches",
          code_of(value="$30.00", quote=QUOTE_OK, source_doc=DOC_WITH_DATE,
                  value_type="money", key_meta=META_ANCHOR_VAPI) != REJECT_VALUE_NOT_IN_QUOTE)
    # enum/string value_type: case-insensitive containment.
    enum_doc = "OTM currently supports FOUR languages for its voice line."
    check("G2 NON-FIRE: enum/string case-insensitive containment",
          code_of(value="four languages", quote="supports FOUR languages",
                  source_doc=enum_doc, value_type="string",
                  key_meta={"value_type": "string", "anchor_terms": ["otm"],
                            "exclusion_terms": []}) != REJECT_VALUE_NOT_IN_QUOTE)
    check("G2 FIRE: enum/string value absent from quote",
          code_of(value="two languages", quote="supports FOUR languages",
                  source_doc=enum_doc, value_type="string",
                  key_meta={"value_type": "string", "anchor_terms": ["otm"],
                            "exclusion_terms": []}) == REJECT_VALUE_NOT_IN_QUOTE)
    # REGRESSION (2026-07-14): a money value carrying a period/unit suffix
    # ("$497/mo", verbatim from a quote reading "base $497/mo") must NOT be
    # rejected -- the $497 amount IS grounded in the quote. Before the
    # symmetric-tokenize fix the value normalized to the STRING form "497/mo"
    # and never matched the quote's bare "$497" -> "497.00" token, so the SKU#2
    # capture-at-birth entry was wrongly rejected in the lane-new run.
    rate_quote = ("otm ai marketing department pricing = base $497/mo, sold "
                  "both standalone and bundled with the ai front desk (base "
                  "$297/mo), with a bundle discount.")
    for v in ("$497/mo", "$497/month", "$497"):
        check(f"G2 NON-FIRE: period-suffixed money value {v!r} grounds against its quote",
              code_of(value=v, quote=rate_quote, source_doc=rate_quote,
                      value_type="money", key_meta=META_ANCHOR_VAPI)
              != REJECT_VALUE_NOT_IN_QUOTE)
    # And it must STILL fire when the amount genuinely differs (guard not gutted).
    check("G2 FIRE: period-suffixed money value with a WRONG amount still rejects",
          code_of(value="$450/mo", quote=rate_quote, source_doc=rate_quote,
                  value_type="money", key_meta=META_ANCHOR_VAPI)
          == REJECT_VALUE_NOT_IN_QUOTE)


# ---------------------------------------------------------------------------
# G3 KEY-ANCHOR — NEVER a hard reject, only PASS or ROUTE_CANDIDATES
# ---------------------------------------------------------------------------

def t_g3():
    print("\n-- G3 KEY-ANCHOR (route-only, never a hard reject) --")
    # FIRE: no registry anchor term appears anywhere in the window.
    r = ground_extraction(value="$30", quote=QUOTE_OK, source_doc=DOC_WITH_DATE,
                          value_type="money", key_meta=META_NO_ANCHOR)
    check("G3 FIRE: no anchor in window -> ROUTE_CANDIDATES",
          r["code"] == ROUTE_CANDIDATES)
    check("G3 FIRE reason recorded as no_anchor_in_window",
          r["reason"] == "no_anchor_in_window")

    # FIRE: anchor present, but an exclusion term for a DIFFERENT referent
    # also sits in the window (comparative sentence) -> route, not reject.
    r2 = ground_extraction(value="$30", quote=QUOTE_OK, source_doc=DOC_WITH_DATE,
                           value_type="money", key_meta=META_ANCHOR_VAPI_EXCL)
    check("G3 FIRE: exclusion term in window -> ROUTE_CANDIDATES",
          r2["code"] == ROUTE_CANDIDATES)
    check("G3 FIRE reason is an exclusion sub-reason, never a reject code",
          r2["reason"] in ("exclusion_in_window", "exclusion_in_quote"))

    # NON-FIRE: anchor present, no exclusion anywhere in window -> passes G3
    # (continues to G4 / overall PASS since no event_marker was supplied).
    check("G3 NON-FIRE: anchor present, no exclusion -> proceeds past G3",
          code_of(value="$30", quote=QUOTE_OK, source_doc=DOC_WITH_DATE,
                  value_type="money", key_meta=META_ANCHOR_VAPI) == GROUNDING_PASS)

    # Fence check: G3 code is NEVER anything other than PASS or
    # ROUTE_CANDIDATES — it must never produce a REJECT_* code.
    for meta in (META_NO_ANCHOR, META_ANCHOR_VAPI_EXCL, META_ANCHOR_VAPI):
        c = code_of(value="$30", quote=QUOTE_OK, source_doc=DOC_WITH_DATE,
                    value_type="money", key_meta=meta)
        check(f"G3 fence: code for anchors={meta['anchor_terms']} is never a hard reject",
              c not in (REJECT_QUOTE_NOT_FOUND, REJECT_VALUE_NOT_IN_QUOTE))


# ---------------------------------------------------------------------------
# G4 DATE-IN-WINDOW — strip-and-downgrade, never a reject
# ---------------------------------------------------------------------------

def t_g4():
    print("\n-- G4 DATE-IN-WINDOW --")
    # FIRE: explicit_date marker, but no surface variant of the date appears
    # anywhere in the +/-1-sentence window -> downgrade (strip date, keep
    # value, caller routes to hearsay).
    r = ground_extraction(value="$30", quote=QUOTE_OK, source_doc=DOC_NO_DATE,
                          value_type="money", key_meta=META_ANCHOR_VAPI,
                          event_marker="explicit_date", event_time="2026-07-01")
    check("G4 FIRE: no date variant in window -> DOWNGRADE_DATE_UNGROUNDED",
          r["code"] == DOWNGRADE_DATE_UNGROUNDED)

    # NON-FIRE: a surface variant ("July 1") is present in the window
    # (adjacent sentence) -> full PASS.
    r2 = ground_extraction(value="$30", quote=QUOTE_OK, source_doc=DOC_WITH_DATE,
                           value_type="money", key_meta=META_ANCHOR_VAPI,
                           event_marker="explicit_date", event_time="2026-07-01")
    check("G4 NON-FIRE: 'July 1' surface variant present -> PASS",
          r2["code"] == GROUNDING_PASS)

    # Surface-variant coverage: numeric forms also count.
    numeric_doc = "Vapi credit balance of $30 was confirmed. Logged on 7/1."
    r3 = ground_extraction(value="$30", quote="Vapi credit balance of $30",
                           source_doc=numeric_doc, value_type="money",
                           key_meta=META_ANCHOR_VAPI, event_marker="explicit_date",
                           event_time="2026-07-01")
    check("G4 NON-FIRE: numeric '7/1' surface variant also satisfies the window",
          r3["code"] == GROUNDING_PASS)

    # No event_marker at all -> G4 doesn't apply; a doc with no date still
    # reaches full PASS (G4 only fires for explicit_date markers).
    check("G4 not applicable (no event_marker) -> PASS regardless of date presence",
          code_of(value="$30", quote=QUOTE_OK, source_doc=DOC_NO_DATE,
                  value_type="money", key_meta=META_ANCHOR_VAPI) == GROUNDING_PASS)


# ---------------------------------------------------------------------------
# S6 mis-bind tripwires (2026-07-08) — reproduce real 7/8 corpus POISONs and
# prove the tightened rules now REJECT/ROUTE them instead of asserting, while
# the legit near-neighbors they resemble still PASS. Fixtures are the actual
# offending corpus sentences (trimmed). See soak_report_2026-07-08.json.grading.
# ---------------------------------------------------------------------------

def t_s6_misbind_tripwires():
    print("\n-- S6 mis-bind tripwires (7/8 corpus POISONs) --")

    # POISON #1 al/members_count: "17 upcoming" (an EVENTS count) bound to
    # members_count because the domain-literal anchor "al" matched INSIDE
    # "total" in the adjacent sentence. Letter-boundary matching kills it.
    src1 = ("Session LLM test spend: $0.0053 total. events supercharge "
            "complete: /events now shows 17 upcoming this morning.")
    meta1 = {"value_type": "number",
             "anchor_terms": ["al", "members count", "signed up"],
             "exclusion_terms": []}
    r1 = ground_extraction(value="17 upcoming", quote="/events now shows 17 upcoming",
                           source_doc=src1, value_type="number", key_meta=meta1)
    check("TRIPWIRE #1: 'al' inside 'total' no longer anchors -> ROUTE_CANDIDATES",
          r1["code"] == ROUTE_CANDIDATES)

    # POISON #4 tooling/local_brain_model: "LOCAL BRAIN v1" (a build name) bound
    # to the model key because anchor "llama" matched INSIDE "Ollama".
    src4 = ("2026-07-03 LOCAL BRAIN v1 BUILT -> Projects/LOCAL_BRAIN. It does "
            "keyword retrieval plus Ollama chat api and self-writing notes.")
    meta4 = {"value_type": "string",
             "anchor_terms": ["tooling", "local brain model", "llama"],
             "exclusion_terms": []}
    r4 = ground_extraction(value="LOCAL BRAIN v1", quote="LOCAL BRAIN v1 BUILT",
                           source_doc=src4, value_type="string", key_meta=meta4)
    check("TRIPWIRE #4: 'llama' inside 'Ollama' no longer anchors -> ROUTE_CANDIDATES",
          r4["code"] == ROUTE_CANDIDATES)

    # POISON #3 tooling/fable_access (value_type=date): a NON-date value
    # "~20% used" satisfied G2's old raw-containment fallback on a date key.
    # The date-shape guard now rejects it at G2.
    src3 = "Note: Fable not maxed - ~20% used (NOW.md's 91-98% is stale)."
    meta3 = {"value_type": "date",
             "anchor_terms": ["tooling", "fable", "access", "billing"],
             "exclusion_terms": []}
    r3 = ground_extraction(value="~20% used", quote="Fable not maxed - ~20% used",
                           source_doc=src3, value_type="date", key_meta=meta3)
    check("TRIPWIRE #3: non-date value on a date-typed key -> REJECT_VALUE_NOT_IN_QUOTE",
          r3["code"] == REJECT_VALUE_NOT_IN_QUOTE)

    # --- NON-REGRESSION GUARDS: the legit facts these poisons resemble ---

    # A real prose-date value on the SAME date-typed key ("free until Jul 7")
    # legitimately fails strict parse but STILL carries a date token, so it must
    # clear G2 (it may route on G3, but must NOT be rejected at G2).
    src_ok3 = "Fable billing: free until Jul 7, then pay-as-you-go afterward."
    r_ok3 = ground_extraction(value="free until Jul 7",
                              quote="free until Jul 7, then pay-as-you-go",
                              source_doc=src_ok3, value_type="date", key_meta=meta3)
    check("GUARD: prose-date value 'free until Jul 7' clears G2 (not a value reject)",
          r_ok3["code"] != REJECT_VALUE_NOT_IN_QUOTE)

    # Letter-boundary must still allow anchors glued to DIGITS/PUNCT, not just
    # spaces — else it would break legit model-name facts. "qwen" must match
    # inside "qwen2.5:3b" (digit boundary), "ram" inside "mac-ram" (hyphen).
    src_ok_q = "The chosen local extractor is qwen2.5:3b for feature extraction."
    meta_q = {"value_type": "string", "anchor_terms": ["qwen"], "exclusion_terms": []}
    r_okq = ground_extraction(value="qwen2.5:3b", quote="local extractor is qwen2.5:3b",
                              source_doc=src_ok_q, value_type="string", key_meta=meta_q)
    check("GUARD: 'qwen' still anchors inside 'qwen2.5:3b' (digit boundary) -> PASS",
          r_okq["code"] == GROUNDING_PASS)
    src_ok_r = "Vahe ordered more mac-ram; the machine now has 40GB installed."
    meta_r = {"value_type": "string", "anchor_terms": ["ram"], "exclusion_terms": []}
    r_okr = ground_extraction(value="40GB", quote="machine now has 40GB installed",
                              source_doc=src_ok_r, value_type="string", key_meta=meta_r)
    check("GUARD: 'ram' still anchors inside 'mac-ram' (hyphen boundary) -> PASS",
          r_okr["code"] == GROUNDING_PASS)


# ---------------------------------------------------------------------------
# Registry seam
# ---------------------------------------------------------------------------

def t_registry_seam():
    print("\n-- registry seam: get_key_meta(domain, key) --")
    unknown = get_key_meta("nonexistent_domain", "nonexistent_key")
    check("unknown key returns a valid empty meta (no crash)",
          unknown == {"value_type": None, "anchor_terms": [], "exclusion_terms": []})
    legacy = get_key_meta("vapi", "credit_balance")
    check("legacy key vapi/credit_balance has value_type=money",
          legacy["value_type"] == "money")
    check("legacy key vapi/credit_balance has non-empty anchor_terms",
          len(legacy["anchor_terms"]) > 0)
    legacy2 = get_key_meta("otm", "price")
    check("legacy key otm/price has value_type=money",
          legacy2["value_type"] == "money")


# ---------------------------------------------------------------------------
# Integration: whole_loop.ingest() wiring — quote persists + is served back;
# legacy (no source_doc) callers are completely unaffected.
# ---------------------------------------------------------------------------

# Test clock pinned (as_of) for the perishable key: credit_balance has a 14-day
# freshness horizon, so against the wall clock a July balance is correctly STALE.
def t_ingest_integration():
    print("\n-- whole_loop.ingest() integration --")
    m, p = fresh()
    try:
        # PASS path: source_doc supplied, quote grounds cleanly -> fact
        # written, quote persists, serve() exposes it.
        st = m.ingest("vapi", "credit_balance",
                      {"value": "$30", "event_time": "2026-07-01",
                       "quote": QUOTE_OK, "event_marker": "explicit_date"},
                      "2026-07-01", source_doc=DOC_WITH_DATE)
        check("PASS path writes the fact (status='inserted')", st == "inserted")
        served = m.serve("vapi", "credit_balance", as_of="2026-07-05")
        check("served value is CURRENT $30", served["status"] == "CURRENT"
              and served["value"] == "$30")
        check("served row carries the persisted quote (read-time confession)",
              served["quote"] == QUOTE_OK)

        # REJECT_QUOTE_NOT_FOUND path: nothing written, key stays UNKNOWN.
        st2 = m.ingest("otm", "price",
                       {"value": "$297", "event_time": "2026-07-01",
                        "quote": "totally fabricated text"},
                       "2026-07-01", source_doc=DOC_WITH_DATE)
        check("G1 reject returns the exact code",
              st2 == REJECT_QUOTE_NOT_FOUND)
        check("G1 reject wrote nothing (still UNKNOWN)",
              m.serve("otm", "price")["status"] == "UNKNOWN")

        # ROUTE_CANDIDATES path: nothing written (never a hard reject).
        st3 = m.ingest("infra", "some_key",
                       {"value": "$30", "event_time": "2026-07-01",
                        "quote": QUOTE_OK},
                       "2026-07-01", source_doc=DOC_WITH_DATE)
        check("G3 route-to-candidates returns the exact code",
              st3 == ROUTE_CANDIDATES)
        check("G3 route wrote nothing (still UNKNOWN)",
              m.serve("infra", "some_key")["status"] == "UNKNOWN")

        # G4 downgrade path: value lands in hearsay (UNVERIFIED), date is
        # NOT what anchors it there.
        st4 = m.ingest("vapi", "credit_balance",
                       {"value": "$30", "event_time": "2026-07-01",
                        "quote": QUOTE_OK, "event_marker": "explicit_date"},
                       "2026-07-01", source_doc=DOC_NO_DATE, source="note-2")
        check("G4 downgrade returns the exact code",
              st4 == DOWNGRADE_DATE_UNGROUNDED)
        check("G4 downgrade lands in hearsay, not facts (existing $30 CURRENT untouched)",
              m.serve("vapi", "credit_balance", as_of="2026-07-05")["status"] == "CURRENT")

        # Legacy backward-compat: NO source_doc supplied (as all 6 pre-flight
        # suites call it) -> quote-grounding is skipped entirely; behavior
        # identical to pre-Phase① whole_loop.py.
        m2, p2 = fresh()
        try:
            legacy_status = m2.ingest("vapi", "credit_balance",
                                      {"value": "$30", "event_time": "2026-07-01",
                                       "kind": "event", "confident": True},
                                      "2026-07-01")
            check("legacy call (no source_doc, no quote) still writes normally",
                  legacy_status == "inserted")
            legacy_served = m2.serve("vapi", "credit_balance", as_of="2026-07-05")
            check("legacy served fact is CURRENT with quote=None",
                  legacy_served["status"] == "CURRENT" and legacy_served["quote"] is None)
        finally:
            m2.close()
            os.path.exists(p2) and os.unlink(p2)
    finally:
        m.close()
        os.path.exists(p) and os.unlink(p)


def main():
    print("=" * 60)
    print("test_quote_grounding — Phase ① G1-G4 quote-grounding")
    print("=" * 60)

    t_canonicalization()
    t_no_quote()
    t_g1()
    t_g2()
    t_g3()
    t_g4()
    t_s6_misbind_tripwires()
    t_registry_seam()
    t_ingest_integration()

    print("\n" + "=" * 60)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILED")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print(f"RESULT: ALL PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()

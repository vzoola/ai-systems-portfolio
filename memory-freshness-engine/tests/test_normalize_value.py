"""
test_normalize_value.py — property/pairs table for normalize_value.

Every regression across three review rounds (R3 → RR9 → RR11) lived inside this
one function or its callers. Per Claude Code's recommendation, this is its own
exhaustive must-equal / must-differ table, per value_type. Run it before ANYONE
touches normalize_value again — it is the tripwire for the recurring hole.
"""

import sys
from fact_store_v2 import normalize_value as n

FAILS = []


def eq(a, b, vt, why=""):
    if n(a, vt) != n(b, vt):
        FAILS.append(f"MUST-EQUAL failed [{vt}] {a!r} vs {b!r}  "
                     f"({n(a, vt)!r} != {n(b, vt)!r})  {why}")


def ne(a, b, vt, why=""):
    if n(a, vt) == n(b, vt):
        FAILS.append(f"MUST-DIFFER failed [{vt}] {a!r} vs {b!r}  "
                     f"(both {n(a, vt)!r})  {why}")


# ---- money: MUST be equal (format drift is not a change) ----
eq("$30", "30.00", "money", "currency + trailing zero")
eq("$30", "30", "money", "currency symbol only")
eq("$12.69", "12.69", "money", "the original burn value")
eq("$297/mo", "297/mo", "money", "RR11: unit-suffixed price, $ vs none")
eq("$1,297.50", "1297.5", "money", "thousands comma + trailing zero")
eq("$0.30/mo", "0.30/mo", "money", "sub-dollar unit-suffixed")
eq("  $30  ", "$30", "money", "whitespace")

# ---- money: MUST differ (real changes / distinct values) ----
ne("30 days", "$30", "money", "RR9: unit-blind — a mis-bound value must NOT re-confirm")
ne("$100 setup + $30/mo", "$100 one-time, then $75/mo", "money", "R3: multi-number change")
ne("-$5", "$5", "money", "sign preserved")
ne("$597", "$897", "money", "different prices")
ne("$297/mo", "$347/mo", "money", "real unit-suffixed change")

# ---- percent ----
eq("30%", "30.0", "percent", "percent format drift")
ne("30%", "40%", "percent", "different percentages")

# ---- string (default) ----
eq("US-East", "us-east", "string", "case")
eq("hello   world", "hello world", "string", "whitespace runs")
ne("us-east", "us-west", "string", "different values")
eq("disabled", "DISABLED", None, "value_type None -> string path")

# ---- ROUND-4 GAPS (Claude Code final pass, 2026-07-04) --------------------
# These rows FAIL today. They are the missing corpus shapes the table needed
# to be a real tripwire. The first one is LIVE SILENT POISON (see harness RR13:
# an echo of history '~$0.30/mo' re-mentioned as '$0.30/mo' slips the guard and
# SUPERSEDES) — '~$0.30/mo' is a literal corpus value (test_set.py case 5).
# Fix sketch: strip approx markers (~, ≈) and map unicode minus in `cleaned`;
# make the residue check reject parentheses and non-ASCII currency symbols
# (whitelist residue chars) instead of only rejecting [a-zA-Z].
eq("~$0.30/mo", "$0.30/mo", "money", "ROUND-4: approx-tilde drift on unit-suffixed (LIVE POISON, RR13)")
eq("~$0.30/mo", "0.30/mo", "money", "ROUND-4: tilde + $ drift together")
ne("−$5", "$5", "money", "ROUND-4: unicode minus loses the sign -> sign-flip false-equal")
ne("($30)", "$30", "money", "ROUND-4: accounting-negative parens read as +30")
ne("€30", "$30", "money", "ROUND-4: cross-currency false-equal (euro == dollar)")
ne("30% off", "$30 off", "money", "ROUND-4: money path strips % too -> percent == dollars")
ne("$5-10", "$5–10", "money", "ROUND-4 reconciled → ACCEPTED NOISE: ASCII-hyphen range parses -10, en-dash doesn't; disambiguating hyphen-as-range vs hyphen-as-minus would endanger real sign handling. Failure is a spurious CONFLICT (safe), never a wrong value.")

# ---- ROUND-5 GAP (Claude Code confirmation pass, 2026-07-04) --------------
# The number regex `-?\d+(?:\.\d+)?` requires a LEADING DIGIT, so a leading-dot
# decimal ".30" parses as "30": '$.30' normalizes to '30.00'. Both directions
# break — false-EQUAL with '$30' (LIVE POISON, harness RR14: a real 100x price
# change ingested as '$.30' returns 'refreshed' — swallowed as a re-confirmation
# of $30, and the freshness clock moves) and false-DIFFER from '$0.30' (echo
# vector). Fix: `-?(?:\d+(?:\.\d+)?|\.\d+)` in BOTH the findall and the residue
# sub. '$.30/mo' is a realistic US-shorthand corpus shape.
ne("$.30", "$30", "money", "ROUND-5: leading-dot decimal — 30 cents must NOT equal 30 dollars (LIVE POISON, RR14)")
eq("$.30", "$0.30", "money", "ROUND-5: leading-dot decimal drift of the same value")

# ---- current behavior LOCKED (these pass; documented on purpose) ----------
ne("＄30", "$30", "money", "full-width dollar sign falls back — splits (noise-direction, accepted; CJK input artifact, unlikely in this corpus)")
ne("$1 297.50", "$1,297.50", "money", "NBSP thousands separator splits into two numbers (noise-direction, accepted; European format, unlikely in this corpus)")
eq("$30.", "$30", "money", "trailing period")
eq("$ 30", "$30", "money", "space after currency symbol")
eq("３０", "30", "money", "full-width digits alone (re \\d and float() both accept them)")
ne("3e2", "$300", "money", "exponent form falls back — splits (safe)")
eq("$5k", "5k", "money", "k-suffix drift (both fall back, $ stripped)")
eq("$1,297.50/yr", "1297.50/yr", "money", "comma + unit suffix drift")
ne("USD 30", "$30", "money", "USD prefix falls back to string — splits (noise-direction, accepted)")
ne("$5k", "$5,000", "money", "no unit expansion — semantically equal but split (noise-direction, accepted)")
ne("40%/yr", "40/yr", "money", "ROUND-4 reconciled → DIFFER: money path no longer strips % (that was needed for '30% off' != '$30 off'). Semantically correct: 40% is not 40. The old 'equal' lock encoded the buggy %-strip.")

# ---- ROUND-6 GAP (Fable-Code adversarial pass, 2026-07-05, issue N5) -------
# The old unconditional `.replace(",", "")` read EVERY comma as a thousands
# separator, so a European comma-decimal "12,69" (== 12.69) became 1269 —
# a LIVE SILENT POISON: ingesting "12,69" over a stored "$1,269" returned
# 'refreshed' and moved the freshness clock while all suites stayed green.
# Fix: disambiguate — "<digit>,<1-2 digits><boundary>" is a decimal comma
# ("," -> "."); "<digit>,<exactly 3 digits><boundary>" is thousands (drop it);
# anything else keeps the comma and falls to the string path (never a number).
ne("12,69", "1269", "money", "N5 LIVE POISON: comma-decimal 12.69 must NOT read as 1269")
ne("12,69", "$1,269", "money", "N5: the exact poison pair — 12.69 vs 1,269 dollars")
eq("12,69", "12.69", "money", "N5: comma-decimal == dot-decimal")
eq("12,69", "$12.69", "money", "N5: comma-decimal == currency dot-decimal")
eq("1,5", "1.50", "money", "N5: single-trailing-digit comma-decimal")
ne("1,5", "15", "money", "N5: 1,5 is one-point-five, not fifteen")
eq("1,299", "1299", "money", "N5: exactly-3-digit group stays a thousands separator")
eq("1,234,567", "1234567", "money", "N5: multi-group thousands all stripped")
eq("$1,299/yr", "1299/yr", "money", "N5: thousands + unit suffix still string-equal (unchanged)")
ne("1,23,456", "123456", "money", "N5: odd/mixed grouping keeps the comma -> string fallback, never a silent number")


def main():
    print("=" * 60)
    print("normalize_value property table")
    print("=" * 60)
    total = 20  # keep in rough sync with the assertions above
    if FAILS:
        print(f"FAILURES ({len(FAILS)}):")
        for f in FAILS:
            print("  -", f)
        print("=" * 60)
        sys.exit(1)
    print(f"ALL PAIRS PASS (~{total} equal/differ assertions across money/percent/string)")
    print("normalize_value behavior locked — do not edit without re-running this.")
    sys.exit(0)


if __name__ == "__main__":
    main()

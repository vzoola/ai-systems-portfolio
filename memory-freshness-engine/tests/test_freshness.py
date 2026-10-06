"""
test_freshness.py — the adversarial test the freshness engine MUST pass.

Reproduces the exact bug this system exists to defeat:
  "OTM Vapi credits" was topped up to $30 on 2026-07-01, but a note written
  on 2026-07-03 re-quoted the old pre-topup balance "$12.69". A naive
  "newest text wins" memory system would return $12.69 because that text
  was *mentioned* most recently -- which is WRONG. Currency must be decided
  by EVENT TIME (when the fact became true), not by mention/recording order.

Run: python3 test_freshness.py
Exit 0 if every check passes, exit 1 on the first failure.
"""

import os
import sys
import tempfile

from fact_store import FactStore

PASS = "PASS"
FAIL = "FAIL"

failures = []


def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    line = f"[{status}] {label}"
    if detail:
        line += f" ({detail})"
    print(line)
    if not condition:
        failures.append(label)


def main():
    db_fd, db_path = tempfile.mkstemp(prefix="freshness_test_", suffix=".db")
    os.close(db_fd)
    os.remove(db_path)  # let sqlite create it fresh

    print(f"Using scratch DB: {db_path}\n")

    store = FactStore(db_path)

    # ------------------------------------------------------------------
    # SCENARIO 1: the OTM Vapi credits bug, exactly as it happened.
    # ------------------------------------------------------------------
    print("=== Scenario 1: OTM Vapi credits (the $12.69 trap) ===")

    print("Step 1: assert_fact('otm','vapi_credits','$12.69', event 2026-06-28, "
          "'pre-topup balance')")
    store.assert_fact(
        "otm", "vapi_credits", "$12.69",
        event_time="2026-06-28T00:00:00", source="pre-topup balance",
    )
    check("Step 1: current after initial insert is $12.69",
          store.current("otm", "vapi_credits") == "$12.69",
          f"got={store.current('otm', 'vapi_credits')!r}")

    print("\nStep 2: assert_fact('otm','vapi_credits','$30', event 2026-07-01, "
          "'Vahe topped up')")
    store.assert_fact(
        "otm", "vapi_credits", "$30",
        event_time="2026-07-01T00:00:00", source="Vahe topped up",
    )

    print("\nStep 3: current('otm','vapi_credits') should be '$30' (newer event wins)")
    val3 = store.current("otm", "vapi_credits")
    check("Step 3: current == $30 after topup", val3 == "$30", f"got={val3!r}")

    print("\nStep 4 (THE TRAP): re-mention the stale value 'later' in wall-clock time "
          "but with the OLD event_time —")
    print("  assert_fact('otm','vapi_credits','$12.69', event 2026-06-28, "
          "'re-quoted in a 7/3 note')")
    store.assert_fact(
        "otm", "vapi_credits", "$12.69",
        event_time="2026-06-28T00:00:00", source="re-quoted in a 7/3 note",
    )
    val4 = store.current("otm", "vapi_credits")
    check("Step 4: current STILL == $30 (stale re-mention did NOT override)",
          val4 == "$30", f"got={val4!r}")

    print("\nStep 5: exactly ONE current row exists for ('otm','vapi_credits')")
    cur = store._conn.execute(
        "SELECT COUNT(*) AS n FROM facts WHERE domain=? AND fact_key=? AND valid_to IS NULL",
        ("otm", "vapi_credits"),
    ).fetchone()
    check("Step 5: exactly one current row", cur["n"] == 1, f"count={cur['n']}")

    print("\nStep 6: '$12.69' is still present in history() (preserved, not deleted)")
    hist = store.history("otm", "vapi_credits")
    print("  history:")
    for row in hist:
        print(f"    value={row['value']!r:>8}  event_time={row['event_time']}  "
              f"recorded_at={row['recorded_at']}  valid_to={row['valid_to']}  "
              f"source={row['source']!r}")
    values_in_history = [row["value"] for row in hist]
    check("Step 6: $12.69 preserved in history",
          "$12.69" in values_in_history, f"history values={values_in_history}")
    check("Step 6b: history has 3 rows (2 stale $12.69 rows + 1 current $30 row)",
          len(hist) == 3, f"got {len(hist)} rows")
    check("Step 6c: the $30 row is the only one with valid_to IS NULL",
          sum(1 for r in hist if r["valid_to"] is None) == 1)
    check("Step 6d: both $12.69 rows are marked superseded (valid_to = 2026-07-01)",
          all(r["valid_to"] == "2026-07-01T00:00:00"
              for r in hist if r["value"] == "$12.69"))

    # ------------------------------------------------------------------
    # SCENARIO 2: a second key, to prove this isn't a one-off — a price
    # change where an old quote resurfaces after a price increase.
    # ------------------------------------------------------------------
    print("\n=== Scenario 2: generalization — SearchFit SEO monthly price ===")

    print("Step 1: assert_fact('searchfit','monthly_price','$49', event 2026-01-01, "
          "'launch price')")
    store.assert_fact(
        "searchfit", "monthly_price", "$49",
        event_time="2026-01-01T00:00:00", source="launch price",
    )

    print("Step 2: assert_fact('searchfit','monthly_price','$79', event 2026-05-01, "
          "'price increase announced')")
    store.assert_fact(
        "searchfit", "monthly_price", "$79",
        event_time="2026-05-01T00:00:00", source="price increase announced",
    )
    check("Step 2: current == $79", store.current("searchfit", "monthly_price") == "$79")

    print("Step 3 (trap): an old sales deck footer re-quotes '$49' dated back to launch")
    store.assert_fact(
        "searchfit", "monthly_price", "$49",
        event_time="2026-01-01T00:00:00", source="stale sales deck footer",
    )
    val_sf = store.current("searchfit", "monthly_price")
    check("Step 3: current STILL == $79 (stale deck footer did not override)",
          val_sf == "$79", f"got={val_sf!r}")

    print("Step 4: a genuine further price change with a NEWER event wins")
    store.assert_fact(
        "searchfit", "monthly_price", "$99",
        event_time="2026-08-01T00:00:00", source="second price increase",
    )
    val_sf2 = store.current("searchfit", "monthly_price")
    check("Step 4: current == $99 after genuine newer change", val_sf2 == "$99",
          f"got={val_sf2!r}")

    # ------------------------------------------------------------------
    # SCENARIO 3: a third key — equal event_time correction (same-day
    # correction should win per the ">=" rule), plus identical-value no-op.
    # ------------------------------------------------------------------
    print("\n=== Scenario 3: equal-event-time correction + identical-value no-op ===")

    store.assert_fact(
        "patientcatch", "supported_languages", "FOUR languages",
        event_time="2026-03-01T00:00:00", source="initial spec",
    )
    print("Step 1: initial value 'FOUR languages' set at 2026-03-01")

    # Same-day correction (equal event_time) should be treated as newer info
    # and win, per the ">=" rule in assert_fact.
    store.assert_fact(
        "patientcatch", "supported_languages", "FOUR languages (corrected list)",
        event_time="2026-03-01T00:00:00", source="same-day correction",
    )
    val_pc = store.current("patientcatch", "supported_languages")
    check("Step 2: equal event_time correction wins (>=  rule)",
          val_pc == "FOUR languages (corrected list)", f"got={val_pc!r}")

    # Re-asserting the exact same current value/time again should be a no-op,
    # not create a duplicate current row or blow up the unique index.
    store.assert_fact(
        "patientcatch", "supported_languages", "FOUR languages (corrected list)",
        event_time="2026-03-01T00:00:00", source="re-confirmed",
    )
    hist_pc = store.history("patientcatch", "supported_languages")
    check("Step 3: identical-value re-assert is a no-op (still 2 rows total)",
          len(hist_pc) == 2, f"got {len(hist_pc)} rows: {hist_pc}")
    check("Step 3b: current is still 'FOUR languages (corrected list)'",
          store.current("patientcatch", "supported_languages")
          == "FOUR languages (corrected list)")

    # ------------------------------------------------------------------
    # audit() — structural safety net across everything inserted above.
    # ------------------------------------------------------------------
    print("\n=== audit() — global health check ===")
    report = store.audit()
    print(f"  {report}")
    check("audit: no (domain,key) has more than one current row", report["ok"])
    check("audit: total_facts matches expected count (3 + 4 + 2 = 9)",
          report["total_facts"] == 9, f"got={report['total_facts']}")
    check("audit: distinct_keys == 3", report["distinct_keys"] == 3,
          f"got={report['distinct_keys']}")

    store.close()
    os.remove(db_path)

    print("\n" + "=" * 60)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        return 1
    else:
        print("RESULT: ALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())

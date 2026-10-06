"""
test_answer_gate.py — E2E proof that the injection layer OBEYS status
(Grok round-2 ask). Runs real facts through ingest->serve->gate and asserts a
non-CURRENT value can never be presented as authoritative fact.

This is the deterministic half of "prove injection obeys status": it proves the
BLOCK a model is handed is safe. The remaining half — that a real LLM given a
correct block actually respects it — is the LOCAL_BRAIN Mac E2E (a real prompt,
asserting no bare stale dollar amount in the model's answer).
"""

import os
import sys
import tempfile

from whole_loop import MemoryLoop
from answer_gate import (
    build_injection_block, authoritative_values, is_safe_to_assert, render_line,
)

FAILS = []


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILS.append(label)


def fresh():
    fd, p = tempfile.mkstemp(suffix=".db"); os.close(fd); os.unlink(p)
    return MemoryLoop(p), p


def ext(v, et, kind="event", confident=True):
    return {"value": v, "event_time": et, "kind": kind, "confident": confident}


def main():
    print("=" * 60)
    print("test_answer_gate — injection layer obeys status (E2E)")
    print("=" * 60)

    # Build a realistic mixed state: one CURRENT, one STALE, one UNVERIFIED,
    # one CONTESTED, one UNKNOWN — then prove the gate quarantines all non-CURRENT.
    m, p = fresh()
    try:
        # CURRENT: a fresh, dated fact
        m.ingest("otm", "price", ext("$297", "2026-07-01"), "2026-07-01")
        price = m.serve("otm", "price")

        # STALE: aged perishable
        m.ingest("vapi", "credit_balance", ext("$30", "2026-06-01"), "2026-06-01")
        bal = m.serve("vapi", "credit_balance", as_of="2026-07-04")  # >14d -> STALE

        # UNVERIFIED: undated mention -> hearsay
        m.ingest("infra", "x_api_price", ext("$12.69", ""), "2026-07-03")
        xapi = m.serve("infra", "x_api_price")

        # CONTESTED: equal-time different value
        m.ingest("infra", "region", ext("us-east", "2026-06-15"), "2026-06-15")
        m.ingest("infra", "region", ext("us-west", "2026-06-15"), "2026-06-15")
        region = m.serve("infra", "region")

        # UNKNOWN
        unk = m.serve("otm", "never_seen")

        check("CURRENT price is assertable", is_safe_to_assert(price["status"]))
        check("STALE balance NOT assertable", not is_safe_to_assert(bal["status"]))
        check("UNVERIFIED x_api NOT assertable", not is_safe_to_assert(xapi["status"]))
        check("CONTESTED region NOT assertable", not is_safe_to_assert(region["status"]))
        check("UNKNOWN NOT assertable", not is_safe_to_assert(unk["status"]))

        items = [
            ("otm/price", price),
            ("vapi/credit_balance", bal),
            ("infra/x_api_price", xapi),
            ("infra/region", region),
            ("otm/never_seen", unk),
        ]
        block = build_injection_block(items)
        auth_vals = authoritative_values(items)

        # The core E2E safety property: the ONLY value the gate lets be asserted
        # is the CURRENT one. The stale $30, the unverified $12.69 — never.
        check("only $297 is authoritative", auth_vals == {"$297"})
        check("$30 (stale) is NOT authoritative", "$30" not in auth_vals)
        check("$12.69 (unverified) is NOT authoritative", "$12.69" not in auth_vals)

        # Split the rendered block and prove no non-CURRENT value appears above
        # the QUARANTINE divider.
        head, _, tail = block.partition("=== QUARANTINE")
        check("authoritative section contains $297", "$297" in head)
        check("authoritative section has NO $30", "$30" not in head)
        check("authoritative section has NO $12.69", "$12.69" not in head)
        check("quarantine explicitly says do NOT assert", "do NOT assert" in tail)
        # Every non-CURRENT value, if shown at all, is wrapped in a do-not-assert line
        check("$12.69 only appears with an uncertainty marker",
              ("$12.69" not in block) or ("UNVERIFIED" in block and "NOT confirmed" in block))
        check("$30 only appears with a STALE marker",
              ("$30" not in block) or ("STALE" in block))

        print("\n--- sample injection block a model would receive ---")
        print(block)
    finally:
        m.close(); os.path.exists(p) and os.unlink(p)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILED")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL PASSED — the injection block obeys status; a non-CURRENT")
    print("value can never enter the authoritative section.")
    print("(Remaining: LOCAL_BRAIN real-LLM E2E — a live prompt with this block,")
    print(" asserting the model's answer states no quarantined value as fact.)")
    sys.exit(0)


if __name__ == "__main__":
    main()

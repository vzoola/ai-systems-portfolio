"""
answer_gate.py — the serve -> injection/answer boundary (Grok round-2 "whole
category none touched": the serve contract is correct in Python, but nobody had
proven the layer that FEEDS a model obeys `status`).

The danger: serve returns {value:"$12.69", status:"UNVERIFIED"}. The value field
is a loaded gun; status is the safety. Any naive injection that prints the value
as "authoritative state" recreates the original burn with honest provenance
attached. This module is the deterministic gate between serve() and whatever
prompt/answer a model sees:

  - Only status == CURRENT facts may enter the AUTHORITATIVE block (safe to
    assert as fact).
  - STALE / UNVERIFIED / CONTESTED / UNKNOWN go to a QUARANTINE block, each line
    explicitly marked "do NOT assert", value shown only with its uncertainty.
  - `is_safe_to_assert(status)` is the single predicate the rest of the system
    must gate on — never on the presence of a value.

Stdlib only.
"""

ASSERTABLE = {"CURRENT"}


def is_safe_to_assert(status: str) -> bool:
    """The ONLY thing allowed to gate a confident statement of a value."""
    return status in ASSERTABLE


def _qualifier(status: str) -> str:
    return {
        "STALE": "STALE — last known, may be outdated, REVERIFY before use",
        "UNVERIFIED": "UNVERIFIED — an unconfirmed note only, do NOT assert",
        "CONTESTED": "CONTESTED — unresolved conflict, do NOT rely",
        "UNKNOWN": "UNKNOWN — not in memory",
    }.get(status, f"{status} — do NOT assert")


def render_line(key_label: str, served: dict) -> str:
    """One human/model-facing line that can NEVER present a non-CURRENT value
    as a bare fact."""
    status = served.get("status")
    value = served.get("value")
    if is_safe_to_assert(status):
        et = served.get("event_time")
        return f"{key_label}: {value}" + (f" (as of {et})" if et else "")
    if value is None:
        return f"{key_label}: [{_qualifier(status)}]"
    return f"{key_label}: [{_qualifier(status)}] reported value={value!r} — NOT confirmed"


def build_injection_block(items):
    """
    items: list of (key_label, served_dict).
    Returns the string a model would be given. AUTHORITATIVE holds only CURRENT
    facts (safe to state); QUARANTINE holds everything else, each marked do-not-
    assert. A non-CURRENT value can NEVER appear in AUTHORITATIVE.
    """
    authoritative, quarantine = [], []
    for label, served in items:
        (authoritative if is_safe_to_assert(served.get("status")) else quarantine
         ).append(render_line(label, served))

    parts = ["=== AUTHORITATIVE CURRENT STATE (safe to state as fact) ==="]
    parts += authoritative or ["(none)"]
    parts += ["", "=== QUARANTINE — do NOT assert any value below ==="]
    parts += quarantine or ["(none)"]
    return "\n".join(parts)


def authoritative_values(items):
    """The set of values the gate permits to be asserted. Anything NOT here must
    never be stated as current fact by a consumer."""
    return {served.get("value") for label, served in items
            if is_safe_to_assert(served.get("status"))}

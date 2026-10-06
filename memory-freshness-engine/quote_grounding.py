"""
quote_grounding.py — Phase ① G1-G4 quote-grounding (SPEC_SOAK_AND_HARDENING.md
Part 1.1-1.2), the S6 defense: every extracted fact must carry a verbatim
`quote` from its source document, and four deterministic, ordered, fail-fast
checks run BEFORE policy (whole_loop.ingest decides nothing until these pass).

Canonical text form (shared helper, used by all four checks):
    Unicode NFC -> curly/smart quotes & apostrophes -> straight
                -> collapse whitespace runs to a single space -> strip.
G1 compares canonical text CASE-SENSITIVE (a stronger fabrication tripwire).
G2-G4 compare CASE-INSENSITIVE.

The four checks, in order, each with its own code (the eval counts CAUSES,
not just failures — see SPEC 1.2 and SKILL §3 Phase ①):
    G1 SUBSTRING      -> REJECT_QUOTE_NOT_FOUND     quote not literally in source
    G2 VALUE-IN-QUOTE -> REJECT_VALUE_NOT_IN_QUOTE   extractor bound the wrong token
    G3 KEY-ANCHOR     -> ROUTE_CANDIDATES            NEVER a hard reject — the fact
                                                      may belong to a sibling key
    G4 DATE-IN-WINDOW -> DOWNGRADE_DATE_UNGROUNDED    strip the date, keep the value;
                                                      caller routes it to hearsay
An extraction with no quote at all never reaches G1: it is discarded before
policy and counted as NO_QUOTE.

Registry seam (Phase ② -- DONE): `get_key_meta(domain, key)` ->
{"value_type", "anchor_terms", "exclusion_terms"} now delegates to
registry.py's 73-key registry (registry.json, seeded from
REGISTRY_MERGED_FOR_VAHE_2026-07-07.md), preserving this exact
(domain, key) -> dict signature so this module and its callers (whole_loop.py)
did not need to change at any call site.

Stdlib only.
"""

import re
import unicodedata

from fact_store_v2 import normalize_value, parse_event_time
import registry

# ---------------------------------------------------------------------------
# Rejection / routing code constants
# ---------------------------------------------------------------------------
NO_QUOTE = "no_quote"
REJECT_QUOTE_NOT_FOUND = "REJECT_QUOTE_NOT_FOUND"
REJECT_VALUE_NOT_IN_QUOTE = "REJECT_VALUE_NOT_IN_QUOTE"
ROUTE_CANDIDATES = "ROUTE_CANDIDATES"
DOWNGRADE_DATE_UNGROUNDED = "DOWNGRADE_DATE_UNGROUNDED"
PASS = "PASS"

# Spec ceiling on extracted-quote length. Not independently enforced as a
# rejection code here — none was named for it (only the four codes above +
# NO_QUOTE exist); an over-length quote still lives or dies on G1 substring
# match like any other quote. See build report for this decided ambiguity.
MAX_QUOTE_CHARS = 200

_CURLY_TO_STRAIGHT = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "′": "'", "‵": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "″": '"', "‶": '"',
}
_WS_RE = re.compile(r"\s+")


def canonical_text(s) -> str:
    """NFC -> curly-to-straight quotes/apostrophes -> collapse whitespace -> strip.
    Case is PRESERVED here; callers lowercase explicitly for case-insensitive
    checks. G1 uses this form as-is (case-sensitive)."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFC", str(s))
    for curly, straight in _CURLY_TO_STRAIGHT.items():
        s = s.replace(curly, straight)
    return _WS_RE.sub(" ", s).strip()


def canonical_ci(s) -> str:
    """canonical_text, lowercased — the form G2-G4 compare on."""
    return canonical_text(s).lower()


# Sentence split, defined once (spec: "crude is fine at this corpus's
# hygiene; define once, test once").
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


def _sentence_spans(canonical_source: str):
    """[(start, end), ...] offsets of each sentence within canonical_source."""
    spans = []
    start = 0
    for m in _SENTENCE_BOUNDARY_RE.finditer(canonical_source):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(canonical_source)))
    return spans


def _window_around(canonical_source: str, quote_start: int, quote_end: int) -> str:
    """The quote's sentence(s), +/- 1 sentence, inside canonical_source."""
    spans = _sentence_spans(canonical_source)
    lo = hi = None
    for i, (s, e) in enumerate(spans):
        if s < quote_end and e > quote_start:  # true overlap with the quote span
            lo = i if lo is None else lo
            hi = i
    if lo is None:  # degenerate fallback — should not happen once G1 passed
        lo = hi = 0
    lo = max(0, lo - 1)
    hi = min(len(spans) - 1, hi + 1)
    return canonical_source[spans[lo][0]:spans[hi][1]]


# ---------------------------------------------------------------------------
# G2 — value normalized per value_type must appear inside the quote
# ---------------------------------------------------------------------------

# A quote is prose ("credit balance of $30"), not a bare value — normalize_value
# only takes the numeric fast-path when the ENTIRE string is number+formatting
# with no letters (its documented residue-whitelist behavior, fact_store_v2.py
# :184-199, which we do NOT edit). Feeding it the whole quote would fall
# through to the STRING branch and never isolate "$30" from the prose. So we
# pluck money/percent-shaped TOKENS out of the quote first, then normalize
# each token individually (each token has no letter residue, so it always
# takes normalize_value's numeric path) and compare the resulting sets.
_NUMERIC_TOKEN_RE = re.compile(r"[-+~≈−]?\$?\d[\d,]*(?:\.\d+)?%?")


def _numeric_tokens_in(text: str):
    return _NUMERIC_TOKEN_RE.findall(text)


def _money_amount_tokens(text: str, value_type: str) -> set:
    """Pluck money/percent-shaped tokens out of prose and normalize each one
    individually (each has no letter residue, so normalize_value takes its
    numeric path). The quote side has always used this; the value side must use
    it too — see _value_in_quote."""
    out = set()
    for tok in _numeric_tokens_in(text):
        nt = normalize_value(tok, value_type)
        if nt:
            out.update(nt.split("|"))
    return out


def _value_in_quote(value, value_type, canon_quote_ci: str) -> bool:
    if value_type in ("money", "percent"):
        # The VALUE from the extractor is prose too ("$497/mo", verbatim from
        # "base $497/mo"), not a bare number. Normalizing the WHOLE value string
        # took normalize_value's STRING branch whenever a period/unit suffix left
        # letter-residue ("$497/mo" -> "497/mo"), while the quote side plucked
        # bare tokens ("$497" -> "497.00") -- so a legitimately-grounded "$X/mo"
        # value could NEVER match its own quote (REJECT_VALUE_NOT_IN_QUOTE on the
        # $497 SKU#2 capture-at-birth entry despite the quote reading literally
        # "base $497/mo"; 2026-07-14 lane-new run). Fix: also pluck the value's
        # numeric amount tokens so both sides are canonicalized by the SAME path.
        # ADDITIVE -- val_tokens is a superset of the old whole-string form, so
        # no value that used to satisfy G2 can start failing it, and match
        # semantics (non-empty intersection = the amount is present) are
        # unchanged. Currency/unit BINDING remains the registry+G3's job; G2
        # grounds the numeric amount, and the quote side was already
        # currency-blind, so this only makes the value side consistent with it.
        norm_val = normalize_value(value, value_type)
        val_tokens = set(norm_val.split("|")) if norm_val else set()
        val_tokens |= _money_amount_tokens(canonical_ci(value), value_type)
        if not val_tokens:
            return False
        quote_tokens = _money_amount_tokens(canon_quote_ci, value_type)
        return bool(val_tokens & quote_tokens)
    if value_type == "date":
        try:
            dt = parse_event_time(value)
        except (ValueError, TypeError):
            # S6 mis-bind fix (2026-07-08): value_type is date but the value
            # did not parse as one. The old fallback here was raw containment,
            # which let a NON-date value ("~20% used") satisfy G2 on a
            # date-typed key (fable_access) purely because it appeared in the
            # quote — one of the 7/8 POISON mis-binds. Prose-date values
            # ("free until Jul 7") legitimately fail parse yet still CARRY a
            # date token, so accept only when a date-shaped token survives;
            # a value with no date token at all is a value-type mis-bind.
            if not _has_date_token(value):
                return False
            return canonical_ci(value) in canon_quote_ci
        return any(canonical_ci(v) in canon_quote_ci
                   for v in _date_surface_variants(dt))
    # enum/string default: case-insensitive containment
    return canonical_ci(value) in canon_quote_ci


# ---------------------------------------------------------------------------
# G3 — key-anchor. Only two outcomes ever: pass, or ROUTE_CANDIDATES (never a
# hard reject — SPEC 1.2/1.3(c) and SKILL §5 fence #2/#3).
# ---------------------------------------------------------------------------

def _anchor_present(text_ci: str, anchor_ci: str) -> bool:
    """S6 mis-bind fix (2026-07-08): an anchor term only counts when it sits at
    LETTER boundaries — i.e. it is not glued to an ASCII letter on either side.
    Plain substring containment let promiscuous short/embedded anchors license
    a wrong-key bind from adjacent prose: "al" matched inside "tot-AL-", "llama"
    matched inside "O-llama-" (Ollama) — two of the 7/8 POISON mis-binds. Digits
    and punctuation are still valid boundaries, so model-name/version anchors
    keep matching ("qwen" in "qwen2.5:3b", "ram" in "mac-ram"). This can only
    turn a former G3 PASS into ROUTE_CANDIDATES; it never newly PASSes anything,
    so it cannot admit a fact G3 previously rejected."""
    if not anchor_ci:
        return False
    return re.search(r"(?<![a-z])" + re.escape(anchor_ci) + r"(?![a-z])",
                     text_ci) is not None


def _check_g3(window_ci: str, quote_ci: str, anchor_terms, exclusion_terms):
    """
    Pass iff >=1 anchor_term appears (at letter boundaries — see
    _anchor_present) in the window AND no exclusion_term appears anywhere in
    the window (which contains the quote). Exclusion matching stays plain
    substring (deliberately the more aggressive form — an exclusion is a
    safety reject and must NOT be loosened). Reason is returned for
    diagnostics/metrics only ("the codes exist so failures indict the right
    layer" — SPEC 3.5) — the caller-facing code is always ROUTE_CANDIDATES
    regardless of which sub-reason fired.
    """
    anchors = [a.lower() for a in (anchor_terms or [])]
    exclusions = [e.lower() for e in (exclusion_terms or [])]
    has_anchor = any(_anchor_present(window_ci, a) for a in anchors)
    excl_in_quote = any(e in quote_ci for e in exclusions)
    excl_in_window = any(e in window_ci for e in exclusions)
    if has_anchor and not excl_in_window:
        return True, None
    if not has_anchor:
        return False, "no_anchor_in_window"
    if excl_in_quote:
        return False, "exclusion_in_quote"
    return False, "exclusion_in_window"


# ---------------------------------------------------------------------------
# G4 — date-in-window: a surface variant of the parsed event date must
# appear in the +/-1-sentence window.
# ---------------------------------------------------------------------------

_MONTH_FULL = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]
_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul",
               "Aug", "Sep", "Oct", "Nov", "Dec"]

# A month name (full or abbr) OR a numeric M/D OR an ISO date OR a bare 4-digit
# year. Used by G2's date-shape guard to tell a prose-date value ("free until
# Jul 7") from a non-date value ("~20% used") on a date-typed key.
_DATE_TOKEN_RE = re.compile(
    r"(?<![a-z])(?:" + "|".join(_MONTH_FULL + _MONTH_ABBR) + r")(?![a-z])"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{4}\b",
    re.IGNORECASE,
)


def _has_date_token(value) -> bool:
    """True iff `value` carries a recognizable date surface form. Lets prose
    dates (which fail strict parse) through G2 while rejecting values with no
    date content at all on a date-typed key (S6 value-type mis-bind)."""
    return _DATE_TOKEN_RE.search(str(value or "")) is not None


def _date_surface_variants(dt):
    """Surface forms per SPEC 1.2 examples: "July 1","Jul 1","7/1","07/01",
    "2026-07-01", plus the same two named forms with a trailing ", YYYY"."""
    m, d, y = dt.month, dt.day, dt.year
    full, abbr = _MONTH_FULL[m - 1], _MONTH_ABBR[m - 1]
    return {
        f"{full} {d}", f"{abbr} {d}",
        f"{full} {d}, {y}", f"{abbr} {d}, {y}",
        f"{m}/{d}", f"{m:02d}/{d:02d}",
        f"{y}-{m:02d}-{d:02d}",
    }


def _date_in_window(event_time, window_ci: str) -> bool:
    try:
        dt = parse_event_time(event_time)
    except (ValueError, TypeError):
        return False
    return any(canonical_ci(v) in window_ci for v in _date_surface_variants(dt))


# ---------------------------------------------------------------------------
# The ordered, fail-fast pipeline
# ---------------------------------------------------------------------------

def ground_extraction(value, quote, source_doc, value_type=None,
                       event_marker=None, event_time=None, key_meta=None):
    """
    Runs G1 -> G2 -> G3 -> G4 in order, fail-fast. Returns:
        {"code": <constant above>, "window": str|None, "reason": str|None}
    `window` (the quote's +/-1-sentence canonical window) is populated from
    G3 onward; None for NO_QUOTE/G1/G2 outcomes where no window was computed.
    `reason` is only set for ROUTE_CANDIDATES (G3 sub-reason, diagnostics only).
    """
    if not quote or not canonical_text(quote):
        return {"code": NO_QUOTE, "window": None, "reason": None}

    canon_quote = canonical_text(quote)            # case preserved, for G1
    canon_source = canonical_text(source_doc or "")

    idx = canon_source.find(canon_quote)            # G1: case-SENSITIVE substring
    if idx == -1:
        return {"code": REJECT_QUOTE_NOT_FOUND, "window": None, "reason": None}

    canon_quote_ci = canon_quote.lower()
    if not _value_in_quote(value, value_type, canon_quote_ci):
        return {"code": REJECT_VALUE_NOT_IN_QUOTE, "window": None, "reason": None}

    window = _window_around(canon_source, idx, idx + len(canon_quote))
    window_ci = window.lower()

    meta = key_meta or {}
    ok, reason = _check_g3(window_ci, canon_quote_ci,
                            meta.get("anchor_terms"), meta.get("exclusion_terms"))
    if not ok:
        return {"code": ROUTE_CANDIDATES, "window": window, "reason": reason}

    if event_marker == "explicit_date" and event_time:
        if not _date_in_window(event_time, window_ci):
            return {"code": DOWNGRADE_DATE_UNGROUNDED, "window": window, "reason": None}

    return {"code": PASS, "window": window, "reason": None}


# ---------------------------------------------------------------------------
# Registry seam (Phase ② -- wired to registry.py; the pre-Phase② fixture
# dict this replaced is preserved verbatim as registry.py's
# _LEGACY_FIXTURE_META, so ("otm","price") -- a test-only synthetic key, not
# one of the 73 frozen registry keys -- still resolves identically).
# ---------------------------------------------------------------------------

def get_key_meta(domain, key):
    """
    Returns {"value_type", "anchor_terms", "exclusion_terms"} for
    (domain, key), delegating to registry.py's 73-key registry. Unknown keys
    still get an empty-but-valid meta (value_type=None, no anchors, no
    exclusions) so G3 always routes to candidates on them (a missing
    registry entry is a candidate-queue/registry-growth signal, never a
    crash or a hard reject) -- this is registry.get_key_meta's own documented
    behavior, unchanged from the Phase ① fixture it replaced.
    """
    return registry.get_key_meta(domain, key)

"""
registry.py -- Phase (2) registry-as-code (SPEC_SOAK_AND_HARDENING.md PART 2
Sec 2.1, SKILL hq-memory-soak-campaign Sec 3 Phase (2)).

Loads registry.json -- the 73-key registry seeded from the FROZEN source of
truth, REGISTRY_MERGED_FOR_VAHE_2026-07-07.md (66 GOLDEN + 4 PENDING_ACTION +
3 DRIFTING). This module is metadata-only: it never holds a current VALUE for
a key (values live in fact_store_v2's `facts` table) -- it holds the MEANING
(definition, scope_excludes, anchors/exclusions, value_type, stakes, guarded,
lifecycle status) that the #13 referent-rot defense and the G1-G4 quote-
grounding checks (quote_grounding.py) are pinned against.

Registry freeze + golden-label provenance: the registry was ALREADY FROZEN
and golden-labeled by Vahe (2026-07-07, REGISTRY_MERGED_FOR_VAHE_2026-07-07.md)
before this module was built -- this module builds FROM that frozen sheet, it
does not re-freeze it.

Grading metadata (load-bearing for Phases (5)/(6), the soak grader): every key
carries a `grade_class` in {golden, pending_action, drifting}, copied exactly
from the frozen sheet's SOAK GRADING POLICY split. `pending_action` keys stay
IN the registry (so ingest/serve/quote-grounding still work on them) but the
grader must EXCLUDE them from the silent-poison/accuracy metrics -- their
golden value is a decision not yet live on the site/DB (SKILL Sec 5 fence #3).
`drifting` keys additionally carry `tolerance` + `as_of` so the grader can
grade with a tolerance band instead of a frozen point value, never as a
frozen point (SPEC 3.5 / SKILL Sec 5 fence #3).

Two registry seams this module fills (both pre-existing, both preserved
byte-for-byte in call signature):
  - quote_grounding.get_key_meta(domain, key) -> {"value_type",
    "anchor_terms", "exclusion_terms"}  (was `_KEY_META_FIXTURE`, 2 keys)
  - whole_loop.py's VALUE_TYPES / PERISHABLE_HORIZON_DAYS hardcoded dicts
    (2 keys / 1 key) -> value_type_for() / perishable_days_for()

Legacy fixture key: ("otm", "price") is a synthetic key used only by the
pre-existing test suites (test_answer_gate.py, test_quote_grounding.py) --
it is NOT one of the 73 frozen registry keys (no `otm/price` row exists in
REGISTRY_MERGED_FOR_VAHE_2026-07-07.md; the real OTM prices are
`otm/starter_price` / `otm/full_team_price`). It is kept as a small
backward-compat entry below (_LEGACY_FIXTURE_META) purely so those
pre-existing suites keep resolving value_type="money" identically to the old
hardcoded VALUE_TYPES/{_KEY_META_FIXTURE} dicts -- it is never counted in the
73-key registry total or in the grade_class split.

Deprecation SEMANTICS (Phase (3), SPEC_SOAK_AND_HARDENING.md PART 2 Sec 2.2):
implemented in whole_loop.py's ingest()/serve() -- this module's part of that
work is `find_key_by_alias()` below, the reverse-alias lookup ingest() uses to
redirect a write TARGETING an alias of a deprecated key onto its successor
(the ONE allowed redirect; it never copies the deprecated key's old value --
SKILL fence #1). Deprecation itself stays pure registry metadata (status,
deprecated_at, superseded_by, deprecation_reason) joined at ingest/serve time
-- this module holds no runtime state for it (retirement's "zero serve hits"
counter lives in-memory on whole_loop.MemoryLoop, never here and never in the
SQL store -- zero store schema change, per spec).

Stdlib only.
"""

import json
import os

_REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "registry.json")

# Legacy pre-registry test-fixture key -- see module docstring. NOT part of
# the frozen 73-key registry; used only so byte-for-byte pre-existing
# suites (test_answer_gate.py's ("otm","price") ingests) keep resolving
# value_type="money" exactly as whole_loop.py's old hardcoded VALUE_TYPES
# dict did.
_LEGACY_FIXTURE_META = {
    ("otm", "price"): {
        "value_type": "money",
        "anchor_terms": ["otm", "price", "plan"],
        "exclusion_terms": [],
        "perishable_days": None,
    },
}


def _load():
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        doc = json.load(f)
    by_domain_key = {}
    for entry in doc["keys"]:
        by_domain_key[(entry["domain"], entry["key"])] = entry
    return doc, by_domain_key


_DOC, _BY_KEY = _load()


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------

def all_keys():
    """List every full registry entry dict (all 73 frozen keys). Does NOT
    include the legacy fixture key -- that is test-only backward-compat, not
    a registered key."""
    return list(_DOC["keys"])


def get_key(domain, key):
    """Full registry dict for (domain, key), or None if not registered
    (including the legacy fixture key -- use get_key_meta for that seam)."""
    entry = _BY_KEY.get((domain, key))
    return dict(entry) if entry is not None else None


def get_key_meta(domain, key):
    """
    quote_grounding.py's seam -- EXACT same (domain, key) -> dict signature
    as the fixture it replaces: {"value_type", "anchor_terms",
    "exclusion_terms"}. Unknown keys (not in the registry, not the legacy
    fixture) get a safe empty-but-valid meta so G3 always routes to
    candidates rather than crashing (matches the pre-Phase(2) fixture's
    documented behavior).
    """
    entry = _BY_KEY.get((domain, key))
    if entry is not None:
        return {
            "value_type": entry["value_type"],
            "anchor_terms": list(entry["anchor_terms"]),
            "exclusion_terms": list(entry["exclusion_terms"]),
        }
    legacy = _LEGACY_FIXTURE_META.get((domain, key))
    if legacy is not None:
        return {
            "value_type": legacy["value_type"],
            "anchor_terms": list(legacy["anchor_terms"]),
            "exclusion_terms": list(legacy["exclusion_terms"]),
        }
    return {"value_type": None, "anchor_terms": [], "exclusion_terms": []}


def value_type_for(domain, key):
    """whole_loop.py's VALUE_TYPES seam. Registry keys with a granular type
    label (phone/enum/number/date/string) that normalize_value() doesn't
    special-case fall through to normalize_value's generic string path --
    identical to the pre-registry behavior where only "money" was ever
    hardcoded. Unknown keys (including anything not in the registry or the
    legacy fixture) return None, matching VALUE_TYPES.get(...)'s old
    default (normalize_value(value, None) already takes the string path)."""
    entry = _BY_KEY.get((domain, key))
    if entry is not None:
        return entry["value_type"]
    legacy = _LEGACY_FIXTURE_META.get((domain, key))
    if legacy is not None:
        return legacy["value_type"]
    return None


def perishable_days_for(domain, key):
    """whole_loop.py's PERISHABLE_HORIZON_DAYS seam. Returns an int or None
    (no staleness horizon -- the pre-registry default for every key except
    vapi/credit_balance)."""
    entry = _BY_KEY.get((domain, key))
    if entry is not None:
        return entry["perishable_days"]
    legacy = _LEGACY_FIXTURE_META.get((domain, key))
    if legacy is not None:
        return legacy["perishable_days"]
    return None


def grade_class_for(domain, key):
    """"golden" | "pending_action" | "drifting" | None (not a frozen
    registry key -- e.g. the legacy fixture, or truly unknown). Phase (5)/(6)
    soak grader seam: only "golden" rows count toward silent-poison/accuracy
    metrics; "pending_action" rows are excluded; "drifting" rows grade via
    tolerance_for()/as_of_for(), never as a frozen point value."""
    entry = _BY_KEY.get((domain, key))
    return entry["grade_class"] if entry is not None else None


def tolerance_for(domain, key):
    """Drifting-key grading tolerance dict ({"amount","unit",...}), or None
    for non-drifting / unknown keys."""
    entry = _BY_KEY.get((domain, key))
    return entry["tolerance"] if entry is not None else None


def as_of_for(domain, key):
    """Drifting-key as-of date string (YYYY-MM-DD), or None for
    non-drifting / unknown keys."""
    entry = _BY_KEY.get((domain, key))
    return entry["as_of"] if entry is not None else None


def find_key_by_alias(domain, alias):
    """
    Phase (3) reverse-alias lookup (SPEC 2.2.4): find the registry entry (in
    the given `domain`) whose `aliases` list contains `alias`, or None if no
    entry claims it. Scans `_BY_KEY` (not just the frozen 73 via `all_keys()`)
    so entries injected at runtime (e.g. by a test monkeypatching `_BY_KEY`
    directly, without touching registry.json) are resolvable too -- this is
    the ONLY seam whole_loop.ingest() uses to decide whether an incoming
    (domain, key) is actually someone else's alias.
    """
    for entry in _BY_KEY.values():
        if entry["domain"] == domain and alias in (entry.get("aliases") or []):
            return dict(entry)
    return None


def counts_by_grade_class():
    """{"golden": 66, "pending_action": 4, "drifting": 3} -- the frozen
    split, recomputed live from the loaded doc (not hardcoded) so a future
    registry edit can't silently drift from its own assertion."""
    counts = {}
    for entry in _DOC["keys"]:
        gc = entry["grade_class"]
        counts[gc] = counts.get(gc, 0) + 1
    return counts

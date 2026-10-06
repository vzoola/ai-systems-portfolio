"""
whole_loop.py — the ingest -> store -> serve policy layer.

Grok's review (2026-07-04) landed: reliability does NOT "live in the store"
until the layer that FEEDS the store is proven. The store executes whatever
(value, event_time) it is handed; the danger is a TRUE value handed over with
the wrong date or the wrong binding. That policy lives HERE, and until this
session it was hand-waved. This module makes every policy decision explicit
and testable, and adds the serve contract (abstention must never be served as
silent stale truth).

Explicit policy (each defends a specific attack Grok named):

  P1  empty extracted value   -> ABSTAIN. No write. (But serve may later flag
                                 the key STALE by age — abstention is NOT a
                                 silent "all good".)
  P2  empty / unparseable date -> NEVER default to the document date (F1
                                 landmine). Queue as contestable instead.
  P3  event_time is taken from the EXTRACTOR's stated date, and is NEVER
                                 re-derived from `kind`. A wrong kind therefore
                                 cannot move a timestamp. (Defuses Grok A2, the
                                 kind->date boundary.)
  P4  echo guard (Fable E4): if the incoming value equals a HISTORICAL
                                 (superseded) value of this key AND is dated
                                 NEWER than current, it is an ambiguous
                                 echo-or-return -> CONTESTED, do NOT supersede.
                                 (Defuses the forward-dated $12.69 re-mention.)
  P5  low-confidence flag      -> passed to the store as contestable=True.

Known residual holes this layer CANNOT close (measured, not hidden, by
test_whole_loop.py):
  - a CONFIDENT wrong date on a FIRST insert (no prior state to compare to)
    still anchors wrong (Grok A1);
  - key-bleed inside a multi-fact paragraph if the extractor mis-binds;
  - echo-vs-legitimate-return is undecidable from text -> we flag CONTESTED
    rather than guess, which is safe but needs a human to clear.
"""

import json
from datetime import datetime, timezone, timedelta

from fact_store_v2 import (
    FactStore, parse_event_time, canonical_event_time, normalize_value,
)
from quote_grounding import (
    ground_extraction, get_key_meta,
    NO_QUOTE, REJECT_QUOTE_NOT_FOUND, REJECT_VALUE_NOT_IN_QUOTE,
    ROUTE_CANDIDATES, DOWNGRADE_DATE_UNGROUNDED, PASS as GROUNDING_PASS,
)
import registry


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# Event times lie in the PAST. A date more than this far in the future is a
# typo/format error (a "2062" that would lock a key for 36 years) or a genuine
# scheduled/TTL fact — either way it must not silently become current (Fable B2).
FUTURE_TOLERANCE = timedelta(hours=48)

# Phase ③ (SPEC_SOAK_AND_HARDENING.md PART 2 Sec 2.2.6): a deprecated key with
# zero serve hits for this many days retires. Injected via serve()'s existing
# `as_of` clock param — never a wall-clock call (SKILL §3 Phase ③).
RETIREMENT_HORIZON_DAYS = 90

# Phase ⑤ E2/E3 (SPEC_SOAK_AND_HARDENING.md PART 3 Sec 3.4 E2/E3): the
# queue-resolution ladder that keeps the conflicts queue from growing
# unboundedly. Both act ONLY through the existing store.resolve_conflict()
# door (the same human-resolution door A4/C5/C6/etc. already use) — no new
# table, no side door.
#   AUTO_CONFIRM_WINDOW_DAYS: a 2nd INDEPENDENT (distinct source), event-
#     marked mention corroborating the same contested value, within this
#     many days of the first, promotes it to current (E2).
#   AUTO_DISMISS_RECORROBORATION_THRESHOLD: this many CONFIDENT
#     reconfirmations of the value that was already current, while a
#     conflict is open, auto-clears that conflict — the challenger failed
#     to corroborate; the incumbent held (E3).
# Both thresholds compare off event_time / a counted event count, never a
# wall clock.
AUTO_CONFIRM_WINDOW_DAYS = 7
AUTO_DISMISS_RECORROBORATION_THRESHOLD = 2

# Phase ⑤ D5 (SPEC PART 3 Sec 3.4 D5): a hearsay row stops being returned
# by serve() once it is this many days old (by as_of vs. its mention_date)
# — a serve-time filter only. The row is NEVER deleted and stays queryable
# via store.get_hearsay() forever (SKILL fence: age-out must not delete
# rows or falsify history).
HEARSAY_AGE_OUT_DAYS = 30

# Registry: value normalization type per key, so comparisons are semantic not
# textual (Fable B3). Unknown keys default to string normalization.
#
# Phase (2) (SPEC_SOAK_AND_HARDENING.md PART 2 Sec 2.1): these were a 2-key /
# 1-key hardcoded dict; now sourced from registry.py's 73-key registry
# (registry.json, seeded from REGISTRY_MERGED_FOR_VAHE_2026-07-07.md). The
# two legacy keys resolve identically to before:
#   ("vapi","credit_balance") -> "money" (real registry entry, DRIFTING class)
#   ("otm","price")           -> "money" (registry.py's small backward-compat
#                                 legacy-fixture map -- not one of the 73
#                                 frozen keys; see registry.py's module doc)
# Unknown keys still resolve to None, and normalize_value(value, None) still
# takes the same generic string-normalization path it always did -- no
# behavior change for any key outside the registry.


def _value_types_get(domain_key, default=None):
    """Back-compat shim: VALUE_TYPES.get((domain, key)) -> registry lookup."""
    domain, key = domain_key
    vt = registry.value_type_for(domain, key)
    return vt if vt is not None else default


def _perishable_horizon_get(domain_key, default=None):
    """Back-compat shim: PERISHABLE_HORIZON_DAYS.get((domain, key))."""
    domain, key = domain_key
    horizon = registry.perishable_days_for(domain, key)
    return horizon if horizon is not None else default


def _conflict_source_set(conflict_row):
    """
    A `conflicts.source` cell is a JSON array (fact_store_v2._queue_conflict
    stores it that way so a source name containing a comma can't be
    miscounted — Claude Code RR12). E2's independence check needs the same
    parsing; reuse the same defensive fallback rather than assuming shape.
    """
    raw = conflict_row.get("source")
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return {s for s in parsed if s}
        return {raw}
    except (ValueError, TypeError):
        return {raw}


def _hearsay_aged_out(hearsay_row, as_of):
    """
    Phase ⑤ D5: True once `hearsay_row` is more than HEARSAY_AGE_OUT_DAYS
    older than `as_of`, compared off its own `mention_date` (never a wall
    clock). An unparseable/missing mention_date fails OPEN (never ages
    out) — serve() must never crash or hide a row over a bad date string.
    """
    md = hearsay_row.get("mention_date")
    if not md:
        return False
    try:
        age_days = (parse_event_time(as_of) - parse_event_time(md)).days
    except ValueError:
        return False
    return age_days > HEARSAY_AGE_OUT_DAYS


class MemoryLoop:
    def __init__(self, db_path):
        self.store = FactStore(db_path)
        self.metrics = {
            "ingested": 0, "wrote": 0, "abstained": 0,
            "hearsay": 0, "future_contested": 0, "echo_contested": 0,
            # Phase ① G1-G4 quote-grounding counters (only increment when a
            # caller supplies source_doc — see ingest() docstring).
            "no_quote": 0, "reject_quote_not_found": 0,
            "reject_value_not_in_quote": 0, "route_candidates": 0,
            "downgrade_date_ungrounded": 0,
            # Phase ③ deprecation counters (SPEC PART 2 Sec 2.2).
            "alias_redirect_to_successor": 0, "deprecated_target_queued": 0,
        }
        # Phase ③ retirement bookkeeping (SPEC 2.2.6) — deliberately IN-MEMORY,
        # never in the SQL store (zero store schema change) and never derived
        # from a wall clock (driven by serve()'s injectable `as_of` instead).
        # `_serve_hits`: count of DEPRECATED-status serves per (domain, key)
        # since this MemoryLoop was constructed — a deprecated key that has
        # NEVER been served (hits==0) once >RETIREMENT_HORIZON_DAYS have
        # elapsed past its deprecated_at is eligible to retire.
        # `_retired_override`: once a key auto-retires it stays retired for
        # the lifetime of this loop (a later serve() call must not flip it
        # back to DEPRECATED just because that very call counts as a "hit").
        self._serve_hits = {}
        self._retired_override = set()
        # Phase ⑤ E3 bookkeeping (SPEC 3.4 E3) — count of CONFIDENT
        # reconfirmations of the value that was already current, seen while
        # a conflict is open for that key, since this MemoryLoop was
        # constructed. In-memory only (never the SQL store, never a
        # wall-clock call) — reset to 0 once it triggers an auto-dismiss.
        self._recorroboration_count = {}

    # ------------------------------------------------------------------
    # INGEST — the real policy layer (not a hand-call to assert_fact)
    # ------------------------------------------------------------------
    def ingest(self, domain, key, extractor_output, doc_date, source=None,
              source_doc=None):
        """
        extractor_output: {"value", "event_time", "kind", "confident"(bool),
        "quote"(optional str), "event_marker"(optional str, e.g.
        "explicit_date")}. Returns a status string describing the policy
        decision taken.

        source_doc (Phase ①, optional): the full source document text the
        extraction was drawn from. When supplied, the G1-G4 quote-grounding
        checks (quote_grounding.ground_extraction) run BEFORE any policy
        below, fail-fast, in order — see SPEC_SOAK_AND_HARDENING.md Part 1.
        When source_doc is None (no caller in this codebase's 6 pre-flight
        suites ever passes it), quote-grounding is skipped entirely and
        behavior is byte-for-byte identical to pre-Phase① whole_loop.py —
        this is the deliberate backward-compat seam that keeps the existing
        suites green without touching their call sites.
        """
        self.metrics["ingested"] += 1
        value = (extractor_output.get("value") or "").strip()
        et = (extractor_output.get("event_time") or "").strip()
        confident = extractor_output.get("confident", True)
        quote = extractor_output.get("quote")
        event_marker = extractor_output.get("event_marker")

        # P1: nothing extracted -> abstain, do not touch the store.
        if not value:
            self.metrics["abstained"] += 1
            return "abstained"

        # Phase ③ deprecation routing (SPEC_SOAK_AND_HARDENING.md PART 2 Sec
        # 2.2.4) — resolved BEFORE any value_type/grounding-dependent policy,
        # since (domain, key) itself may change below (alias -> successor
        # redirect). Registry lookup happens fresh on every call (never
        # cached), so a registry edit takes effect on the next ingest with no
        # process restart.
        reg_entry = registry.get_key(domain, key)
        if reg_entry is None:
            # Is (domain, key) actually an ALIAS of some OTHER registered key?
            alias_owner = registry.find_key_by_alias(domain, key)
            if (alias_owner is not None and alias_owner.get("status") == "deprecated"
                    and alias_owner.get("superseded_by")):
                # SPEC 2.2.4: aliases are REASSIGNED to the successor key at
                # deprecation. This redirects the WRITE TARGET only — the
                # successor's keyspace starts EMPTY; the deprecated key's old
                # value is never copied across (SKILL fence #1).
                key = alias_owner["superseded_by"][0]
                self.metrics["alias_redirect_to_successor"] += 1
                reg_entry = registry.get_key(domain, key)
        elif reg_entry.get("status") in ("deprecated", "retired"):
            # A DIRECT write targeting a deprecated (or retired) key: never a
            # silent accept, never an auto-migrate onto the successor (fence
            # #1) — route to the EXISTING conflicts queue mechanism
            # (fact_store_v2._queue_conflict / the `conflicts` table), the
            # same door P2/P4/contestable writes already use. No side door,
            # no second queue.
            vt_dep = _value_types_get((domain, key))
            cur = self.store.current_row(domain, key)
            try:
                conflict_et = canonical_event_time(et) if et else _now_iso()
            except ValueError:
                conflict_et = _now_iso()
            queued = self.store._queue_conflict(
                domain, key, value, conflict_et, _now_iso(), source,
                "deprecated_target",
                cur["value"] if cur else None,
                cur["event_time"] if cur else None, value_type=vt_dep)
            self.metrics["deprecated_target_queued"] += 1
            return "deprecated_target_queued" if queued else "deprecated_target_deduped"

        vt = _value_types_get((domain, key))

        # Phase ① G1-G4: quote-grounding runs BEFORE policy (P2-P5), fail-fast,
        # ordered. Never a side door — this is inside ingest(), the one policy
        # door both soak passes will use (SKILL §5 fence: no side doors).
        if source_doc is not None:
            meta = get_key_meta(domain, key)
            gr = ground_extraction(value, quote, source_doc, value_type=vt,
                                   event_marker=event_marker,
                                   event_time=et or None, key_meta=meta)
            code = gr["code"]
            if code == NO_QUOTE:
                self.metrics["no_quote"] += 1
                return NO_QUOTE
            if code == REJECT_QUOTE_NOT_FOUND:
                self.metrics["reject_quote_not_found"] += 1
                return REJECT_QUOTE_NOT_FOUND
            if code == REJECT_VALUE_NOT_IN_QUOTE:
                self.metrics["reject_value_not_in_quote"] += 1
                return REJECT_VALUE_NOT_IN_QUOTE
            if code == ROUTE_CANDIDATES:
                # G3 NEVER hard-rejects — the fact may belong to a sibling
                # key. Nothing is written; the extraction is simply not
                # bound to THIS key. No candidates-queue table exists yet
                # (that lands with Phase ④'s soak runner / registry growth
                # product) — Phase ① counts the cause via metrics.
                self.metrics["route_candidates"] += 1
                return ROUTE_CANDIDATES
            if code == DOWNGRADE_DATE_UNGROUNDED:
                # G4: the danger is an invented date, not the mention — strip
                # the date, KEEP the value, route to hearsay (never `facts`).
                self.store.add_hearsay(domain, key, value, mention_date=doc_date,
                                       source=source, quote=quote)
                self.metrics["hearsay"] += 1
                self.metrics["downgrade_date_ungrounded"] += 1
                return DOWNGRADE_DATE_UNGROUNDED
            # code == GROUNDING_PASS -> fall through to existing P2-P5 policy.

        # P2 (Fable B1): no usable date -> NEVER fabricate one from doc_date.
        # It goes to the HEARSAY lane and never enters `facts`/currency.
        usable_date = True
        if not et:
            usable_date = False
        else:
            try:
                new_dt = parse_event_time(et)
            except ValueError:
                usable_date = False
        if not usable_date:
            self.store.add_hearsay(domain, key, value,
                                   mention_date=doc_date, source=source,
                                   quote=quote)
            self.metrics["hearsay"] += 1
            return "hearsay"

        # B2: a date too far in the future can't be a past event -> never let it
        # anchor/supersede. Queue it (current, if any, is untouched).
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if new_dt > now + FUTURE_TOLERANCE:
            cur = self.store.current_row(domain, key)
            queued = self.store._queue_conflict(
                domain, key, value, canonical_event_time(et), _now_iso(),
                source, "future_event_time",
                cur["value"] if cur else None,
                cur["event_time"] if cur else None, value_type=vt)
            if queued:
                self.metrics["future_contested"] += 1
                return "future_contested"
            return "future_deduped"

        # P4: echo guard — forward-dated reappearance of a KNOWN old value.
        # Compare NORMALIZED values (Fable B3) so "12.69" can't slip past "$12.69".
        cur = self.store.current_row(domain, key)
        if cur is not None:
            historical_norm = {
                normalize_value(h["value"], vt)
                for h in self.store.history(domain, key)
                if h["valid_to"] is not None
            }
            cur_dt = parse_event_time(cur["event_time"])
            v_norm = normalize_value(value, vt)
            if (v_norm in historical_norm
                    and v_norm != normalize_value(cur["value"], vt)
                    and new_dt > cur_dt):
                queued = self.store._queue_conflict(
                    domain, key, value, canonical_event_time(et), _now_iso(),
                    source, "echo_or_return_ambiguous",
                    cur["value"], cur["event_time"], value_type=vt)
                if queued:
                    self.metrics["echo_contested"] += 1
                    return "echo_contested"
                # Already dismissed/known (dedup) -> don't lie that we re-flagged
                # it; the human's dismissal stands and serve stays as-is (Grok #14).
                return "echo_deduped"

        # P3/P5: trust the extractor's DATE (never kind); low-confidence -> queue.
        status = self.store.assert_fact(domain, key, value, et, source=source,
                                        contestable=not confident, value_type=vt,
                                        quote=quote)
        self.metrics["wrote"] += 1

        # Phase ⑤ E2/E3 (SPEC PART 3 Sec 3.4): the queue-resolution ladder,
        # layered on top of the SAME assert_fact() call above — no side
        # door, no second policy path. E2 only ever fires on a freshly
        # queued contestable conflict; E3 only ever fires on a confident
        # reconfirmation of the value that was ALREADY current before this
        # call (`cur`, fetched above for the echo guard).
        if status == "contestable_queued":
            if self._maybe_auto_confirm(domain, key, vt):
                status = "auto_confirmed"
        elif status in ("refreshed", "noop") and cur is not None:
            self._maybe_auto_dismiss(domain, key, vt, value, cur)

        return status

    # ------------------------------------------------------------------
    # Phase ⑤ E2 — AUTO-CONFIRM (SPEC PART 3 Sec 3.4 E2)
    # ------------------------------------------------------------------
    def _maybe_auto_confirm(self, domain, key, value_type):
        """
        Once >=2 OPEN conflicts for this key corroborate the SAME
        normalized value from DISTINCT ("independent") sources, with
        event_times within AUTO_CONFIRM_WINDOW_DAYS of each other, promote
        that value to current and resolve every matching open conflict.
        "Independent" = a source id that isn't shared between the two
        conflict rows — an echo re-mention from the SAME source must never
        self-confirm. Compared off the conflicts' own event_time fields —
        never wall-clock. Returns True iff an auto-confirm fired.
        """
        open_c = self.store.open_conflicts(domain, key)
        by_value = {}
        for c in open_c:
            nv = normalize_value(c["value"], value_type)
            by_value.setdefault(nv, []).append(c)
        for rows in by_value.values():
            if len(rows) < 2:
                continue
            for i in range(len(rows)):
                for j in range(i + 1, len(rows)):
                    ri, rj = rows[i], rows[j]
                    si, sj = _conflict_source_set(ri), _conflict_source_set(rj)
                    if not si or not sj or not si.isdisjoint(sj):
                        continue  # not independent (blank or shared source)
                    ti = parse_event_time(ri["event_time"])
                    tj = parse_event_time(rj["event_time"])
                    if abs((ti - tj).days) > AUTO_CONFIRM_WINDOW_DAYS:
                        continue
                    # The later corroborating mention is the one whose date
                    # + source becomes the accepted correction; the rest of
                    # the matching group is dismissed as redundant (already
                    # folded into the accepted value).
                    winner = rj if tj >= ti else ri
                    self.store.resolve_conflict(winner["id"], accept=True)
                    for other in rows:
                        if other["id"] != winner["id"] and other["resolved"] == 0:
                            self.store.resolve_conflict(other["id"], accept=False)
                    return True
        return False

    # ------------------------------------------------------------------
    # Phase ⑤ E3 — AUTO-DISMISS (SPEC PART 3 Sec 3.4 E3)
    # ------------------------------------------------------------------
    def _maybe_auto_dismiss(self, domain, key, value_type, value, prior_current):
        """
        Once the value that was CURRENT before this ingest() call is
        CONFIDENTLY re-corroborated AUTO_DISMISS_RECORROBORATION_THRESHOLD
        times while >=1 conflict is open for this key, the challenger
        failed to corroborate and the incumbent held — auto-clear every
        open conflict. `_recorroboration_count` is in-memory-only bookkeeping
        (never the SQL store, never a wall clock), matching the Phase ③
        retirement-bookkeeping style; it resets once it fires.
        """
        if normalize_value(value, value_type) != normalize_value(
                prior_current["value"], value_type):
            return
        open_c = self.store.open_conflicts(domain, key)
        if not open_c:
            return
        dk = (domain, key)
        self._recorroboration_count[dk] = self._recorroboration_count.get(dk, 0) + 1
        if self._recorroboration_count[dk] >= AUTO_DISMISS_RECORROBORATION_THRESHOLD:
            for c in open_c:
                if c["resolved"] == 0:
                    self.store.resolve_conflict(c["id"], accept=False)
            self._recorroboration_count[dk] = 0

    # ------------------------------------------------------------------
    # SERVE — the abstention/staleness contract
    # ------------------------------------------------------------------
    def serve(self, domain, key, as_of=None):
        """
        Returns {"value", "status", "event_time", "quote"}.
        status: CURRENT | UNKNOWN | CONTESTED | STALE | UNVERIFIED | DEPRECATED
        - CONTESTED: an unresolved conflict exists for this key (never serve a
          contested fact as if settled).
        - UNKNOWN: no fact known (never let the caller fall back to guessing).
          Also returned for a RETIRED key (see `registry_status` below) — the
          value itself is withheld once retired, only the pointer remains.
        - STALE: perishable key aged past its horizon (abstention/no-update is
          disclosed, not hidden behind a confident old value).
        - DEPRECATED (Phase ③, SPEC PART 2 Sec 2.2.3): the key's registry
          status is `deprecated`. Payload is the OLD current value (facts
          table is untouched by deprecation — it's a registry-metadata join,
          never a store mutation) + its as-of event_time/quote, PLUS
          `superseded_by` (list of successor key names) and
          `deprecation_reason`. Deprecation never falsifies history; it ends
          a meaning's tenure. `is_safe_to_assert()` already excludes anything
          not literally "CURRENT", so DEPRECATED is quarantined automatically
          — no answer_gate.py change needed.
        `quote` (Phase ①): the verbatim source excerpt backing this value, if
        one was recorded — a poisoned value must carry its own confession at
        read time (SPEC 1.3(iii)). None for pre-Phase① rows / legacy callers
        that never supplied a quote.
        """
        reg_entry = registry.get_key(domain, key)
        if reg_entry is not None and reg_entry.get("status") in ("deprecated", "retired"):
            return self._serve_deprecated_or_retired(domain, key, reg_entry, as_of)

        if self.store.open_conflicts(domain, key):
            row = self.store.current_row(domain, key)
            return {"value": row["value"] if row else None,
                    "status": "CONTESTED",
                    "event_time": row["event_time"] if row else None,
                    "quote": row["quote"] if row else None}

        row = self.store.current_row(domain, key)
        if row is None:
            # No settled fact. If an undated mention exists, disclose it as
            # UNVERIFIED with provenance (Fable B1) — never silent, never
            # promoted to a confident answer.
            hs = self.store.get_hearsay(domain, key)
            # Phase ⑤ D5 (SPEC 3.4 D5): a hearsay row this stale stops being
            # SERVED as UNVERIFIED (it stays queryable via get_hearsay()
            # forever — no row is ever dropped here). Only applied when an
            # explicit `as_of` is supplied — never a wall-clock fallback
            # (matches the Phase ③ retirement-check style: no as_of, no
            # age-out check, old behavior preserved byte-for-byte).
            if as_of is not None:
                hs = [h for h in hs if not _hearsay_aged_out(h, as_of)]
            if hs:
                # ----------------------------------------------------------
                # SPEC 3.4 D4 — hearsay echo guard (Vahe blessed Option 3,
                # 2026-07-14; frozen-file edit authorized).
                #
                # This branch used to `return hs[-1]` -- the MOST RECENT
                # mention. Undated input (i.e. ordinary chat, where P2 rightly
                # refuses to invent a date) routes EVERYTHING here, so:
                # state A -> correct to B -> restate A, and the newest mention
                # IS the echo. The correction got silently un-done and serve()
                # handed back the RETIRED value, 20/20 deterministic (found by
                # the Danculus agent-memory-integrity harness, 2026-07-13; our
                # own suites missed it because the soak corpus is date-stamped,
                # so we only ever exercised the dated path).
                #
                # The dated lane already refuses to guess here: P4 flags an
                # echo-or-return as CONTESTED precisely because
                # "echo-vs-legitimate-return is undecidable from text". The
                # undated lane has strictly LESS information, so it may not
                # quietly pick a winner where the dated lane declines to.
                #
                # So: if the surviving mentions disagree, we serve NO value and
                # disclose every one of them. answer_gate.py's docstring is the
                # rule -- "the value field is a loaded gun; status is the
                # safety" -- and the honest move when we cannot know which
                # undated mention is current is to UNLOAD THE GUN, not to hand
                # over the most recent thing anyone happened to say.
                #
                # Compare NORMALIZED values (Fable B3, mirroring P4 at L334) so
                # "$297"/"297" is not a false disagreement.
                # No row is dropped, no date invented, no history falsified --
                # every mention stays queryable via get_hearsay() forever.
                # `status` stays UNVERIFIED (already non-assertable), so
                # answer_gate semantics are untouched.
                vt = _value_types_get((domain, key))
                distinct = {normalize_value(h["value"], vt) for h in hs}

                if len(distinct) > 1:
                    return {
                        "value": None,          # <-- the gun, unloaded
                        "status": "UNVERIFIED",
                        "event_time": None,
                        "provenance": (
                            f"{len(hs)} undated mentions DISAGREE "
                            f"({len(distinct)} distinct values) — memory cannot "
                            f"tell which is current; go read the files"),
                        "quote": None,
                        "disagreement": True,
                        "competing_mentions": [
                            {"value": h["value"],
                             "mention_date": h["mention_date"],
                             "source": h["source"],
                             "quote": h["quote"]}
                            for h in hs
                        ],
                    }

                # All surviving mentions agree -> byte-identical to the old
                # behavior (same value, same provenance string, same quote).
                h = hs[-1]
                return {"value": h["value"], "status": "UNVERIFIED",
                        "event_time": None,
                        "provenance": f"per a {h['mention_date']} note"
                                      + (f" ({h['source']})" if h["source"] else ""),
                        "quote": h["quote"]}
            return {"value": None, "status": "UNKNOWN", "event_time": None,
                    "quote": None}

        horizon = _perishable_horizon_get((domain, key))
        if horizon is not None:
            as_of_dt = parse_event_time(as_of) if as_of else datetime.utcnow()
            age_days = (as_of_dt - parse_event_time(row["event_time"])).days
            if age_days > horizon:
                return {"value": row["value"], "status": "STALE",
                        "event_time": row["event_time"], "quote": row["quote"]}

        return {"value": row["value"], "status": "CURRENT",
                "event_time": row["event_time"], "quote": row["quote"]}

    # ------------------------------------------------------------------
    # Phase ③ — deprecation / retirement serve semantics (SPEC PART 2 Sec 2.2)
    # ------------------------------------------------------------------
    def _serve_deprecated_or_retired(self, domain, key, reg_entry, as_of):
        """
        Registry-metadata-only join (facts table is NEVER touched here — see
        serve()'s docstring). `reg_entry["status"]` is either "retired"
        (persisted directly in the registry, e.g. a key retired by hand) or
        "deprecated" (in which case retirement may ALSO be computed
        dynamically here: deprecated + >RETIREMENT_HORIZON_DAYS + zero serve
        hits, per SPEC 2.2.6 — driven off the `as_of` clock parameter that
        already exists on serve(), never a wall-clock call).
        """
        superseded_by = list(reg_entry.get("superseded_by") or [])
        reason = reg_entry.get("deprecation_reason")
        dk = (domain, key)

        def _retired_response():
            return {"value": None, "status": "UNKNOWN", "event_time": None,
                    "quote": None, "registry_status": "retired",
                    "superseded_by": superseded_by, "deprecation_reason": reason}

        if reg_entry["status"] == "retired" or dk in self._retired_override:
            return _retired_response()

        # status == "deprecated" — check the dynamic 90-day/zero-hit
        # retirement condition before serving the DEPRECATED shape.
        deprecated_at = reg_entry.get("deprecated_at")
        if as_of is not None and deprecated_at:
            try:
                days_since = (parse_event_time(as_of)
                              - parse_event_time(deprecated_at)).days
            except ValueError:
                days_since = None
            if (days_since is not None and days_since > RETIREMENT_HORIZON_DAYS
                    and self._serve_hits.get(dk, 0) == 0):
                # Sticky: once auto-retired it stays retired for this loop's
                # lifetime — this very call must not count as the "hit" that
                # flips it back to DEPRECATED on the next call.
                self._retired_override.add(dk)
                return _retired_response()

        self._serve_hits[dk] = self._serve_hits.get(dk, 0) + 1
        row = self.store.current_row(domain, key)
        return {"value": row["value"] if row else None, "status": "DEPRECATED",
                "event_time": row["event_time"] if row else None,
                "quote": row["quote"] if row else None,
                "registry_status": "deprecated",
                "superseded_by": superseded_by, "deprecation_reason": reason}

    def close(self):
        self.store.close()

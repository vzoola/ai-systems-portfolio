# Memory Freshness Engine

> Part of [ai-systems-portfolio](../README.md). This is the core of a memory layer I run in production behind several AI agents. It is stdlib-only Python with 8 test suites (~200 checks). Run `./run_tests.sh`.

## Beyond the proof of core: the layers in this folder

| File | Layer | What it stops |
|---|---|---|
| `fact_store.py` | v1 bitemporal store | a stale re-mention overwriting a newer fact |
| `fact_store_v2.py` | v2 store + conflict queue | silent overwrites; same-key contradictions get queued, not resolved by guesswork |
| `quote_grounding.py` | G1–G4 grounding checks | an extracted value with no verbatim quote, wrong anchor terms, or the wrong referent |
| `whole_loop.py` | ingest → store → serve | serving a perishable value past its freshness horizon (`STALE`), a contested one (`CONTESTED`), or an undated echo of a retired value |
| `answer_gate.py` | serve-side gate | the model asserting anything whose status is not literally `CURRENT` ("the value field is a loaded gun; status is the safety") |
| `registry.py` + `registry.json` | key registry (sample: 10 of 78 keys) | referent rot: holds what each key MEANS, never its value |

The hearsay echo guard (`tests/test_hearsay_echo_guard.py`) closed a real hole an outside harness found: state A, correct to B, restate A, and a naive system serves A. When undated mentions disagree, the engine now serves no value and discloses all of them.

---

## Proof of core (the original design note)

A minimal, dependency-free (Python 3 stdlib `sqlite3` only) proof that an
AI-memory system can stop repeating a stale/corrected fact across sessions.

## The bug this exists to kill

"OTM Vapi credits" was topped up to **$30 on 2026-07-01**. A note written on
**2026-07-03** re-quoted the old pre-topup balance, **"$12.69"**. A naive
memory system that trusts "the text I saw most recently" returns $12.69 —
wrong, and dangerously confident about it.

## The fix: event time, not mention time

Every fact has two timestamps:

- `event_time` — when the fact became true in the real world (the topup
  happened 2026-07-01).
- `recorded_at` — when this system happened to hear about it (could be any
  time, including much later, including out of order).

**Currency is decided by `event_time`, never by `recorded_at` or by insertion
order.** A note mentioning an old balance is still describing something that
became true on an old date — restating it later doesn't make it newer.

## One current fact per key — enforced by the database, not by code

```sql
CREATE UNIQUE INDEX idx_facts_one_current
    ON facts(domain, fact_key)
    WHERE valid_to IS NULL;
```

This is a **partial unique index**: SQLite only enforces uniqueness among
rows where `valid_to IS NULL` ("current" rows). Historical rows are exempt
entirely, so history can hold as many old values as it wants, but the
database itself will physically refuse a second current row for the same
`(domain, fact_key)`. This isn't a discipline app code has to remember to
uphold — a stray `INSERT` that tries to sneak in a second current row raises
`sqlite3.IntegrityError` immediately (verified during build: raw SQL bypass
attempt was rejected with `UNIQUE constraint failed: facts.domain,
facts.fact_key`).

`current(domain, key)` is therefore a single indexed `WHERE valid_to IS NULL`
lookup — no scanning, no "pick the max timestamp" logic, no ambiguity.

## Supersede, never delete

`assert_fact(domain, key, value, event_time, source)` has three cases:

1. **No current row yet** → insert as current.
2. **New `event_time` >= current row's `event_time`** → this is genuinely
   newer (or a same-day correction) information. The current row is closed
   out (`valid_to` set to the new event_time) and the new value is inserted
   as the new current row.
3. **New `event_time` < current row's `event_time`** → this is a **stale
   re-mention** of an older fact (the $12.69 trap). It is inserted as a
   **non-current historical row** (`valid_to` = the current row's
   event_time, so it reads as "superseded before it was even recorded") and
   the real current row is **left completely untouched**.

Nothing is ever deleted. The old $12.69 value stays in `history()` forever,
correctly marked as superseded, with its `source` preserved — so the system
can always explain *why* it once believed something and *when* that stopped
being true (provenance). This also means the fix is auditable: you can prove
to a skeptic that the system saw $12.69 and consciously rejected it as
current, rather than never having seen it.

An identical value asserted with the same-or-older event time is a no-op
(only `recorded_at` is bumped, as a "seen again" breadcrumb) — this avoids
creating pointless duplicate history rows every time the same true fact is
casually restated.

## Provenance

Every row carries a `source` string (e.g. `"Vahe topped up"`,
`"re-quoted in a 7/3 note"`). Nothing is asserted anonymously — `history()`
lets you reconstruct not just what changed, but who/what said so and when.

## Schema

```
facts(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  domain      TEXT,      -- e.g. 'otm'
  fact_key    TEXT,      -- e.g. 'vapi_credits'
  value       TEXT,
  event_time  TEXT,      -- ISO 8601, when the fact became true
  recorded_at TEXT,      -- ISO 8601, when this row was written, default now()
  valid_to    TEXT NULL, -- NULL = current; else the event_time that superseded it
  source      TEXT
)
```

## API (`fact_store.py`)

- `FactStore(db_path)` — opens/creates the SQLite DB and schema.
- `assert_fact(domain, key, value, event_time, source=None)` — write path
  described above.
- `current(domain, key)` — the one current value, or `None`. Pure indexed SQL.
- `current_row(domain, key)` — full current row as a dict, or `None`.
- `history(domain, key)` — every version ever recorded, oldest `event_time`
  first, each with `value/event_time/recorded_at/valid_to/source`.
- `audit()` — safety-net health check: finds any `(domain, key)` with more
  than one current row (should be structurally impossible thanks to the
  partial unique index) plus totals (`total_facts`, `current_facts`,
  `distinct_keys`).

## How to run

```bash
./run_tests.sh          # all 8 suites, stdlib Python 3 only
# or one suite:
PYTHONPATH=. python3 tests/test_freshness.py
```

Each suite exits `0` if every check passes, `1` on the first failure. The test
reproduces the exact $12.69-after-topup scenario plus two more keys
(a SearchFit price increase, and a PatientCatch same-day correction) to
show the mechanism generalizes, then runs `audit()` as a final structural
check.

## Design non-goals (proof-of-core scope)

- No concurrency/locking beyond SQLite's defaults (single-writer assumed).
- No natural-language extraction of `event_time` from free text — callers
  are expected to supply ISO 8601 timestamps.
- No network, no external packages, no server — this is the core data
  model and invariant only, meant to be embedded in a larger system later.

---

## Origin (recorded 2026-07-07, Vahe's own account — provenance: operator-primary, recollection, no precise event date)

The famous trigger was the $12.69 bug: a stale note about a Vapi credit balance nearly overwrote the true $30, and "the AI confidently serving yesterday's fact" became the enemy this whole system exists to kill.

But the actual turn happened one question earlier. Vahe asked: **"How is it that you remember to tell me to go to bed every night, consistently?"** — and the answer was that the reminder doesn't come from model memory at all; it's a scheduled task, a deterministic structure that fires regardless of what any model remembers. Reliability came from STRUCTURE, not intelligence.

That is the founding principle of everything in this folder: don't ask the model to remember the truth — build a structure that cannot forget it. The registry, the freshness engine, the answer gate, the quote-grounding: all of it is that one observation, industrialized.

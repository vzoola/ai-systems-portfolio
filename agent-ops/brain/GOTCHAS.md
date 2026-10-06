# Gotchas (sample) — traps already paid for, as trap → fix pairs

## A test that greps SOURCE TEXT is satisfied by its own comment (2026-07-13)
Trap: a "the guard exists" test grepped the source for the guard's name — and passed because a comment mentioned it. Fix: test behavior (trip the guard), never text; confirm with a mutant that deletes the guard.

## A failing assertion PUBLISHES its operands (2026-07-14)
Trap: the test proving secrets don't leak printed the secret in its own failure message. Fix: assert on hashes/lengths, never on raw secret values.

## A permission ledger's cursor must be a LINE COUNT, never a timestamp (2026-07-13)
Trap: two approvals in the same second; the timestamp cursor skipped one. Fix: cursor = number of lines consumed in the append-only log.

## Threshold alerts lie about WHEN (2026-07-16)
Trap: a "money fire" alert fired three days after the real event because it compared against a stale aggregate. Fix: alerts carry the event's own timestamp, and relays never guess WHY.

## Correlated verifiers = fake consensus (2026-07-07)
Trap: five copies of the same model "agreeing" is one opinion. Fix: the reviewer must differ in vendor, context, or method (static review + executed repro + mutation run).

## Shell clock is UTC (2026-10-05)
Trap: an orchestrator read a UTC shell clock as local time and gave the human an 8-hour ETA for a 1-hour job. Fix: always `TZ=<local> date` before stating a time to a human.

# Critical rules (sample)

## 1. VERIFY, ALWAYS — never say "done" without proof (2026-06-13)
"Done" means evidence: a command's exit code, a file's real contents, a live URL's response. An agent's own summary of its work is a claim, not evidence — the orchestrator re-reads the disk before reporting.

## 2. TEST ONE BEFORE ANY BATCH (2026-06-14)
Anything that costs money or touches many records runs on ONE item first, is verified, and only then runs on the batch.

## 3. PROVE-THE-GATE — every guard ships with a proof-of-fire (2026-07-14)
A safety check that has never been seen to fire is a decoration. Every new guard ships with a test that trips it on purpose, and mutation testing confirms that deleting the guard makes a test fail.

## 4. FOUR-STATE ACCOUNTING (2026-07-14)
Every task is exactly one of: DONE (with evidence), BLOCKED (with the named blocker), HANDED OFF (with the recipient), or DROPPED (with the reason). "Mostly done" is not a state.

## 5. STOP ON AMBIGUITY (2026-07-16)
When two rules conflict, or a request has two readings with different consequences, the agent stops and asks. It never silently picks the reading that lets it keep moving.

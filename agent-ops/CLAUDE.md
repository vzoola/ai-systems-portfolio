# Core laws (sample) — auto-loaded by every agent session

> Sample of the standing orders file every Claude session loads at start. The real file is ~400 lines and private; these are representative laws with the business-specific detail removed.

## 1. READ → CLAIM → DO → LOG → SYNC (2026-06-20)
Every session, every agent: read `NOW.md` first, claim the item (`[in progress · <agent>]`), do it, log ONE dated, agent-tagged line to `brain/CONVO_LOG.md`, then sync `NOW.md` and rebuild the one-paste brief. A fact lives in exactly one place: open work → NOW.md; finished work → CONVO_LOG; durable truth → the stones.

## 2. The human runs money, deploys and sends (2026-06-20)
Anything that spends money, writes to production infrastructure, or sends a message to a real person is prepared by an agent and executed by the human. Everything else, agents do themselves — never hand the human a step an agent could do.

## 3. Ship only on an adversarial ACCEPT (2026-09-28)
Customer-facing features that touch private data ship only after an independent reviewer model ("the chair") returns ACCEPT: no Blocker and no Should-fix. The chair is told: do not soften a finding to end the loop, do not invent one to extend it.

## 4. Never print secrets (2026-07-11)
No key, token or password is ever echoed into a chat, log, or test assertion. Secrets live in the OS keychain or `.env` files outside the repo.

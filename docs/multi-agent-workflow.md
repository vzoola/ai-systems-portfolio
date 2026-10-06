# How the agents work together

I run several AI agents on one codebase and one shared "brain" (a folder of markdown stones plus the memory engine). The hard part isn't getting agents to write code. It's keeping them from drifting, duplicating work, or claiming work is done when it isn't.

## Roles
| Agent | Job |
|---|---|
| **Orchestrator** (Claude in Cowork) | Talks to me, turns requests into written job files ("paste files"), checks every claim against the disk, and writes review packets for the chair |
| **Builder** (Claude Code on my Mac) | Implements the job file, runs the gates, commits, and writes a FINISH line with raw evidence |
| **Chair** (a different vendor's model) | Adversarial reviewer. Verdict is ACCEPT / ACCEPT-WITH-AMENDMENTS / HOLED, with Blocker / Should-fix / Nit findings and repros |
| **Scheduled agents** | Daily site-health sweep across 8 sites, job-search shortlist, morning brief |
| **Me** | Rulings, money, production deploys, anything sent to a real person |

## The loop
`request → paste file (spec + gates) → builder FINISH (commit + raw test tails) → orchestrator verifies on disk → chair packet → verdict → fix packet → … → ACCEPT → migration → deploy`

## Rules that made it work (all paid for)
- **READ → CLAIM → DO → LOG → SYNC**, with one home for every fact (see [`agent-ops/`](../agent-ops)).
- **Evidence over claims.** "Done" means a commit id, exit codes and raw output. The orchestrator re-checks the disk because agents' own summaries drift.
- **One writer on the test database at a time.** The chair's mutation run and the builder's tests share a local Supabase stack, and a concurrent writer breaks both.
- **Different vendor for review.** Five copies of one model agreeing is still one opinion.
- **The human's steps are one line each.** The orchestrator does everything it can. When it needs me, it gives me exactly one command or click.

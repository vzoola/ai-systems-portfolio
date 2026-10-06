# Architecture: "AI Corp", the agent company that runs my businesses

AI Corp is the layer that runs my businesses day to day. It's organized like a company, with departments, each staffed by agents that have a defined role, tools and rules. It's built so every claim an agent makes is checked against reality before anyone trusts it.

## Control plane
- **A deterministic LangGraph graph with a Task Ledger.** No free-running "manager agent": we tested a hierarchical auto-manager, found it unreliable, and replaced it.
- **Job queue and dispatcher.** Jobs land in an inbox and get claimed and executed exactly once. That rule came from an incident where a hand-run job sitting in the inbox got executed twice.
- **The approval door ("bless").** Anything that touches money, sends to a real person, deploys, or has legal weight produces a one-tap approval card. Nothing fires without it, and every approval is written to an append-only ledger.
- **Alert router.** Instant pings only for money, a real person waiting, or a site down. Everything else folds into one daily digest. The router replaced about 16 pings a day from about 20 senders.

## The truth department (verification)
Agents' summaries drift, so verification is its own department, and it's independent of the agents doing the work:
- **Auditors / inspectors** re-derive status from reality (git log, file contents, live URLs, database counts). They never take an agent's word for it.
- **The witness rule:** a fix only turns green after an *independent* agent re-checks it from scratch. Skipping the witness isn't allowed.
- **Send-safety auditor:** fails closed before any outbound batch. It caught placeholder addresses (like `you@company.com`) sitting in a 1,155-address pool before anything was sent.
- **Silence alarms:** a lane that has been quiet for 36 hours pages, even when its jobs "ran." Quiet death is treated as a failure.

## The self-healing ladder (shown live on my ops dashboard)
| Step | What happens |
|---|---|
| 🟢 Flowing | receipts are fresh, nobody needed |
| 🔴 Sensor catches it | a watchdog spots the break and opens a ticket automatically, and the line pulses red with the ticket number |
| 🤖 Medic claims it | if a known playbook exists, a repair agent fixes it on its own |
| 🔧→🔍 Fix + witness | the fix comes with a receipt, then an independent agent re-checks it before it turns green |
| 🟠 Escalate | no playbook, or the witness bounced it twice: it becomes one action card for a human |
| 🧑‍⚖️ Owner gate | money, outbound messages to people, deploys and legal are never automated |

## Departments (the menu I sell from)
Executive · Reception / Front Desk · Sales · Marketing · Customer Success / Retention · Operations · Finance · Technology & Integrations · Data & Intelligence · Quality / Audit, plus industry toggles (Legal / Compliance and others). The same department model drives the [OfficeTechMate](architecture-officetechmate.md) product: a client picks their industry and sees only the departments that apply.

## Content assembly line (one real department, end to end)
`research (sourced facts) → strategist → storyteller / video (drafts only) → copy-law gate (blocks guarantees and fake claims) → owner bless → fire queue (double-post guard) → receipt + witness → silence alarm`

## What I learned building it
- Five copies of the same model agreeing is one opinion. Real verification needs a different vendor, context or method.
- A guard you've never seen fire is a decoration. Every guard ships with a test that trips it, and mutation testing proves the test needs it.
- The board can lie. A per-business count once silently dropped a ticket that belonged to no business, so the dashboard now reconciles its totals on screen and shows any mismatch.

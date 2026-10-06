# Vaheh Sahakian — AI systems portfolio

I've worked in IT for about 28 years, and for the last year I've been building with AI agents full time. I design the systems, set the rules, and direct the agents (Claude, plus a second-vendor model as reviewer) that write and test the code. Then I run the results in production for real users.

This repo holds the parts I can share publicly. The application code for my live products is private, and those products are linked below.

## What's here

### 1. [Memory Freshness Engine](memory-freshness-engine/) — runnable, tested
A memory layer that stops an AI from confidently repeating a fact that has since changed. It uses a bitemporal SQLite store where **event time beats mention time**. A partial unique index enforces "one current fact per key" in the database itself, and nothing is ever deleted. The serve gate refuses to assert anything that isn't `CURRENT` (stale, contested, unverified, deprecated). Stdlib Python, **8 suites / ~200 checks, all passing**: `./run_tests.sh`.
Architecture map: [docs/memory-system-map.html](docs/memory-system-map.html)

### 2. [Agent operating system](agent-ops/)
The shared brain several agents work from: one live work list, append-only logs, a rule ledger in which every ruling is word-cited, a **generated "constitution"** with a linter that finds contradicting, superseded or never-used rules, and the generator for my live ops dashboard. Sample data, real tooling.

### 3. [Case study: shipping a private family tree with an adversarial AI reviewer](docs/case-study-family-tree-privacy.md)
Defense in depth (RLS, a single allowlist gate, server-side redaction), plus a written launch rule: **ship only when a second-vendor reviewer returns ACCEPT**. Eight review passes caught inference leaks that no field-level check would find. Mutation testing proved each guard: 75 of 76 mutants killed and the last caught by a privacy test.

### 4. [How the agents work together](docs/multi-agent-workflow.md)
Orchestrator → builder → chair → owner, with evidence-over-claims gates.

### 5. Product architecture (reusable patterns, no private data)
- [AI Corp: the agent company that runs my businesses (truth department, self-healing ladder, approval door)](docs/architecture-ai-corp.md)
- [OfficeTechMate: AI teams for small businesses (builder, voice agents, multi-tenant portal)](docs/architecture-officetechmate.md)
- [Armenian Listing: directory + claim funnel + guarded releases](docs/architecture-directory-platform.md)
- [Patient Catch: AI overflow receptionist](docs/architecture-voice-receptionist.md)
- [Command Center board: sample output](agent-ops/command-center/_cc_site/index.html)

## Live products I built and run
| Product | What it is |
|---|---|
| [armenianlisting.com](https://armenianlisting.com) | Community business directory (~5,400 listings), memberships/sponsorships via Stripe, AI chat, and the private family tree (Roots) |
| [armeniandaycare.com](https://armeniandaycare.com) | Daycare directory (~4,300 listings) |
| [nearbyla.com](https://www.nearbyla.com) | LA local jobs / gigs marketplace |
| [officetechmate.com](https://officetechmate.com) | Small-business automation: AI team builder with live ROI and price |
| [patientcatch.com](https://patientcatch.com) | AI overflow receptionist for dental offices: the front desk answers first, and only missed calls go to the AI (launched, now paused) |

Plus a daily automated health sweep across 8 sites (sitemaps, canonicals, robots, Search Console indexing), voice agents on Vapi, and scheduled agents for ops.

## Contact
vahehs@gmail.com

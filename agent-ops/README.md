# Agent operating system ("the brain")

The shared memory and rulebook that several AI agents read and write. I organize it in stones. **The data here is sample data.** The real brain is private, but the structure and tooling are the same ones I use every day.

| Piece | What it is |
|---|---|
| `CLAUDE.md` | Core laws every agent session loads automatically |
| `NOW.md` | The single live list of open work. Agents claim items here before touching them |
| `brain/CRITICAL_RULES.md` | Hard operating rules (verify always, test one before a batch, prove-the-gate) |
| `brain/GOTCHAS.md` | Traps we already paid for, as trap → fix pairs |
| `brain/DECISIONS.md` | Per-project ruling ledger. Every row cites the owner's words |
| `brain/CONVO_LOG.md` | Append-only, dated, agent-tagged history |
| `brain/build_constitution.py` | Builds `CONSTITUTION.html`: one numbered, searchable view of all four rule stones. IDs are append-only, never reassigned. It's a generated view and never a second copy |
| `brain/constitution_linter.py` | Lints the rules themselves: contradiction candidates (keyword overlap with opposite polarity), superseded-but-unstruck rules, and rules nobody has ever cited |
| `command-center/` | Generator for the live ops dashboard. It parses `NOW.md` into a card board (needs-owner / open / watch / done). [Sample output](command-center/_cc_site/index.html) |

```bash
cd agent-ops/brain
python3 constitution_linter.py   # writes constitution_lint.json
python3 build_constitution.py    # writes ../CONSTITUTION.html
cd ../command-center && python3 _tools/gen_command_center.py   # writes _cc_site/index.html
```
All stdlib Python 3.

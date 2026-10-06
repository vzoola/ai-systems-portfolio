# Case study: shipping a private family tree with an adversarial AI reviewer

**Product:** Armenian Roots, a family-tree feature inside [armenianlisting.com](https://armenianlisting.com/roots). Families build a tree together, invite relatives by link, and print it.
**Stack:** Next.js (App Router, server actions), Supabase Postgres + Storage, Vercel.
**My role:** product owner and architect. I directed the AI agents that wrote the code, set the privacy rules, and decided what shipped.

The application code lives in a private repository. This page describes the design and the process.

## The privacy model (owner rulings)
- The **whole tree is private to invited members**, including deceased relatives. The only way in is an invite link.
- **Minors** appear to other members as first name only. Surname, year, photo and sex are removed.
- A person can **hide their own birth year**. Only they see it.
- Any outside visibility has to be a **per-tree opt-in, off by default**.

## How it is enforced (defense in depth)
1. **Database:** 8 `roots_*` tables, all with RLS on and no client grants. Only the server role touches them. A guard trigger protects hidden-year fields, and a unique index allows one claimed card per person per tree. Photos sit in a private bucket behind a RESTRICTIVE storage policy, and are validated and re-encoded to JPEG/WEBP under 1 MB.
2. **One gate function:** every read goes through a single `toVisiblePersons` gate. Redaction uses an **allowlist** (fields a first-name-only card may show), not a blocklist. A new column can't leak just because nobody remembered to strip it.
3. **Data leaves the server already gated.** The tree view, print view and search all get gated objects, and the UI never has to hide something it was already sent.

## The review loop: a "chair" model with a written launch rule
A second model from a different vendor reviewed every pass against a written rule: **ship only on ACCEPT, meaning no Blocker and no Should-fix.** Each review packet asked it to re-run its earlier attack scripts, write new ones, and run a **mutation check**: deliberately break each guard and confirm a test fails.

| Pass | Verdict | What it found |
|---|---|---|
| 1–5 | HOLED | Search leaks (result counts and `truncated` flags that depended on rows the viewer couldn't see), suggestion bells on redacted cards, photo-file edge cases (extra JPEG tables, WebP chunks) |
| 6 | **ACCEPT** → launched | 63 mutants: 62 killed, 1 caught by a privacy test, 0 survived |
| 7 | HOLED | New "birth order" feature: a redacted child's "2nd" narrowed their birth year to a 5-year range |
| 8 | HOLED | The fix hid the child's own number, but siblings showing "1st" and "3rd" still pointed at them (NEW-13). Full run: 76 mutants, 75 killed, 1 caught by a privacy test, 0 survived |
| 9 | in progress | Fix: if any sibling is hidden from the viewer, the server withholds the whole group's numbers, and the edit form drops the box |

**What this shows:** most of these leaks were **inference** leaks. No field was exposed directly. They came from ordering, gaps, counts and timing. A static "is the field null?" check would never have caught them. Catching them took an adversarial reviewer that writes and runs exploits, plus mutation testing to prove each guard actually fires.

## Gates on every release
`tsc --noEmit` exit 0 · `test:roots` run twice back-to-back on a growing database (168/168) · targeted + full mutation runs · a deploy script that runs a 21-check SEO/safety gate locally before it will call `vercel --prod` · migrations applied to production **before** the code that writes the new column is deployed.

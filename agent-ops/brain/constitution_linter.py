#!/usr/bin/env python3
"""
constitution_linter.py — the constitution's own lint pass. KICKOFF_CONSTITUTION_
HARDENING.md build item 1 (born 2026-07-16, u/_sam-i-am_ amendments, Vahe order
"lets implement the good guy's framework into ours").

READS the same 4 stones build_constitution.py reads. NEVER edits them — every
finding here is a HUMAN-REVIEW candidate, never an auto-judgment (the linter
obeys STOP-ON-AMBIGUITY on itself too — DECISIONS D-28).

Three checks:
  CONTRADICTION candidates — article pairs sharing strong keyword overlap
    (Jaccard over significant words) with OPPOSING polarity markers (never/
    forbidden/banned vs always/required/mandatory). Flagged for a human to
    read side by side — the linter never decides which one wins.
  SHADOWED rules — an article whose body reads as superseded/voided/reversed
    but the dead claim itself is NOT wrapped in ~~strikethrough~~, so it would
    still SHOW as a live rule to anyone skimming the page.
  NEVER-CITED rules — an article whose ID and no distinctive phrase from its
    title/body appears anywhere else in CONVO_LOG.md, GOTCHAS.md, or any
    KICKOFF_*.md. An unexercised rule is a decoration — the TCC trap (paid
    twice before anyone wrote it down where it would be re-read) is the case
    study the kickoff cites.

SCOPE NOTE on "since each rule's birth date" (kickoff wording): this
constitution's article IDs were minted for the first time on 2026-07-16 (the
extraction date), which is NOT the same as when the underlying rule was
actually authored — most GOTCHAS/CRITICAL_RULES/CLAUDE.md content predates
today by weeks. Gating citation search to "on/after first_seen" would compare
against the WRONG date for nearly every article and hide real citations,
producing a noisy, useless report. This linter instead searches the full
corpus (any date) for a citation, and reports the article's own embedded date
(`a["date"]`, extracted from its prose) alongside each never-cited finding so
a human reviewer can judge staleness themselves — a real date-windowed search
over thousands of un-timestamped prose lines is a rabbit hole disproportionate
to this pass, and a false "cited" read is cheaper than a false "never-cited"
scare.

Output: CROSS_AI_BRAIN/constitution_lint.json (raw findings, the source of
truth build_constitution.py reads to render the "⚠️ Lint" folder + per-row
chips on CONSTITUTION.html). Also POSTs ONE proposed-review triage card
summarizing the run via the local bless server (agents propose, never dispose
— same law as triage_stale_board.mjs) — skipped gracefully, not a failure, if
the server isn't reachable.

Run: python3 CROSS_AI_BRAIN/constitution_linter.py [--no-card]
Stdlib only (+ a plain urllib POST for the optional review card). $0.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import build_constitution as bc  # noqa: E402  (sibling script, reuses its stone parser)

LINT_OUT = HERE / "constitution_lint.json"
STATUS_API = "http://127.0.0.1:8771"

# ── shared text helpers ─────────────────────────────────────────────────────

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "be", "this", "that", "it", "its", "as", "with", "by", "at", "from", "was",
    "were", "will", "not", "but", "if", "then", "than", "so", "into", "over",
    "you", "your", "his", "her", "their", "our", "any", "all", "one", "two",
    "never", "always",  # polarity words — meaningful for polarity(), noise for overlap
}


def sig_words(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z0-9\-]{3,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


POLARITY_NEVER = re.compile(r"\b(never|forbidden|banned|prohibited|must not|refuses?|refused)\b", re.I)
POLARITY_ALWAYS = re.compile(r"\b(always|required|mandatory|must\b|standing law|non-negotiable)\b", re.I)


def polarity(text: str) -> str | None:
    n = bool(POLARITY_NEVER.search(text))
    a = bool(POLARITY_ALWAYS.search(text))
    if n and not a:
        return "never"
    if a and not n:
        return "always"
    return None  # mixed or neutral signal — not a clean pair to compare


# ── check 1: CONTRADICTION candidates ───────────────────────────────────────

def find_contradictions(articles: list[dict], min_overlap: int = 6, min_jaccard: float = 0.22) -> list[dict]:
    enriched = []
    for a in articles:
        full = f"{a['title']} {a['body']}"
        enriched.append((a, sig_words(full), polarity(full)))

    candidates = []
    for i in range(len(enriched)):
        a, wa, pa = enriched[i]
        if not pa or len(wa) < min_overlap:
            continue
        for j in range(i + 1, len(enriched)):
            b, wb, pb = enriched[j]
            if not pb or pb == pa or len(wb) < min_overlap:
                continue
            inter = wa & wb
            union = wa | wb
            if not union:
                continue
            jac = len(inter) / len(union)
            if jac >= min_jaccard and len(inter) >= min_overlap:
                candidates.append({
                    "a_id": a["id"], "a_title": a["title"], "a_polarity": pa,
                    "b_id": b["id"], "b_title": b["title"], "b_polarity": pb,
                    "shared_terms": sorted(inter)[:12],
                    "jaccard": round(jac, 3),
                })
    candidates.sort(key=lambda c: -c["jaccard"])
    return candidates


# ── check 2: SHADOWED (superseded-but-unstruck) ─────────────────────────────

# Deliberately NOT bare "void" — this codebase's own ledgers use VOID as a live,
# legitimate operation (append-only permission logs, STATUS_LOG resets), so a bare
# match false-positives on articles that merely DESCRIBE that feature (GOTCHAS
# G-35 was the case that caught this). Only the actual supersession markers count.
SUPERSEDE_RE = re.compile(r"(superseded|→\s*void|reversed (minutes|by|~)|never enforce this)", re.I)
STRIKE_RE = re.compile(r"~~.+?~~", re.S)


def find_shadowed(articles: list[dict]) -> list[dict]:
    out = []
    for a in articles:
        body = a["body"]
        if SUPERSEDE_RE.search(body) and not STRIKE_RE.search(body):
            m = SUPERSEDE_RE.search(body)
            out.append({
                "id": a["id"], "title": a["title"], "source": a["source_loc"],
                "matched": m.group(0),
                "why": "body reads as superseded/void/reversed but the dead claim is not wrapped in ~~strikethrough~~ — it will still render as a live article.",
            })
    return out


# ── check 3: NEVER-CITED ────────────────────────────────────────────────────

def load_corpus_text() -> dict[str, str]:
    files = [HERE / "CONVO_LOG.md", HERE / "GOTCHAS.md"]
    files += sorted(ROOT.glob("KICKOFF_*.md"))
    out = {}
    for f in files:
        if f.exists():
            out[str(f.relative_to(ROOT))] = f.read_text(encoding="utf-8", errors="ignore").lower()
    return out


def distinctive_phrase(title: str) -> str | None:
    # DECISIONS rows are "Section — decision text"; drop the section prefix so
    # the phrase is the actual ruling, not a project-name label repeated on
    # every row in that section (which would "cite" trivially against itself).
    t = title.split(" — ", 1)[-1]
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", t)
    if len(words) < 4:
        return None
    return " ".join(words[:8]).lower()


def find_never_cited(articles: list[dict], corpus: dict[str, str]) -> list[dict]:
    out = []
    for a in articles:
        phrase = distinctive_phrase(a["title"])
        own_file = a["source_loc"].split(" §", 1)[0]
        found_in = []
        for fname, text in corpus.items():
            if fname == own_file:
                continue  # citing yourself in your own defining stone isn't exercise
            if a["id"] and a["id"] in text:
                found_in.append(fname)
                continue
            if phrase and phrase in text:
                found_in.append(fname)
        if not found_in and phrase:  # no distinctive phrase = too short to judge, skip rather than false-flag
            out.append({
                "id": a["id"], "title": a["title"], "source": a["source_loc"],
                "embedded_date": a.get("date"),
                "checked_phrase": phrase,
            })
    return out


# ── review card ──────────────────────────────────────────────────────────────

def post_review_card(n_contra: int, n_shadow: int, n_uncited: int) -> None:
    total = n_contra + n_shadow + n_uncited
    body = json.dumps({
        "itemId": "constitution-lint",
        "day": bc.now_pt().strftime("%Y-%m-%d"),
        "reason": "constitution-lint-run",
        "receipt": (
            f"constitution_linter.py run: {n_contra} contradiction candidate(s), "
            f"{n_shadow} shadowed rule(s), {n_uncited} never-cited rule(s) — "
            f"{total} total. Human-review only, nothing auto-changed. "
            f"See CROSS_AI_BRAIN/constitution_lint.json and the ⚠️ Lint folder on CONSTITUTION.html."
        ),
        "actor": "constitution_linter",
    }).encode("utf-8")
    req = urllib.request.Request(
        STATUS_API + "/triage/propose", data=body,
        headers={"content-type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            j = json.loads(r.read())
            print(f"review card: {j.get('state', '?')} (proposalId={j.get('proposalId', '?')})")
    except (urllib.error.URLError, ConnectionRefusedError, OSError) as e:
        print(f"review card: SKIPPED — bless server not reachable at {STATUS_API} ({e})")


def main() -> None:
    no_card = "--no-card" in sys.argv

    articles = bc.load_articles()
    index = bc.load_index()  # not saved back — the linter never mints permanent IDs
    articles, _index = bc.assign_ids(articles, dict(index))

    contradictions = find_contradictions(articles)
    shadowed = find_shadowed(articles)
    corpus = load_corpus_text()
    never_cited = find_never_cited(articles, corpus)

    generated_at = bc.now_pt().strftime("%Y-%m-%d %H:%M PT")
    out = {
        "generated_at": generated_at,
        "counts": {
            "contradictions": len(contradictions),
            "shadowed": len(shadowed),
            "never_cited": len(never_cited),
        },
        "contradictions": contradictions,
        "shadowed": shadowed,
        "never_cited": never_cited,
    }
    LINT_OUT.write_text(json.dumps(out, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    print(f"✅ constitution_lint.json written ({generated_at})")
    print(f"   CONTRADICTION candidates: {len(contradictions)}")
    print(f"   SHADOWED (superseded-but-unstruck): {len(shadowed)}")
    print(f"   NEVER-CITED: {len(never_cited)}")

    if not no_card:
        post_review_card(len(contradictions), len(shadowed), len(never_cited))


if __name__ == "__main__":
    main()

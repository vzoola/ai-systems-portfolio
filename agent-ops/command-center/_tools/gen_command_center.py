#!/usr/bin/env python3
"""Generate _cc_site/index.html (the gated Command Center dashboard) from NOW.md.

Keeps the proven HTML/CSS/render template BYTE-FOR-BYTE and only regenerates the
DATA block (`const UPDATED` + `const ITEMS`). Run standalone or from
build_brain.command so the dashboard never goes stale.

Parsing rules (deliberately conservative — a busy-but-current board beats a
pretty-but-stale one, but we don't want to dump NOW.md's reference prose):
  🔴 NEEDS VAHE  -> status "vahe"
  🔥 LEADS       -> status "vahe", lane "Lead / Revenue"
  🟡 OPEN        -> status "open"  — ONLY bullets that carry an owner tag
                    ([Vahe]/[Cowork]/[Code]/[auto]); untagged = reference, skipped
  👀 WATCH       -> status "watch"
  ✅ RECENTLY DONE -> status "done"
Owner chips come from the [..] tags in each bullet; none found -> "info".
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOW_MD = ROOT / "NOW.md"
OUT = ROOT / "_cc_site" / "index.html"

SECTIONS = {
    "🔴 NEEDS VAHE": ("vahe", None),
    "🔥 LEADS": ("vahe", "Lead / Revenue"),
    "🟡 OPEN": ("open", None),
    "👀 WATCH": ("watch", None),
    "✅ RECENTLY DONE": ("done", None),
}
OWNER_TOKENS = [  # (regex, owner) — order = chip order
    (re.compile(r"\bVahe\b", re.I), "vahe"),
    (re.compile(r"\bCowork\b", re.I), "cowork"),
    (re.compile(r"\bCode\b"), "code"),
    (re.compile(r"\bauto(?:-task)?\b", re.I), "auto"),
]


def _clean(text: str) -> str:
    """Markdown -> safe plaintext (escaped for HTML + JS string)."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)   # links -> label
    text = text.replace("`", "")                            # drop code ticks
    text = re.sub(r"[*_#]", "", text)                        # stray md
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _card(raw: str, lane: str | None, status: str) -> dict | None:
    body = raw[2:].strip()  # drop leading "- "
    if not body:
        return None
    # owner chips from the [..] tags
    tags = " ".join(re.findall(r"\[[^\]]*\]", body))
    owners = [o for rx, o in OWNER_TOKENS if rx.search(tags)]
    # OPEN section: require an owner tag (skips reference/history bullets)
    if status == "open" and not owners:
        return None
    if not owners:
        owners = ["info"]
    # lead = first bold phrase IF it starts near the top, else the first few words
    # (avoids grabbing a mid-sentence bold as the title).
    m = re.search(r"\*\*(.+?)\*\*", body)
    if m and m.start() <= 60:
        lead, after = m.group(1), body[m.end():]
    else:
        words = body.split()
        lead, after = " ".join(words[:8]), " ".join(words[8:])
    strip_edges = r"[\s:—–\-`*]+"
    lead = re.sub(r"\[[^\]]*\]", "", lead).replace("`", "")
    lead = re.sub(strip_edges + r"$", "", lead.strip())
    after = re.sub(r"\[[^\]]*\]", "", after).replace("`", "")
    after = re.sub(r"^" + strip_edges, "", after.strip())
    # A done item (✅) parked in the OPEN section belongs under "done".
    if status == "open" and (body.lstrip().startswith("✅") or "✅" in lead):
        status = "done"
    sentence = re.split(r"(?<=[.;])\s", after, maxsplit=1)[0]
    if len(sentence) > 210:
        sentence = sentence[:207].rsplit(" ", 1)[0] + "…"
    txt = f"<b>{_clean(lead)}</b>"
    if sentence.strip():
        txt += " — " + _clean(sentence)
    return {"status": status, "lane": _clean(lane or "General"),
            "owner": owners, "txt": txt}


def parse(md: str) -> list[dict]:
    status, lane, items = None, None, []
    for line in md.splitlines():
        h = re.match(r"^##\s+(.*)", line)
        if h:
            head = h.group(1)
            status = None
            for key, (st, ln) in SECTIONS.items():
                if key in head:
                    status, lane = st, ln
                    break
            continue
        if status is None:
            continue
        # lane sub-header within a section: a line that STARTS with a bold phrase
        # (may carry trailing owner-tag text) and is not a bullet
        stripped = line.strip()
        sub = re.match(r"^\*\*(.+?)\*\*", stripped)
        if sub and not stripped.startswith(("-", ">")):
            lane = re.split(r"\s*[—(]", sub.group(1))[0].strip()
            continue
        if re.match(r"^- ", line):  # top-level bullet only
            card = _card(line, lane, status)
            if card:
                items.append(card)
    return items


def js_items(items: list[dict]) -> str:
    out = []
    for i in items:
        owners = ",".join(f'"{o}"' for o in i["owner"])
        out.append(
            f'  {{status:"{i["status"]}", lane:"{i["lane"]}", '
            f'owner:[{owners}], txt:"{i["txt"]}"}},'
        )
    return "\n".join(out)


def main() -> int:
    md = NOW_MD.read_text(encoding="utf-8")
    items = parse(md)
    today = datetime.date.today().isoformat()
    data_block = (
        f"// === DATA: auto-generated from NOW.md by _tools/gen_command_center.py. "
        f"Do NOT hand-edit; re-run the generator. ===\n"
        f'const UPDATED = "{today} · Code (auto)";\n'
        f"const ITEMS = [\n{js_items(items)}\n];"
    )
    html = OUT.read_text(encoding="utf-8")
    # Replace the existing DATA block: from the DATA comment to the ITEMS ']'.
    new_html, n = re.subn(
        r"// === DATA:.*?const ITEMS = \[.*?\n\];",
        lambda _m: data_block,
        html,
        count=1,
        flags=re.S,
    )
    if n != 1:
        raise SystemExit("ERROR: could not locate the DATA block to replace")
    OUT.write_text(new_html, encoding="utf-8")
    by = {}
    for i in items:
        by[i["status"]] = by.get(i["status"], 0) + 1
    print(f"generated {OUT} — {len(items)} cards {by} — UPDATED {today}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

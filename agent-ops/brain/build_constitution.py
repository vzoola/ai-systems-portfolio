#!/usr/bin/env python3
"""
build_constitution.py — generates CONSTITUTION.html (Projects root) from the
4 law stones. KICKOFF_AI_CONSTITUTION.md, born 2026-07-16, Vahe-blessed ("Okay,
do it").

HARD DESIGN LAW (Fable ruling, Vahe-blessed): this page is a GENERATED VIEW of
the existing stones — NEVER a hand-maintained second copy. Single source of
truth stays: CLAUDE.md · CROSS_AI_BRAIN/CRITICAL_RULES.md ·
CROSS_AI_BRAIN/GOTCHAS.md · CROSS_AI_BRAIN/DECISIONS.md. This script only
READS those 4 files and WRITES CONSTITUTION.html + constitution_index.json.
It never edits a stone.

Stdlib only, $0. Run: python3 build_constitution.py (also wired into
build_brain.command so every brain rebuild refreshes the constitution).

APPEND-ONLY NUMBERING (one-ID-for-life, same discipline as CARD_INDEX): each
article gets a permanent ID the first time its fingerprint is seen, persisted
in constitution_index.json (fingerprint -> {id, series, n, first_seen, title}).
IDs are NEVER reassigned or renumbered, even if the article's surrounding text
changes later — the fingerprint is keyed on the article's stable TITLE (its
section header, or for a DECISIONS row: project-section + date + decision
prefix), not its full body, so editing/expanding a law in place keeps its ID.
Series: L (CLAUDE.md) · C (CRITICAL_RULES) · G (GOTCHAS) · D (DECISIONS).

LAW FOR AGENTS: carve a new rule into its stone as it happens (CLAUDE.md is
already the rule) — the constitution view follows automatically on next rebuild.
Never hand-edit CONSTITUTION.html or constitution_index.json's article content.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    _PT = ZoneInfo("America/Los_Angeles")
except Exception:  # pragma: no cover - zoneinfo is stdlib py3.9+, this is belt-and-suspenders
    _PT = None

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
INDEX_PATH = HERE / "constitution_index.json"
OUT_PATH = ROOT / "CONSTITUTION.html"

SOURCES = [
    ("L", "Core laws", "CLAUDE.md (auto-loads every session)", ROOT / "CLAUDE.md"),
    ("C", "Critical rules", "CRITICAL_RULES.md (hard operating rules)", HERE / "CRITICAL_RULES.md"),
    ("G", "Gotchas", "GOTCHAS.md (traps we paid for, now law)", HERE / "GOTCHAS.md"),
    ("D", "Decisions", "DECISIONS.md (Vahe's rulings, by project)", HERE / "DECISIONS.md"),
]

SERIES_LABEL = {
    "L": "Core laws",
    "C": "Critical rules",
    "G": "Gotchas",
    "D": "Decisions",
}

DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
HEADER_SPLIT_RE = re.compile(r"^## (.+)$", flags=re.M)


def now_pt() -> datetime:
    return datetime.now(_PT) if _PT else datetime.now()


def clean_title(raw: str) -> str:
    t = re.sub(r"\s+", " ", raw or "").strip()
    return t


def norm_key(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def split_sections(text: str) -> list[tuple[str, str]]:
    """Split a markdown file on level-2 (## ) headers. Returns [(title, body), ...].
    Content before the first ## header (h1 title, preamble) is discarded — it is
    framing, not a rule-bearing unit."""
    parts = HEADER_SPLIT_RE.split(text)
    out = []
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        out.append((title, body))
    return out


def _first_date(*texts: str) -> str | None:
    for t in texts:
        m = DATE_RE.search(t or "")
        if m:
            return m.group(1)
    return None


def parse_header_stone(series: str, filename: str, text: str) -> list[dict]:
    """CLAUDE.md / CRITICAL_RULES.md / GOTCHAS.md: article = one ## section."""
    out = []
    for title, body in split_sections(text):
        ct = clean_title(title)
        out.append(
            {
                "series": series,
                "title": ct,
                "body": body,
                "date": _first_date(body, title),
                "source_loc": f"{filename} §{ct}",
                "key": ct,
            }
        )
    return out


def parse_decisions(filename: str, text: str) -> list[dict]:
    """DECISIONS.md: article = one table row (a single ruling)."""
    out = []
    for section_title, body in split_sections(text):
        st = clean_title(section_title)
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 3:
                continue
            head = cells[0].lower()
            if head in ("date",) or set(cells[0]) <= {"-", ":", ""}:
                continue  # header or separator row
            date_cell, decision_cell, source_cell = cells[0], cells[1], cells[2]
            decision_plain = re.sub(r"[*`~]", "", decision_cell)
            title = f"{st} — {decision_plain}"
            if len(title) > 140:
                title = title[:137] + "..."
            out.append(
                {
                    "series": "D",
                    "title": title,
                    "body": f"**{st}**\n\n{decision_cell}\n\n**Word-cite / source:** {source_cell}",
                    "date": _first_date(date_cell, decision_cell),
                    "source_loc": f"{filename} §{st}",
                    "key": f"{st}::{date_cell}::{decision_plain[:60]}",
                }
            )
    return out


def load_articles() -> list[dict]:
    articles: list[dict] = []
    for series, _label, filename, path in SOURCES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if series == "D":
            articles.extend(parse_decisions(filename, text))
        else:
            articles.extend(parse_header_stone(series, filename, text))
    return articles


def load_index() -> dict:
    if INDEX_PATH.exists():
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return {}


def save_index(idx: dict) -> None:
    INDEX_PATH.write_text(json.dumps(idx, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def derive_scope(a: dict) -> str:
    """Cheap scope only (KICKOFF_CONSTITUTION_HARDENING.md build item 3 — no
    precedence/effective-version machinery, revisit only if a real contradiction
    needs it). For L/C/G the series itself IS the scope (whole-stone, global —
    there's no cheaper/finer breakdown available without real judgment calls).
    For D, the DECISIONS.md section title is already sitting right there in the
    row's own title ("Section — decision...") — free to read, not a new lookup."""
    if a["series"] == "D":
        return a["title"].split(" — ", 1)[0].strip()
    return SERIES_LABEL[a["series"]]


def assign_ids(articles: list[dict], index: dict) -> tuple[list[dict], dict]:
    """Append-only ID assignment. Fingerprint = sha256(series::key)[:16], keyed
    on the article's STABLE identity (header title, or decision row's project+
    date+decision-prefix) — never on the full body, so amending a law in place
    does not mint a new ID. A fingerprint already in the index keeps its ID
    forever; a new fingerprint gets the next number in its series."""
    max_n = {"L": 0, "C": 0, "G": 0, "D": 0}
    for v in index.values():
        max_n[v["series"]] = max(max_n.get(v["series"], 0), v["n"])
    today = now_pt().strftime("%Y-%m-%d")

    for a in articles:
        fp = hashlib.sha256(f"{a['series']}::{a['key']}".encode("utf-8")).hexdigest()[:16]
        a["fingerprint"] = fp
        entry = index.get(fp)
        if entry is None:
            max_n[a["series"]] = max_n.get(a["series"], 0) + 1
            entry = {
                "id": f"{a['series']}-{max_n[a['series']]}",
                "series": a["series"],
                "n": max_n[a["series"]],
                "first_seen": today,
                "title": a["title"],
                "owner": None,  # no cheap owner signal exists yet in these stones — left honest, not guessed
            }
            index[fp] = entry
        # scope is a pure function of series+title — safe to recompute/backfill
        # every run (unlike id/first_seen, it carries no identity, so this never
        # "reassigns" anything the append-only law protects).
        entry["scope"] = derive_scope(a)
        entry.setdefault("owner", None)
        a["id"] = entry["id"]
        a["first_seen"] = entry["first_seen"]
        a["scope"] = entry["scope"]
    return articles, index


# ─────────────────────────────────────────────────────────────────────────────
# HTML render — Gmail model (Sample A, blessed 2026-07-16): header+search,
# always-visible legend strip, left sidebar folders w/ counts, main pane =
# email-style rows (colored ID chip + one-line title + date) that expand in
# place to full text + "Source: <file §> · carved <date>" line.
# ─────────────────────────────────────────────────────────────────────────────

SERIES_COLOR = {
    "L": "#3fd0c9",  # teal — core laws
    "C": "#ff6b6b",  # red — hard operating rules
    "G": "#f5c542",  # gold — traps paid for
    "D": "#8f7bff",  # violet — decisions
}


LINT_PATH = HERE / "constitution_lint.json"


def load_lint_map() -> dict[str, list[dict]]:
    """Read constitution_lint.json (written by constitution_linter.py, a sibling
    script — this file never runs the linter itself) and fold its three finding
    types into one {article_id: [ {type,label,detail}, ... ]} map for the
    renderer. Missing/unreadable lint file = no chips, not an error — the
    linter is optional tooling around the generator, never a dependency of it."""
    if not LINT_PATH.exists():
        return {}
    try:
        data = json.loads(LINT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, list[dict]] = {}

    def add(article_id: str, entry: dict) -> None:
        out.setdefault(article_id, []).append(entry)

    for c in data.get("contradictions", []):
        add(c["a_id"], {
            "type": "contradiction", "label": "⚠️ contradiction?",
            "detail": f"Shares strong overlap with {c['b_id']} (\"{c['b_title']}\") but opposite polarity ({c['a_polarity']} vs {c['b_polarity']}). Shared terms: {', '.join(c['shared_terms'])}. Jaccard {c['jaccard']}. Human review — not auto-resolved (STOP-ON-AMBIGUITY, D-28).",
        })
        add(c["b_id"], {
            "type": "contradiction", "label": "⚠️ contradiction?",
            "detail": f"Shares strong overlap with {c['a_id']} (\"{c['a_title']}\") but opposite polarity ({c['b_polarity']} vs {c['a_polarity']}). Shared terms: {', '.join(c['shared_terms'])}. Jaccard {c['jaccard']}. Human review — not auto-resolved (STOP-ON-AMBIGUITY, D-28).",
        })
    for s in data.get("shadowed", []):
        add(s["id"], {"type": "shadowed", "label": "🕸 shadowed", "detail": s["why"] + f" (matched: \"{s['matched']}\")"})
    for n in data.get("never_cited", []):
        add(n["id"], {
            "type": "never_cited", "label": "👻 unexercised",
            "detail": f"No other citation found for this rule's distinctive phrase (\"{n['checked_phrase']}\") in CONVO_LOG, GOTCHAS, or any KICKOFF_*.md. Embedded date: {n.get('embedded_date') or 'none stated'}. A rule nobody cites is a decoration.",
        })
    return out


def render_html(articles: list[dict], generated_at: str, lint_map: dict[str, list[dict]] | None = None) -> str:
    lint_map = lint_map or {}
    # newest-first (by first_seen, then by id within a tie) — inbox metaphor
    rows = sorted(articles, key=lambda a: (a["first_seen"], a["series"], a.get("n_sort", 0)), reverse=True)
    payload = json.dumps(
        [
            {
                "id": a["id"],
                "series": a["series"],
                "title": a["title"],
                "date": a.get("date") or "",
                "first_seen": a["first_seen"],
                "body": a["body"],
                "source": a["source_loc"],
                "lint": lint_map.get(a["id"], []),
            }
            for a in rows
        ],
        ensure_ascii=False,
    ).replace("</", "<\\/")
    lint_generated_at = None
    if LINT_PATH.exists():
        try:
            lint_generated_at = json.loads(LINT_PATH.read_text(encoding="utf-8")).get("generated_at")
        except (json.JSONDecodeError, OSError):
            lint_generated_at = None

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>⚖️ The AI Constitution</title>
<!--
  GENERATED FILE — do not hand-edit. Rebuilt by CROSS_AI_BRAIN/build_constitution.py
  from the 4 law stones (CLAUDE.md, CRITICAL_RULES.md, GOTCHAS.md, DECISIONS.md).
  Edit a LAW by editing its stone, then re-run the generator (or build_brain.command).
  Generated {generated_at}.
-->
<style>
  :root{{ --bg:#0a1830; --panel:#0e2242; --card:#12294d; --line:#1d3a63; --ink:#eaf2ff; --dim:#8fa3bd; --teal:#3fd0c9; --gold:#f5c542; }}
  *{{box-sizing:border-box}} html,body{{height:100%}}
  body{{margin:0;background:var(--bg);color:var(--ink);font-family:'Helvetica Neue',Arial,sans-serif;display:flex;flex-direction:column}}
  .strip{{background:var(--panel);border-bottom:1px solid var(--line);padding:10px 18px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}}
  .strip .brand{{font-weight:800;font-size:16px;letter-spacing:.5px;white-space:nowrap}}
  .strip .brand span{{color:var(--teal)}}
  #search{{flex:1;max-width:420px;background:var(--card);border:1px solid var(--line);border-radius:10px;color:var(--ink);padding:8px 12px;font-size:13.5px}}
  #search::placeholder{{color:var(--dim)}}
  .legend{{background:var(--panel);border-bottom:1px solid var(--line);padding:8px 18px;display:flex;gap:10px;flex-wrap:wrap;font-size:12px;color:var(--dim)}}
  .legend .chip{{display:inline-flex;align-items:center;gap:6px;background:var(--card);border-radius:999px;padding:3px 10px}}
  .legend .dot{{width:9px;height:9px;border-radius:50%;display:inline-block}}
  .body{{flex:1;display:flex;min-height:0}}
  .side{{width:220px;flex:none;background:var(--panel);border-right:1px solid var(--line);padding:12px 8px 40px;display:flex;flex-direction:column;gap:2px;overflow-y:auto}}
  .ni{{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:9px 12px;border-radius:10px;font-size:14px;cursor:pointer;color:var(--ink)}}
  .ni:hover{{background:var(--card)}}
  .ni.on{{background:var(--card);border:1px solid var(--teal)}}
  .ni .cnt{{font-size:11.5px;font-weight:700;border-radius:999px;padding:2px 8px;background:#1d3a63;color:var(--dim)}}
  .pane{{flex:1;min-width:0;overflow-y:auto;padding:10px 0 60px}}
  .row{{display:flex;align-items:flex-start;gap:12px;padding:11px 20px;border-bottom:1px solid var(--line);cursor:pointer}}
  .row:hover{{background:var(--card)}}
  .row .idchip{{flex:none;font-size:11.5px;font-weight:800;border-radius:8px;padding:3px 8px;color:#0a1830;white-space:nowrap;margin-top:2px}}
  .row .rtitle{{flex:1;font-size:14px;line-height:1.4}}
  .row .rdate{{flex:none;font-size:12px;color:var(--dim);white-space:nowrap;margin-top:2px}}
  .expand{{display:none;padding:4px 20px 20px 20px;background:#0c1e3c;border-bottom:1px solid var(--line);white-space:pre-wrap;font-size:13.5px;line-height:1.6;color:var(--ink)}}
  .expand .src{{margin-top:14px;font-size:12px;color:var(--dim);border-top:1px solid var(--line);padding-top:10px}}
  .row.open + .expand{{display:block}}
  .empty{{padding:40px;color:var(--dim);text-align:center}}
  .side .sep{{margin:10px 10px 4px;font-size:10.5px;letter-spacing:1.5px;color:#5f7292;text-transform:uppercase}}
  .lintchip{{display:inline-block;margin-left:8px;font-size:10.5px;font-weight:700;border-radius:999px;padding:1px 8px;white-space:nowrap;vertical-align:middle}}
  .lintchip-contradiction{{background:#3d1414;color:#ff9b9b}}
  .lintchip-shadowed{{background:#3a2e0a;color:#f5c542}}
  .lintchip-never_cited{{background:#241a3a;color:#c9b6ff}}
  .lintdetail{{margin-top:12px;padding:10px 12px;border-radius:8px;font-size:12.5px;line-height:1.5;white-space:normal}}
  .lintdetail-contradiction{{background:#2a1010;border:1px solid #4a1e1e;color:#ffb3b3}}
  .lintdetail-shadowed{{background:#2a2007;border:1px solid #4a3a10;color:#f5c542}}
  .lintdetail-never_cited{{background:#180f2a;border:1px solid #2e1e4a;color:#c9b6ff}}
  @media(max-width:760px){{ .side{{width:64px}} .ni span.lbl{{display:none}} .ni{{justify-content:center}} .ni .cnt{{display:none}} }}
</style>
</head>
<body>
  <div class="strip">
    <div class="brand">⚖️ THE AI <span>CONSTITUTION</span></div>
    <input id="search" type="text" placeholder="Search every law, rule, gotcha, decision…">
    <div style="font-size:12px;color:var(--dim)">generated {generated_at}{f' &nbsp;·&nbsp; lint {lint_generated_at}' if lint_generated_at else ''}</div>
  </div>
  <div class="legend">
    <span class="chip"><span class="dot" style="background:{SERIES_COLOR['L']}"></span>L = Core laws (CLAUDE.md, auto-loads every session)</span>
    <span class="chip"><span class="dot" style="background:{SERIES_COLOR['C']}"></span>C = Critical rules (hard operating rules)</span>
    <span class="chip"><span class="dot" style="background:{SERIES_COLOR['G']}"></span>G = Gotchas (traps we paid for, now law)</span>
    <span class="chip"><span class="dot" style="background:{SERIES_COLOR['D']}"></span>D = Decisions (Vahe's rulings, by project)</span>
  </div>
  <div class="body">
    <div class="side" id="side"></div>
    <div class="pane" id="pane"></div>
  </div>

<script>
var ARTICLES = {payload};
var SERIES_COLOR = {json.dumps(SERIES_COLOR)};
var SERIES_LABEL = {json.dumps(SERIES_LABEL)};
var currentFolder = 'all';

function isNewThisWeek(a) {{
  var d = new Date(a.first_seen + 'T00:00:00');
  var cutoff = new Date(); cutoff.setDate(cutoff.getDate() - 7);
  return d >= cutoff;
}}

function counts() {{
  var c = {{ new: 0, L: 0, C: 0, G: 0, D: 0, lint: 0, all: ARTICLES.length }};
  ARTICLES.forEach(function(a) {{
    c[a.series] = (c[a.series] || 0) + 1;
    if (isNewThisWeek(a)) c.new++;
    if (a.lint && a.lint.length) c.lint++;
  }});
  return c;
}}

function buildSide() {{
  var c = counts();
  var folders = [
    {{key:'new', label:'⭐ New this week', cnt:c.new}},
    {{key:'lint', label:'⚠️ Lint', cnt:c.lint}},
    {{sep:'By series'}},
    {{key:'L', label:'L — Core laws', cnt:c.L}},
    {{key:'C', label:'C — Critical rules', cnt:c.C}},
    {{key:'G', label:'G — Gotchas', cnt:c.G}},
    {{key:'D', label:'D — Decisions', cnt:c.D}},
    {{sep:''}},
    {{key:'all', label:'All laws', cnt:c.all}}
  ];
  var el = document.getElementById('side'), html = '';
  folders.forEach(function(f) {{
    if (f.sep !== undefined) {{ if (f.sep) html += '<div class="sep">' + f.sep + '</div>'; return; }}
    html += '<div class="ni' + (currentFolder === f.key ? ' on' : '') + '" data-folder="' + f.key + '">'
      + '<span class="lbl">' + f.label + '</span><span class="cnt">' + f.cnt + '</span></div>';
  }});
  el.innerHTML = html;
  el.querySelectorAll('.ni[data-folder]').forEach(function(ni) {{
    ni.addEventListener('click', function() {{ currentFolder = ni.getAttribute('data-folder'); render(); }});
  }});
}}

function esc(s) {{
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

function render() {{
  buildSide();
  var q = (document.getElementById('search').value || '').toLowerCase().trim();
  var list = ARTICLES.filter(function(a) {{
    if (currentFolder === 'new' && !isNewThisWeek(a)) return false;
    if (currentFolder === 'lint' && !(a.lint && a.lint.length)) return false;
    if (['L','C','G','D'].indexOf(currentFolder) !== -1 && a.series !== currentFolder) return false;
    if (q && (a.title + ' ' + a.body).toLowerCase().indexOf(q) === -1) return false;
    return true;
  }});
  var pane = document.getElementById('pane');
  if (!list.length) {{ pane.innerHTML = '<div class="empty">No laws match.</div>'; return; }}
  var html = '';
  list.forEach(function(a, i) {{
    var color = SERIES_COLOR[a.series] || '#888';
    var lintChips = (a.lint || []).map(function(l) {{
      return '<span class="lintchip lintchip-' + l.type + '">' + esc(l.label) + '</span>';
    }}).join('');
    html += '<div class="row" data-i="' + i + '">'
      + '<span class="idchip" style="background:' + color + '">' + esc(a.id) + '</span>'
      + '<span class="rtitle">' + esc(a.title) + lintChips + '</span>'
      + '<span class="rdate">' + esc(a.date || a.first_seen) + '</span>'
      + '</div>'
      + '<div class="expand"></div>';
  }});
  pane.innerHTML = html;
  pane.querySelectorAll('.row').forEach(function(row) {{
    row.addEventListener('click', function() {{
      var i = parseInt(row.getAttribute('data-i'), 10);
      var a = list[i];
      var already = row.classList.contains('open');
      pane.querySelectorAll('.row.open').forEach(function(r) {{ r.classList.remove('open'); }});
      if (!already) {{
        row.classList.add('open');
        var exp = row.nextElementSibling;
        var lintHtml = (a.lint || []).map(function(l) {{
          return '<div class="lintdetail lintdetail-' + l.type + '">' + esc(l.label) + ' — ' + esc(l.detail) + '</div>';
        }}).join('');
        exp.innerHTML = esc(a.body) + lintHtml + '<div class="src">Source: ' + esc(a.source)
          + (a.date ? ' · carved ' + esc(a.date) : ' · date not stated in source') + '</div>';
      }}
    }});
  }});
}}

document.getElementById('search').addEventListener('input', render);
render();
</script>
</body>
</html>
"""


def main() -> None:
    articles = load_articles()
    index = load_index()
    articles, index = assign_ids(articles, index)
    save_index(index)
    generated_at = now_pt().strftime("%Y-%m-%d %H:%M PT")
    lint_map = load_lint_map()
    html = render_html(articles, generated_at, lint_map)
    OUT_PATH.write_text(html, encoding="utf-8")
    by_series = {}
    for a in articles:
        by_series[a["series"]] = by_series.get(a["series"], 0) + 1
    flagged = sum(1 for a in articles if lint_map.get(a["id"]))
    print(
        f"✅ CONSTITUTION.html rebuilt ({generated_at}) — {len(articles)} articles: "
        + ", ".join(f"{k}={v}" for k, v in sorted(by_series.items()))
        + (f" · ⚠️ lint: {flagged} flagged" if lint_map else " · (no lint file — run constitution_linter.py)")
    )


if __name__ == "__main__":
    main()

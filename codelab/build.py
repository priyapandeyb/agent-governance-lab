"""Build the offline HTML for the two workshop documents in this folder.

    python codelab/build.py

Inputs (this folder):
  codelab.md    the step-by-step codelab, written in claat-compatible Markdown
                (metadata header, `## Step` + `Duration:`, `Positive`/`Negative`
                info boxes). Also exportable with `claat export codelab.md`.
  deep-dive.md  the long-form companion (full rationale and code walkthroughs).
Outputs (this folder):
  codelab.html, deep-dive.html   self-contained pages; images load from img/.

No network access is required. Only the `markdown` package is needed
(codelab/requirements.txt). This folder is git-ignored on purpose: the code
repository that Cloud Shell clones does not ship the codelab text.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
EXTENSIONS = ["fenced_code", "tables", "toc", "sane_lists", "md_in_html"]

# ---------------------------------------------------------------------------
# deep-dive.md: Mermaid blocks with a portable SVG equivalent are matched by
# CONTENT, not by order, so adding a Mermaid block elsewhere cannot shift later
# figures onto the wrong image. Blocks without an SVG keep their source visible.
# ---------------------------------------------------------------------------
FIGURES = [
    ("subgraph client",                          "architecture.svg"),
    ("actor C as Clinician\n",                   "request-journey.svg"),
    ("S0[00 env",                                "enforcement-rollout.svg"),
    ("Model Armor: sanitizeUserPrompt",          "content-screening.svg"),
    ("Destination registered in Agent Registry", "enforcement-rollout.svg"),
    ("IAP extension: DRY_RUN",                   "authorization-outcomes.svg"),
]


def _diagram(match: re.Match) -> str:
    code = match[1]
    name = next((svg for key, svg in FIGURES if key in code), None)
    escaped = html.escape(code)
    if name is None:
        # No portable SVG (for example the A2A sequence and the observability
        # pipeline): show the source expanded instead of a misleading image.
        return ("\n<details open><summary>Diagram (Mermaid source; renders on GitHub "
                "and in Mermaid-aware viewers)</summary><pre><code>" + escaped +
                "</code></pre></details>\n")
    return (f'\n<img src="img/{name}" alt="Workshop architecture and control flow" />\n'
            "<details><summary>Editable Mermaid diagram source</summary><pre><code>"
            + escaped + "</code></pre></details>\n")


def prepare_deep_dive(source: str) -> tuple[str, dict]:
    source = re.sub(r"```mermaid\n(.*?)```", _diagram, source, flags=re.S)
    return source, {}


# ---------------------------------------------------------------------------
# codelab.md: claat Markdown conventions -> HTML-friendly Markdown.
# ---------------------------------------------------------------------------
_DURATION = re.compile(r"^Duration:\s*(\d+):(\d{2}):(\d{2})\s*$", re.M)
_INFOBOX = re.compile(r"^(Positive|Negative)\n: (.+)$", re.M)


def prepare_codelab(source: str) -> tuple[str, dict]:
    # 1. Drop the claat metadata header: everything before the `# Title` line.
    title_at = re.search(r"^# .+$", source, re.M)
    if title_at is None:
        raise SystemExit("codelab.md has no '# Title' line")
    source = source[title_at.start():]

    # 2. `Duration: h:mm:ss` under each step heading -> a small badge, and a
    #    running total for the page header.
    total = {"minutes": 0}

    def duration(m: re.Match) -> str:
        minutes = int(m[1]) * 60 + int(m[2]) + (1 if int(m[3]) else 0)
        total["minutes"] += minutes
        return f'\n<p class="duration">About {minutes} min</p>'

    source = _DURATION.sub(duration, source)

    # 3. claat info boxes (`Positive` / `Negative` line, then `: text`) -> a
    #    callout whose inner Markdown is still rendered (md_in_html).
    source = _INFOBOX.sub(
        lambda m: f'<div class="note {m[1].lower()}" markdown="1">\n{m[2]}\n</div>', source)

    # 4. Cross-links are written against deep-dive.md so they work in Markdown
    #    viewers; the HTML build points at the HTML build.
    source = source.replace("](deep-dive.md#", "](deep-dive.html#").replace("](deep-dive.md)", "](deep-dive.html)")
    return source, {"minutes": total["minutes"]}


# ---------------------------------------------------------------------------
# Shared page shell
# ---------------------------------------------------------------------------
CSS = """
:root{--ink:#153448;--muted:#536c7a;--teal:#116b5b;--line:#dbe4e7;--navw:300px}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:#f6f8f9;color:var(--ink);font:16px/1.7 system-ui,-apple-system,sans-serif}
header{height:70px;position:sticky;top:0;z-index:2;background:#123247;color:white;display:flex;align-items:center;justify-content:space-between;padding:0 28px;gap:16px}
header strong{font-size:18px}header small{color:#b9d6cf;margin-left:12px;font-weight:400}
header nav a{color:#d7f6ea;margin-left:18px;font-size:14px}
nav.toc{position:fixed;top:70px;bottom:0;left:0;width:var(--navw);overflow:auto;padding:18px;background:white;border-right:1px solid var(--line)}
nav.toc a{display:flex;gap:12px;color:var(--muted);text-decoration:none;font-size:13px;line-height:1.5;padding:11px 12px;border-radius:7px}
nav.toc a[aria-current=step]{background:#e5f4ef;color:#075d4b;font-weight:700}
nav.toc span{font-variant-numeric:tabular-nums;color:#788e98}
main{margin-left:var(--navw);padding:42px 46px 100px;max-width:1410px}
.chapter{max-width:1020px;background:white;padding:38px 42px;border:1px solid var(--line);border-radius:12px;box-shadow:0 3px 18px #163b4910;margin:0 auto 30px}
.chapter[hidden]{display:none}
h1{font-size:34px;line-height:1.2;letter-spacing:-1px}h2{font-size:28px;line-height:1.25;margin-top:0}
h3{font-size:21px;margin-top:35px;line-height:1.4}h4{font-size:18px}p{margin:18px 0}
a{color:#09665c;text-underline-offset:3px}
img{display:block;max-width:100%;height:auto;margin:25px auto;border-radius:9px}
pre{position:relative;background:#122f3f;color:#e1edf3;overflow:auto;padding:26px 20px 20px;border-radius:8px;font:13px/1.65 ui-monospace,monospace}
code{font:0.88em ui-monospace,monospace;overflow-wrap:anywhere;background:#edf3f5;padding:2px 4px;border-radius:3px}
pre code{font:inherit;background:none;padding:0;overflow-wrap:normal}
.table-scroll{overflow:auto;margin:25px 0}table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;background:#eaf1f3;padding:13px}td{padding:13px;vertical-align:top;border-bottom:1px solid var(--line);min-width:120px}
blockquote{margin:24px 0;border-left:4px solid #bd862b;background:#fff6e6;padding:8px 20px;color:#564320}
details{background:#f1f5f6;padding:12px 18px;border-radius:8px}summary{cursor:pointer}
.duration{display:inline-block;margin:-6px 0 18px;padding:3px 10px;border-radius:999px;background:#eaf1f3;color:var(--muted);font-size:13px;font-weight:600}
.note{margin:24px 0;padding:6px 20px;border-radius:8px;border-left:5px solid}
.note.positive{background:#e8f5ee;border-color:#1e8e5a;color:#0f3d2a}
.note.negative{background:#fdeeea;border-color:#c4442f;color:#4d1d14}
.note p{margin:12px 0}
footer{position:fixed;bottom:0;right:0;left:var(--navw);background:#ffffffed;backdrop-filter:blur(10px);padding:16px 28px;display:flex;justify-content:space-between;gap:15px;border-top:1px solid var(--line)}
button{font:600 14px system-ui;border:1px solid #bdd2cd;border-radius:7px;padding:10px 18px;background:white;color:#155d4e;cursor:pointer}
button:disabled{opacity:.4;cursor:default}#next{background:var(--teal);color:white}
.copy{position:absolute;right:8px;top:6px;font-size:11px;padding:3px 8px;background:#2a4c5d;color:white;border-color:#597480}
#progress{align-self:center;color:var(--muted);font-size:13px}
.skip{position:absolute;left:-1000px}.skip:focus{left:10px;background:white;z-index:9;padding:8px}
@media(max-width:950px){:root{--navw:230px}main{padding:24px 20px 100px}.chapter{padding:25px}h1{font-size:30px}header small{display:none}}
@media(max-width:680px){header{height:auto;min-height:65px;padding:15px;font-size:12px}header strong{font-size:15px}
nav.toc{position:static;width:100%;max-height:190px;padding:8px;border-bottom:1px solid var(--line)}
main{margin:0;padding:18px 12px 90px}.chapter{padding:23px 18px}footer{left:0;padding:12px}h1{font-size:27px}h2{font-size:24px}pre{font-size:12px}td{min-width:150px}}
@media print{header,nav.toc,footer,.copy{display:none}main{margin:0;padding:0}
.chapter,.chapter[hidden]{display:block!important;box-shadow:none;border:0;padding:0;break-before:page}
pre{white-space:pre-wrap;color:black;background:#eee}details{display:none}}
"""

JS = """
const chapters=[...document.querySelectorAll('.chapter')], links=[...document.querySelectorAll('nav.toc a')], label=document.body.dataset.unit||'Chapter';let index=0;
document.querySelectorAll('main img').forEach(img=>{const a=document.createElement('a');a.href=img.src;a.target='_blank';a.rel='noopener';a.title='Open image at full size';img.before(a);a.append(img)});
document.querySelectorAll('table').forEach(t=>{const w=document.createElement('div');w.className='table-scroll';t.before(w);w.append(t)});
function show(n,scroll=true){index=Math.max(0,Math.min(chapters.length-1,n));chapters.forEach((c,i)=>c.hidden=i!==index);links.forEach((a,i)=>{if(i===index)a.setAttribute('aria-current','step');else a.removeAttribute('aria-current')});document.querySelector('#prev').disabled=index===0;document.querySelector('#next').disabled=index===chapters.length-1;document.querySelector('#progress').textContent=`${label} ${index+1} of ${chapters.length}`;if(scroll)window.scrollTo(0,0)}
function route(){const id=decodeURIComponent(location.hash.slice(1));let n=chapters.findIndex(c=>c.dataset.id===id);if(n<0){const target=document.getElementById(id);n=target?chapters.indexOf(target.closest('.chapter')):0;show(n);if(target)target.scrollIntoView();return}show(n)}
window.addEventListener('hashchange',route);document.querySelector('#prev').onclick=()=>location.hash=chapters[index-1].dataset.id;document.querySelector('#next').onclick=()=>location.hash=chapters[index+1].dataset.id;
document.querySelectorAll('pre').forEach(pre=>{const b=document.createElement('button');b.className='copy';b.textContent='Copy';b.onclick=async()=>{try{await navigator.clipboard.writeText(pre.querySelector('code')?.textContent||pre.textContent);b.textContent='Copied';setTimeout(()=>b.textContent='Copy',1300)}catch{b.textContent='Select text to copy'}};pre.append(b)});route();
"""


def split_chapters(body: str, first_title: str) -> tuple[list[str], list[str]]:
    """Split rendered HTML on <h2>. A leading chunk that holds only the <h1>
    (the codelab) is folded into the first step instead of becoming a page."""
    chunks = re.split(r"(?=<h2\b)", body)
    if len(chunks) > 1 and not re.sub(r"<h1[^>]*>.*?</h1>", "", chunks[0], flags=re.S).strip():
        chunks[1] = chunks[0] + chunks[1]
        chunks = chunks[1:]
    sections, nav = [], []
    for i, chunk in enumerate(chunks):
        heading = re.search(r'<h2 id="([^"]+)">(.*?)</h2>', chunk, re.S)
        sid = heading[1] if heading else "welcome"
        title = re.sub("<[^>]+>", "", heading[2]) if heading else first_title
        sections.append(f'<section class="chapter" data-id="{sid}" id="chapter-{sid}">{chunk}</section>')
        nav.append(f'<a href="#{sid}" data-index="{i}"><span>{i + 1:02}</span>{title}</a>')
    return sections, nav


def render(source_name: str, output_name: str, prepare, *, page_title: str, unit: str,
           header_links: list[tuple[str, str]], first_title: str) -> None:
    source = (HERE / source_name).read_text()
    source, meta = prepare(source)
    md = markdown.Markdown(extensions=EXTENSIONS)
    body = md.convert(source)
    sections, nav = split_chapters(body, first_title)

    missing = sorted({src for src in re.findall(r'<img[^>]+src="([^"]+)"', body)
                      if "://" not in src and not (HERE / src).exists()})
    if missing:
        print(f"WARNING {source_name}: missing images: {', '.join(missing)}")

    subtitle = ""
    if meta.get("minutes"):
        h, m = divmod(meta["minutes"], 60)
        subtitle = f"<small>About {h} h {m:02} min · {len(sections)} steps</small>"
    links_html = "".join(f'<a href="{href}">{text}</a>' for text, href in header_links)
    output = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
              "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
              f"<title>{html.escape(page_title)}</title><style>{CSS}</style></head>"
              f'<body data-unit="{unit}"><a class="skip" href="#main">Skip to content</a>'
              f"<header><div><strong>Agent Governance Lab</strong>{subtitle}</div><nav>{links_html}</nav></header>"
              f'<nav class="toc" aria-label="{unit}s">{"".join(nav)}</nav>'
              f'<main id="main">{"".join(sections)}</main>'
              '<footer><button id="prev">← Previous</button><span id="progress" aria-live="polite"></span>'
              f'<button id="next">Next →</button></footer><script>{JS}</script></body></html>')
    (HERE / output_name).write_text(output)
    extra = f", about {meta['minutes']} min" if meta.get("minutes") else ""
    print(f"Built {output_name}: {len(sections)} {unit.lower()}s{extra}; all assets are local.")


def _github_slug(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    return re.sub(r"\s+", "-", slug.strip())


def build_github_codelab() -> None:
    """Export codelab.md to a GitHub-native Markdown file at the repo root (CODELAB.md)."""
    raw = (HERE / "codelab.md").read_text()
    title_at = re.search(r"^# .+$", raw, re.M)
    if title_at is None:
        raise SystemExit("codelab.md has no '# Title' line")
    body = raw[title_at.start():]

    # Collect H2 sections and their durations for the Table of Contents.
    toc_rows: list[str] = []
    total_mins = 0
    for m in re.finditer(r"^## (.+)\n(?:Duration:\s*(\d+):(\d{2}):(\d{2}))?", body, re.M):
        sec_title = m.group(1).strip()
        mins = (int(m.group(2)) * 60 + int(m.group(3))) if m.group(2) else 0
        total_mins += mins
        dur_str = f"{mins} min" if mins else "—"
        toc_rows.append(f"| [{sec_title}](#{_github_slug(sec_title)}) | {dur_str} |")

    toc_block = (
        "\n> **Hands-on Workshop Codelab** · Follow every step below in order in Google Cloud Shell. "
        "Each task includes the architecture and product explanation, copy-pasteable commands, "
        "expected output, verification checks, and troubleshooting.\n\n"
        "### Table of Contents\n\n"
        "| Step | Duration |\n"
        "|---|---|\n"
        + "\n".join(toc_rows)
        + "\n| [Frequently asked questions (FAQ)](#frequently-asked-questions-faq) | Reference |\n"
        "| [Troubleshooting reference](#troubleshooting-reference) | Reference |\n"
    )

    # Insert TOC right after the H1 title line.
    first_nl = body.find("\n")
    body = body[: first_nl + 1] + toc_block + body[first_nl + 1 :]

    # Convert Duration: h:mm:ss -> GitHub-friendly duration badge.
    def _fmt_dur(m: re.Match) -> str:
        mins = int(m[1]) * 60 + int(m[2]) + (1 if int(m[3]) else 0)
        return f"*⏱️ Estimated time: {mins} minutes*"

    body = _DURATION.sub(_fmt_dur, body)

    # Convert claat Positive / Negative callouts into GitHub Markdown Alerts.
    def _fmt_alert(m: re.Match) -> str:
        kind = "TIP" if m[1] == "Positive" else "IMPORTANT"
        return f"> [!{kind}]\n> {m[2]}"

    body = _INFOBOX.sub(_fmt_alert, body)

    # Rewrite image paths and deep-dive links to repo-root paths.
    body = body.replace("(img/healthcare-gateway-hero.png)", "(docs/images/healthcare-gateway-hero.png)")
    body = body.replace("(img/", "(docs/diagrams/")
    body = body.replace("](deep-dive.md", "](codelab/deep-dive.md")
    body = re.sub(r"\n{3,}", "\n\n", body)

    out_path = HERE.parent / "CODELAB.md"
    out_path.write_text(body.strip() + "\n")
    print(f"Built {out_path.name} at repo root ({total_mins} min total across {len(toc_rows)} steps).")


if __name__ == "__main__":
    render("codelab.md", "codelab.html", prepare_codelab,
           page_title="Agent Governance Lab · Codelab", unit="Step",
           header_links=[("Deep dive", "deep-dive.html")],
           first_title="Overview")
    render("deep-dive.md", "deep-dive.html", prepare_deep_dive,
           page_title="Agent Governance Lab · Deep dive", unit="Section",
           header_links=[("Step-by-step codelab", "codelab.html")],
           first_title="Welcome")
    build_github_codelab()


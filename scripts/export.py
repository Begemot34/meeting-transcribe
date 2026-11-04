"""Turns transcript.md into one self-contained HTML (screenshots embedded as base64) and a PDF (fpdf2).

Output: <out_dir>/<folder name>.html and .pdf. A summary section before the transcript heading is optional.
Headings are recognized in the meeting language (labels.py).
"""
import argparse
import base64
import hashlib
import html
import io
import re
import shlex
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from labels import LABELS, detect, to_sec  # noqa: E402

PALETTE = ["#2f5fc4", "#c25a2a", "#1d8a7a", "#8257c9", "#b8871a", "#c24a7c", "#5f8a2a", "#5b6b7f"]

ap = argparse.ArgumentParser()
ap.add_argument("task_dir")
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--quality", type=int, default=72)
ap.add_argument("--no-pdf", action="store_true")
args = ap.parse_args()

task = Path(args.task_dir).resolve()
md_path = task / "transcript.md"
if not md_path.exists():
    sys.exit(f"[transcribe] {md_path} not found — run merge or auto first")
src = md_path.read_text(encoding="utf-8")
LANG = detect(src)
L = LABELS[LANG]
cache = task / ".export_cache"
cache.mkdir(exist_ok=True)


def esc(s):
    return html.escape(s, quote=False)


def inline(s):
    s = esc(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*([^*]+)\*(?!\w)", r"<i>\1</i>", s)
    return s


def image_file(rel):
    """Resized JPEG of a screenshot in the cache. Only images inside the task folder: the HTML gets shared,
    so a hand-edited path must not pull in an arbitrary file from the disk."""
    p = (task / rel).resolve()
    if not p.is_relative_to(task):
        sys.exit(f"[transcribe] image outside {task}: {rel} — keep screenshots in the task folder")
    if not p.exists():
        sys.exit(f"[transcribe] image not found: {p}")
    key = hashlib.sha1(f"{p.relative_to(task)}|{args.width}|{args.quality}".encode()).hexdigest()[:12]
    out = cache / f"{p.stem}-{key}.jpg"
    if not out.exists() or out.stat().st_mtime < p.stat().st_mtime:
        img = Image.open(p).convert("RGB")
        if img.width > args.width:
            img = img.resize((args.width, round(img.height * args.width / img.width)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=args.quality, optimize=True)
        out.write_bytes(buf.getvalue())
    return out


def image_data(rel):
    return "data:image/jpeg;base64," + base64.b64encode(image_file(rel).read_bytes()).decode()


# --- parse: header, summary (markdown), utterances ---
lines = src.split("\n")
title = lines[0].lstrip("# ").strip()
meta, speakers_line = "", ""
for ln in lines[1:6]:
    if ln.startswith(f"{L['date']}:"):
        meta = ln
    if ln.startswith(f"{L['speakers']}:"):
        speakers_line = ln

# the transcript starts at its heading; "---" only in files from before the heading existed
i_tr = next((i for i, ln in enumerate(lines) if ln.strip() == f"## {L['transcript']}"), None)
if i_tr is None:
    i_tr = next((i for i, ln in enumerate(lines) if ln.strip() == "---"), 5)
i_sum = next((i for i, ln in enumerate(lines) if ln.strip() == f"## {L['summary']}"), i_tr)
summary_md = lines[i_sum + 1:i_tr]
body = lines[i_tr + 1:]

UTT = re.compile(r"^### (.+?) · \[(\d+(?::\d\d)+) — (\d+(?::\d\d)+)\]$")
IMG = re.compile(r"^!\[(.*?)\]\((.+?)\)$")
items = []  # ("u", name, a, b, text) | ("img", label, path)
cur = None
for ln in body:
    m = UTT.match(ln.strip())
    if m:
        cur = ["u", m.group(1), m.group(2), m.group(3), []]
        items.append(cur)
        continue
    m = IMG.match(ln.strip())
    if m:
        items.append(("img", m.group(1), m.group(2)))
        cur = None
        continue
    if cur is not None and ln.strip() and ln.strip() != "---":
        cur[4].append(ln.strip())

# talk time per speaker
talk = {}
for it in items:
    if it[0] == "u":
        talk[it[1]] = talk.get(it[1], 0) + to_sec(it[3]) - to_sec(it[2])
order = sorted(talk, key=lambda n: -talk[n])
color = {n: PALETTE[k % len(PALETTE)] for k, n in enumerate(order)}




# anchors: an id per utterance, unique even when two start in the same second
anchors, utt_starts = {}, []
for it in items:
    if it[0] == "u":
        aid = f"t{it[2].replace(':', '')}"
        n = anchors.get(aid, 0)
        anchors[aid] = n + 1
        it.append(aid if n == 0 else f"{aid}-{n}")
        utt_starts.append((to_sec(it[2]), it[5]))


def anchor_for(sec):
    """The utterance that contains the moment, or the last one that started before it."""
    best = utt_starts[0][1]
    for s, aid in utt_starts:
        if s > sec:
            break
        best = aid
    return best


TIME = r"\d{1,2}:\d\d(?::\d\d)?"


def link_times(s):
    # "**00:00–02:31**", "**1:02:10–1:05:00**" and "(19:39)" in the summary → links to utterances
    if not utt_starts:
        return s

    def rep(m):
        return f'<a class="ts" href="#{anchor_for(to_sec(m.group(1)))}">{m.group(0)}</a>'
    return re.sub(rf"(?<![\d:])({TIME})(?:–{TIME})?(?![\d:])", rep, s)


def render_md(md_lines):
    """Summary markdown → (title, lead html, [(heading, body html)]): each ### section is one block."""
    title, blocks = "", [["", []]]
    out, para, lst = blocks[0][1], [], None

    def li_html(x):
        x = link_times(inline(x))
        if x.startswith("[ ] ") or x.startswith("[x] "):
            done = x[1] == "x"
            return f'<li class="task{" done" if done else ""}"><span class="box" aria-hidden="true"></span>{x[4:]}</li>'
        return f"<li>{x}</li>"

    def flush_para():
        nonlocal para
        if para:
            out.append("<p>" + link_times(inline(" ".join(para))) + "</p>")
            para = []

    def flush_list():
        nonlocal lst
        if lst:
            tag, its = lst
            cls = ' class="tasks"' if all(x.startswith(("[ ] ", "[x] ")) for x in its) else ""
            out.append(f"<{tag}{cls}>" + "".join(li_html(x) for x in its) + f"</{tag}>")
            lst = None

    for ln in md_lines:
        s = ln.rstrip()
        if not s.strip():
            flush_para()
            flush_list()
            continue
        if s.startswith("### "):
            flush_para(); flush_list()
            blocks.append([inline(s[4:]), []])
            out = blocks[-1][1]
        elif s.startswith("## "):
            flush_para(); flush_list()
            title = inline(s[3:])
        elif re.match(r"^- ", s):
            flush_para()
            if not lst or lst[0] != "ul":
                flush_list(); lst = ("ul", [])
            lst[1].append(s[2:])
        elif re.match(r"^\d+\. ", s):
            flush_para()
            if not lst or lst[0] != "ol":
                flush_list(); lst = ("ol", [])
            lst[1].append(re.sub(r"^\d+\. ", "", s))
        elif lst and s.startswith("  "):
            lst[1][-1] += " " + s.strip()
        else:
            flush_list()
            if s.startswith("**") and para:  # "**MM:SS–MM:SS** — topic" starts a new paragraph
                flush_para()
            para.append(s.strip())
    flush_para(); flush_list()
    lead = "\n".join(blocks[0][1])
    return title, lead, [(h, "\n".join(body)) for h, body in blocks[1:]]


sum_title, sum_lead, sum_blocks = render_md(summary_md)
has_summary = bool(sum_lead.strip() or sum_blocks)
summary_html = ""
if has_summary:
    rows = "".join(f'<div class="row"><h3>{h}</h3><div class="body">{body}</div></div>' for h, body in sum_blocks)
    summary_html = (f'<section class="summary"><h2>{sum_title or esc(L["summary"])}</h2>'
                    f'<div class="lead">{sum_lead}</div>{rows}</section>')

# --- transcript ---
tr = []
prev = None
n_img = 0
for it in items:
    if it[0] == "img":
        n_img += 1
        data = image_data(it[2])
        tr.append(f'<figure class="shot" id="img{n_img}"><img src="{data}" '
                  f'alt="{html.escape(L["screenshot"] + " " + it[1], quote=True)}">'
                  f'<figcaption>{L["screen_at"]} {esc(it[1])}</figcaption></figure>')
        prev = None
        continue
    _, name, a, b, text, aid = it
    txt = inline(" ".join(text))
    who = "" if prev == name else (f'<span class="nm"><i style="background:{color[name]}"></i>{esc(name)}</span>')
    tr.append(f'<div class="utt{" cont" if prev == name else ""}" id="{aid}"><div class="who">{who}'
              f'<a class="time" href="#{aid}">{a}</a></div><p class="tx">{txt}</p></div>')
    prev = name

top = max(talk.values(), default=1) or 1
people = "".join(
    f'<li><span class="nm"><i style="background:{color[n]}"></i>{esc(n)}</span>'
    f'<span class="bar"><span style="width:{100 * talk[n] / top:.0f}%"></span></span>'
    f'<span class="mins">{talk[n] / 60:.1f} {L["min"]}</span></li>'
    for n in order)
meta_items = "".join(f"<span>{inline(part.strip())}</span>" for part in meta.split("|") if part.strip()) if meta else ""
CLI = Path(__file__).resolve().parent.parent / "transcribe"


def project_url():
    """The tool's public GitHub page, from the git remote of this checkout; None if there is none.
    The HTML gets shared, so only a plain github.com address goes in: never a token from the remote URL
    or an internal Git host."""
    import subprocess
    from urllib.parse import urlparse
    try:
        url = subprocess.run(["git", "-C", str(CLI.parent), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    url = re.sub(r"^git@([^:]+):", r"https://\1/", url).removesuffix(".git")
    u = urlparse(url)
    path = u.path.strip("/")
    if u.scheme != "https" or u.hostname != "github.com" or not re.fullmatch(r"[\w.-]+/[\w.-]+", path):
        return None
    return f"https://github.com/{path}"


PROJECT_URL = project_url()
made_with = (f'{L["made_with"]} <a href="{html.escape(PROJECT_URL, quote=True)}">meeting-transcribe</a>'
             if PROJECT_URL else f'{L["made_with"]} meeting-transcribe')
# the HTML is sent around: no absolute paths (they show the user name and folders), just the folder name
summary_run = f"./transcribe summarize {shlex.quote(task.name)} --llm claude"
summary_cmd = f"{summary_run}   # in the meeting-transcribe folder; or --llm codex, opencode, local, api"
COPY_ICON = ('<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" '
             'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" '
             'rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>')
no_summary_html = (f'<section class="nosum"><p>{esc(L["no_summary"])}</p><div class="cmd"><pre>{esc(summary_cmd)}</pre>'
                   f'<button class="copy" type="button" title="Copy" aria-label="Copy" '
                   f'data-copy="{html.escape(summary_run, quote=True)}">{COPY_ICON}</button></div></section>')

doc = f"""<!doctype html>
<html lang="{LANG}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>
/* Meeting minutes as a document: one text column, section labels in a left rail, hairline rules, no cards. */
:root{{--bg:#ffffff;--ink:#15181d;--mut:#5d6570;--faint:#8b929c;--rule:#e4e7eb;--tint:#f3f5f8;--accent:#1b4fb5;
 --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
 --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;--rail:168px}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#121417;--ink:#e7e9ec;--mut:#a3aab4;--faint:#79818c;--rule:#262a30;--tint:#1b1e23;--accent:#7ea6f2;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#121417;--ink:#e7e9ec;--mut:#a3aab4;--faint:#79818c;--rule:#262a30;--tint:#1b1e23;--accent:#7ea6f2;color-scheme:dark}}
*{{box-sizing:border-box}}
html{{scroll-behavior:smooth}}
@media (prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.6 var(--sans);-webkit-font-smoothing:antialiased}}
.wrap{{max-width:980px;margin:0 auto;padding-block:56px 96px;padding-left:max(24px,env(safe-area-inset-left,0px));padding-right:max(24px,env(safe-area-inset-right,0px))}}
a{{color:var(--accent)}}
a:focus-visible,button:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
header{{padding-bottom:28px;border-bottom:1px solid var(--rule)}}
h1{{font-size:30px;line-height:1.2;font-weight:650;letter-spacing:-.01em;margin:0 0 12px;text-wrap:balance;max-width:28ch}}
.meta{{display:flex;flex-wrap:wrap;gap:4px 20px;color:var(--mut);font-size:13.5px;font-variant-numeric:tabular-nums}}
.people{{list-style:none;margin:22px 0 0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:8px 40px}}
.people li{{display:grid;grid-template-columns:minmax(0,1fr) 64px 58px;align-items:center;gap:12px;font-size:13.5px}}
.nm{{display:inline-flex;align-items:center;gap:8px;font-weight:600;min-width:0}}
.nm i{{flex:0 0 8px;height:8px;border-radius:2px}}
.people .nm{{font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.bar{{height:3px;background:var(--rule)}}
.bar span{{display:block;height:100%;background:var(--mut)}}
.mins{{color:var(--mut);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}
section.summary,section.nosum{{padding-block:32px;border-bottom:1px solid var(--rule)}}
h2{{font-size:13px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);margin:0 0 14px}}
.lead{{font-size:17.5px;line-height:1.55;max-width:68ch;margin-bottom:8px}}
.lead p{{margin:0 0 10px}}
.row{{padding-top:22px;margin-top:22px;border-top:1px solid var(--rule)}}
.row h3{{font-size:15px;font-weight:650;line-height:1.4;margin:0 0 10px;color:var(--ink);text-wrap:balance}}
.body{{max-width:70ch;min-width:0}}
.body p{{margin:0 0 10px}}
.body ol,.body ul{{margin:0;padding-left:1.35em}}
.body li{{margin:0 0 9px}}
.body li::marker{{color:var(--faint);font-variant-numeric:tabular-nums}}
ul.tasks{{list-style:none;padding-left:0}}
li.task{{display:grid;grid-template-columns:20px minmax(0,1fr);gap:8px}}
.box{{width:13px;height:13px;margin-top:5px;border:1.5px solid var(--faint);border-radius:3px}}
li.done .box{{background:var(--accent);border-color:var(--accent)}}
code{{background:var(--tint);border-radius:4px;padding:1px 5px;font:13px/1.4 var(--mono)}}
a.ts{{color:var(--accent);text-decoration:none;font-variant-numeric:tabular-nums}}
a.ts:hover{{text-decoration:underline}}
section.nosum{{color:var(--mut);font-size:14px}}
section.nosum p{{margin:0 0 10px;max-width:70ch}}
.cmd{{position:relative;max-width:820px}}
.cmd pre{{margin:0;background:var(--tint);border-radius:6px;padding:10px 48px 10px 12px;font:12.5px/1.55 var(--mono);white-space:pre-wrap;overflow-wrap:anywhere;color:var(--ink)}}
.copy{{position:absolute;top:6px;right:6px;width:30px;height:30px;display:flex;align-items:center;justify-content:center;border:0;border-radius:5px;background:transparent;color:var(--mut);cursor:pointer}}
.copy:hover{{background:var(--rule);color:var(--ink)}}
.copy.ok{{color:var(--accent)}}
.transcript{{padding-top:32px}}
.utt{{display:grid;grid-template-columns:var(--rail) minmax(0,1fr);gap:24px;padding:10px 0 2px;scroll-margin-top:24px}}
.utt.cont{{padding-top:0}}
.utt:target .tx{{background:var(--tint);box-shadow:0 0 0 6px var(--tint)}}
.who{{display:flex;flex-direction:column;gap:1px;min-width:0;padding-top:1px}}
.who .nm{{font-size:13.5px;line-height:1.45}}
.time{{color:var(--faint);font:12px/1.5 var(--mono);text-decoration:none;font-variant-numeric:tabular-nums;padding-left:16px}}
.time:hover{{color:var(--accent)}}
.tx{{margin:0;max-width:70ch;overflow-wrap:anywhere}}
figure.shot{{margin:18px 0 18px calc(var(--rail) + 24px)}}
figure.shot img{{display:block;width:100%;height:auto;cursor:zoom-in;border-radius:4px}}
figcaption{{font-size:12px;color:var(--faint);margin-top:6px;font-variant-numeric:tabular-nums}}
.lightbox{{display:none;position:fixed;inset:0;background:rgba(8,10,14,.9);z-index:9;align-items:center;justify-content:center;cursor:zoom-out}}
.lightbox.on{{display:flex}}
.lightbox img{{max-width:96vw;max-height:94vh}}
footer{{color:var(--faint);font-size:12px;margin-top:56px;padding-top:16px;border-top:1px solid var(--rule)}}
footer a{{color:var(--mut)}}
@media (max-width:700px){{
 .wrap{{padding-block:32px 64px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}}
 h1{{font-size:24px}} .lead{{font-size:16px}}
 .utt{{grid-template-columns:minmax(0,1fr);gap:6px}}
 .who{{flex-direction:row;align-items:baseline;gap:10px}} .time{{padding-left:0}}
 figure.shot{{margin-left:0}}
}}
@media print{{
 body{{font-size:11.5px}} .wrap{{max-width:none;padding:0}}
 .lightbox,section.nosum{{display:none!important}}
 .utt,figure.shot,.row{{break-inside:avoid}} h2,h3{{break-after:avoid}} section.summary{{break-after:page}}
}}
</style></head>
<body><div class="wrap">
<header><h1>{esc(title)}</h1><div class="meta">{meta_items}</div><ul class="people">{people}</ul></header>
{summary_html or no_summary_html}
<section class="transcript"><h2>{L['transcript']}</h2>
{''.join(tr)}
</section>
<div class="lightbox" id="lb"><img alt=""></div>
<script>
const lb=document.getElementById('lb'),li=lb.querySelector('img');
document.querySelectorAll('figure.shot img').forEach(i=>i.onclick=()=>{{li.src=i.src;lb.classList.add('on')}});
lb.onclick=()=>lb.classList.remove('on');
document.addEventListener('keydown',e=>{{if(e.key==='Escape')lb.classList.remove('on')}});
document.querySelectorAll('button.copy').forEach(b=>b.onclick=async()=>{{
  const t=b.dataset.copy, icon=b.innerHTML;
  try{{await navigator.clipboard.writeText(t)}}catch(_){{
    const a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();document.execCommand('copy');a.remove()}}
  b.classList.add('ok');b.innerHTML='<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
  setTimeout(()=>{{b.classList.remove('ok');b.innerHTML=icon}},1200)}});
</script>
<footer>{made_with}</footer>
</div></body></html>
"""

out_html = task / f"{task.name}.html"
out_html.write_text(doc, encoding="utf-8")
print(f"html: {out_html} ({out_html.stat().st_size / 1e6:.1f} MB)")
if not has_summary:
    print(f"no summary yet — to add one:\n  {summary_cmd}")




# --- PDF: drawn directly with fpdf2 (pure Python, no browser) ---

def font_dir():
    """DejaVu Sans covers Latin and Cyrillic; it ships with matplotlib, which pyannote already installs."""
    import matplotlib
    return Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"


def rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def plain(s):
    return re.sub(r"`([^`]+)`", r"\1", s)


def summary_blocks(md_lines):
    """Markdown of the summary → [(kind, marker, text)]: h2, h3, li, p; wrapped lines are joined."""
    out = []
    for ln in md_lines:
        st = ln.rstrip()
        if not st.strip():
            out.append(None)
        elif st.startswith("### "):
            out.append(("h3", "", st[4:]))
        elif st.startswith("## "):
            out.append(("h2", "", st[3:]))
        elif m := re.match(r"^(- \[[ x]\] |- |\d+\. )(.*)", st):
            marker = {"- ": "•", "- [ ] ": "☐", "- [x] ": "☑"}.get(m.group(1), m.group(1).strip())
            out.append(("li", marker, m.group(2)))
        elif out and out[-1] and out[-1][0] in ("li", "p") and not st.startswith("**"):  # "**MM:SS** — topic" starts anew
            out[-1] = (out[-1][0], out[-1][1], out[-1][2] + " " + st.strip())
        else:
            out.append(("p", "", st.strip()))
    return [b for b in out if b]


def build_pdf(pdf_path):
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_margins(16, 16, 16)
    pdf.set_auto_page_break(True, margin=16)
    fd = font_dir()
    pdf.add_font("DejaVu", "", str(fd / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(fd / "DejaVuSans-Bold.ttf"))
    pdf.add_font("DejaVu", "I", str(fd / "DejaVuSans-Oblique.ttf"))
    pdf.set_title(title)
    pdf.add_page()
    mut, fg = (107, 107, 112), (29, 29, 31)

    def rich(text, size=10.5, h=5.4):
        """A paragraph with **bold**; wraps like normal text."""
        for k, part in enumerate(re.split(r"\*\*(.+?)\*\*", plain(text))):
            pdf.set_font("DejaVu", "B" if k % 2 else "", size)
            pdf.write(h, part)
        pdf.ln(h)

    pdf.set_font("DejaVu", "B", 18)
    pdf.set_text_color(*fg)
    pdf.multi_cell(0, 8, title, align="L")
    pdf.ln(1)
    if meta:
        pdf.set_font("DejaVu", "", 9.5)
        pdf.set_text_color(*mut)
        pdf.multi_cell(0, 5, meta, align="L")
    pdf.ln(1)
    pdf.set_font("DejaVu", "", 9.5)
    for n in order:  # speakers with talk time; a chip never breaks across lines
        mins = f"{talk[n] / 60:.1f} {L['min']}"
        if pdf.get_x() + pdf.get_string_width(f"● {n} {mins}") > pdf.w - pdf.r_margin:
            pdf.ln(5.5)
        pdf.set_text_color(*rgb(color[n]))
        pdf.write(5, "● ")
        pdf.set_text_color(*fg)
        pdf.write(5, f"{n} ")
        pdf.set_text_color(*mut)
        pdf.write(5, mins + "     ")
    pdf.ln(9)

    blocks = summary_blocks(summary_md)
    if blocks:
        pdf.set_text_color(*fg)
        for kind, marker, text in blocks:
            if kind == "h2":
                pdf.set_font("DejaVu", "B", 15)
                pdf.multi_cell(0, 8, plain(text), align="L")
                pdf.ln(1)
            elif kind == "h3":
                pdf.ln(2.5)
                pdf.set_font("DejaVu", "B", 12)
                pdf.set_text_color(*rgb("#1b4fb5"))
                pdf.multi_cell(0, 6, plain(text), align="L")
                pdf.set_text_color(*fg)
                pdf.ln(0.5)
            elif kind == "li":
                pdf.set_font("DejaVu", "", 10.5)
                left = pdf.l_margin
                pdf.cell(7, 5.4, marker)
                pdf.set_left_margin(left + 7)
                rich(text)
                pdf.set_left_margin(left)
                pdf.set_x(left)
                pdf.ln(0.8)
            else:
                rich(text)
                pdf.ln(1.5)
        pdf.add_page()

    pdf.set_font("DejaVu", "B", 15)
    pdf.set_text_color(*fg)
    pdf.multi_cell(0, 8, L["transcript"], align="L")
    pdf.ln(2)
    full_w = pdf.w - pdf.l_margin - pdf.r_margin
    prev = None
    page_h = pdf.h - pdf.t_margin - pdf.b_margin - 8  # room for the caption
    for it in items:
        if it[0] == "img":
            img = image_file(it[2])
            w, h = Image.open(img).size
            img_w = full_w if full_w * h / w <= page_h else page_h * w / h  # a tall (phone) frame fits one page
            img_h = img_w * h / w
            if pdf.get_y() + img_h + 6 > pdf.h - pdf.b_margin:
                pdf.add_page()
            pdf.image(str(img), w=img_w)
            pdf.set_font("DejaVu", "", 8)
            pdf.set_text_color(*mut)
            pdf.cell(0, 5, f"{L['screen_at']} {it[1]}", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
            prev = None
            continue
        _, name, a, b, text, _ = it
        if pdf.get_y() + 16 > pdf.h - pdf.b_margin:
            pdf.add_page()
        if prev != name:
            pdf.ln(1.5)
            pdf.set_font("DejaVu", "B", 10.5)
            pdf.set_text_color(*rgb(color[name]))
            pdf.write(5.4, name + "  ")
            pdf.set_font("DejaVu", "", 8.5)
            pdf.set_text_color(*mut)
            pdf.write(5.4, f"{a} — {b}")
            pdf.ln(5.6)
        pdf.set_font("DejaVu", "", 10.5)
        pdf.set_text_color(*fg)
        pdf.multi_cell(0, 5.2, plain(" ".join(text)), align="L")
        pdf.ln(1)
        prev = name
    pdf.ln(4)
    pdf.set_font("DejaVu", "", 8)
    pdf.set_text_color(*mut)
    pdf.write(4.5, f"{L['made_with']} ")
    pdf.write(4.5, "meeting-transcribe", link=PROJECT_URL or "")
    pdf.output(str(pdf_path))


if not args.no_pdf:
    out_pdf = task / f"{task.name}.pdf"
    build_pdf(out_pdf)
    print(f"pdf: {out_pdf} ({out_pdf.stat().st_size / 1e6:.1f} MB)")

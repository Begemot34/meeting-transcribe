"""The docs follow the code: links and anchors resolve, every option is in COMMANDS(.ru).md, every MT_* variable is
in SETTINGS(.ru).md — and the docs list nothing the code does not have."""
import ast
import re
import unicodedata

import pytest
from conftest import ROOT, SCRIPTS

DOCS = sorted(ROOT.glob("*.md"))
PAIRS = [("COMMANDS.md", "COMMANDS.ru.md"), ("SETTINGS.md", "SETTINGS.ru.md"), ("README.md", "README.ru.md")]
SCRIPT_OF = {"frames": "frames", "badges": "badges", "strip": "strip", "merge": "merge", "summarize": "summarize",
             "export": "export"}
BASH_PARSER = {"auto": "cmd_auto", "asr": "cmd_asr", "setup": "cmd_setup", "doctor": "parse_language"}
INTERNAL = {"summarize": {"--check", "--list"}}  # used by setup to test a backend, not by people
FLAG = re.compile(r"(?<![\w-])(--?[a-z][a-z-]*)")


def prose(md):
    """The text without fenced code blocks: examples there are not links."""
    return re.sub(r"^(```|~~~).*?^\1", "", md, flags=re.S | re.M)


def slug(heading):
    """GitHub's anchor of a heading: lowercase, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
    return "".join(c for c in text if c in " -_" or unicodedata.category(c)[0] in "LN").replace(" ", "-")


def anchors(path):
    seen, out = {}, set()
    for h in re.findall(r"^#{1,6} +(.+?) *#*$", prose(path.read_text(encoding="utf-8")), re.M):
        s = slug(h)
        out.add(s if s not in seen else f"{s}-{seen[s]}")
        seen[s] = seen.get(s, 0) + 1
    return out


def links(path):
    text = re.sub(r"`[^`\n]*`", "", prose(path.read_text(encoding="utf-8")))
    return re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(r'(?:src|srcset|href)="([^"]+)"', text)


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_links_and_anchors_resolve(doc):
    bad = []
    for link in links(doc):
        if re.match(r"[a-z]+:", link):
            continue
        target, _, anchor = link.partition("#")
        path = (doc.parent / target).resolve() if target else doc
        if not path.exists():
            bad.append(link)
        elif anchor and path.suffix == ".md" and anchor not in anchors(path):
            bad.append(link)
    assert not bad


def sections(path):
    """{command: text of its section} from the "## name" / "### name" headings of COMMANDS(.ru).md, plus the
    overview table as "" — commands without options have only a row there."""
    md = path.read_text(encoding="utf-8")
    parts = re.split(r"^#{2,3} +`?([\w-]+)`? *$", md, flags=re.M)
    out = {"": parts[0]}
    out.update(zip(parts[1::2], parts[2::2], strict=True))
    return out


def documented(text):
    """Options in the first column of the tables of a section."""
    rows = re.findall(r"^\| *(`[^|]*?(?:\\\|[^|]*?)*`[^|]*?) *\|", text, re.M)
    return {f for row in rows for code in re.findall(r"`([^`]+)`", row) for f in FLAG.findall(code)}


def python_options(script):
    tree = ast.parse((SCRIPTS / f"{script}.py").read_text(encoding="utf-8"))
    return {a.value for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument"
            for a in node.args if isinstance(a, ast.Constant) and str(a.value).startswith("-")}


def bash_function(name):
    src = (ROOT / "transcribe").read_text(encoding="utf-8")
    m = re.search(rf"^{name}\(\) \{{.*?^\}}$", src, re.S | re.M)
    assert m, f"no function {name} in transcribe"
    return m.group(0)


def bash_options(cmd):
    """Options of the `case "$1" in` argument loop of the function that parses them."""
    body = bash_function(BASH_PARSER[cmd])
    loop = re.search(r'while \[ \$# -gt 0 \]; do\s+case "\$1" in(.*?)\n\s+esac\s+done', body, re.S)
    assert loop, f"{BASH_PARSER[cmd]}: the argument loop is not where this test looks for it"
    arms = re.findall(r"^\s+([-\w|]+)\)", loop.group(1), re.M)
    return {o for arm in arms for o in arm.split("|") if o.startswith("-")}


def code_options():
    out = {cmd: python_options(s) - INTERNAL.get(cmd, set()) for cmd, s in SCRIPT_OF.items()}
    out.update({cmd: bash_options(cmd) for cmd in BASH_PARSER})
    return out


@pytest.mark.parametrize("doc", [p for pair in PAIRS[:1] for p in pair])
def test_every_option_is_documented(doc):
    secs = sections(ROOT / doc)
    for cmd, opts in code_options().items():
        assert opts, cmd
        text = secs.get(cmd) or ""
        if cmd == "doctor":  # one row in the overview: `doctor [--language X]`
            text = "| " + next(r for r in secs[""].splitlines() if "`doctor" in r).split("|")[1] + " |"
        assert documented(text) == opts, f"{doc}: {cmd}"


def test_internal_options_exist():
    for cmd, opts in INTERNAL.items():
        assert opts <= python_options(SCRIPT_OF[cmd])


def variables_in_code():
    files = [ROOT / "transcribe", *SCRIPTS.glob("*.py")]
    return {v for f in files for v in re.findall(r"\bMT_[A-Z0-9_]+\b", f.read_text(encoding="utf-8"))}


@pytest.mark.parametrize("doc", PAIRS[1])
def test_every_variable_is_documented(doc):
    rows = re.findall(r"^\| *`(MT_[A-Z0-9_]+)`", (ROOT / doc).read_text(encoding="utf-8"), re.M)
    assert set(rows) == variables_in_code()


@pytest.mark.parametrize(("en", "ru"), PAIRS)
def test_translations_link_to_each_other(en, ru):
    assert f"[Русский]({ru})" in (ROOT / en).read_text(encoding="utf-8").splitlines()[0]
    assert f"[English]({en})" in (ROOT / ru).read_text(encoding="utf-8").splitlines()[0]

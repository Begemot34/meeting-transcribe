"""Meeting summary via an LLM: inserts a summary section into out_dir/transcript.md before the transcript.

  summarize.py <out_dir> --llm claude|codex|opencode|local|api [--model M]
  summarize.py --check --llm opencode --model provider/model   — one short request: does the model answer?
  summarize.py --list --llm opencode                           — the models OpenCode offers to this run

  claude — Claude Code CLI (`claude -p`, no tools, no MCP servers), on the user's own subscription or key;
  codex  — Codex CLI (`codex exec`, read-only sandbox, no shell, no apps or MCP servers);
  opencode — OpenCode (`opencode run`): a model the user set up in it, provider/model in MT_OPENCODE_MODEL;
           isolated from their OpenCode setup, see opencode_run.py;
  local  — a model on this machine through Ollama (default gemma4:26b), nothing leaves the machine;
  api    — any server with an OpenAI-compatible Chat Completions API: OpenAI, OpenRouter, a company gateway,
           LM Studio, vLLM. URL MT_LLM_BASE_URL, key MT_LLM_API_KEY, model MT_LLM_MODEL.
Summary rules: scripts/summary_prompt.md (the agent uses the same file via SKILL.md).
The summary is written in the meeting language, headings from labels.py.
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import opencode_run  # noqa: E402
from labels import LABELS, detect, meeting_language  # noqa: E402

HERE = Path(__file__).resolve().parent
PROMPT = HERE / "summary_prompt.md"
LOCAL_MODEL = "gemma4:26b"  # the smallest model whose summaries held up in our comparison, see README
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
if not OLLAMA.startswith("http"):
    OLLAMA = f"http://{OLLAMA}"
LOOPBACK = ("localhost", "127.0.0.1", "::1")
RAW = None  # out_dir/raw/summary_answer.md, set in main
BACKENDS = ["claude", "codex", "opencode", "local", "api"]
TIMEOUT = int(os.environ.get("MT_LLM_TIMEOUT", "1800"))  # seconds; a local model on a long meeting needs minutes


def die(msg):
    sys.exit(f"[transcribe] {msg}")


def log(msg):
    print(f"[transcribe] {msg}", file=sys.stderr, flush=True)


def find_bin(name):
    path = shutil.which(name) or next((str(p) for p in [Path.home() / ".local/bin" / name] if p.exists()), None)
    if not path:
        die(f"`{name}` CLI not found: install it or choose another --llm ({', '.join(BACKENDS)})")
    return path


def run_cli(cmd, prompt, what):
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        die(f"{what} gave no answer in {TIMEOUT} s (MT_LLM_TIMEOUT)")
    if r.returncode:
        die(f"{what} failed with exit code {r.returncode}: {(r.stderr or r.stdout).strip()[-500:]}")
    return r.stdout


def supports(binary, *args, flag):
    r = subprocess.run([binary, *args, "--help"], capture_output=True, text=True)
    return flag in r.stdout + r.stderr


def run_claude(system, transcript, model):
    # The transcript is untrusted text: no tools at all, no MCP servers of the user's setup (Slack, mail, …), and
    # with --safe-mode none of their hooks, plugins, skills or CLAUDE.md either; the login still works.
    claude = find_bin("claude")
    safe = ["--safe-mode"] if supports(claude, flag="--safe-mode") else []
    if not safe:
        log("this Claude Code has no --safe-mode: your hooks and plugins stay on; update it: claude update")
    cmd = [claude, "-p", *safe, "--tools", "", "--strict-mcp-config", "--disallowedTools", "mcp__*",
           "--no-session-persistence"] + (["--model", model] if model else [])
    return run_cli(cmd, f"{system}\n{transcript}", "claude -p")


def run_codex(system, transcript, model):
    with tempfile.TemporaryDirectory() as tmp:
        last = Path(tmp) / "last.md"
        # an empty folder, a read-only sandbox, none of the user's config.toml (MCP servers, profiles), and no
        # shell, hooks, apps, browser, sub-agents or images: it only writes text. The login still works.
        codex = find_bin("codex")
        off = ["shell_tool", "unified_exec", "hooks", "apps", "plugins", "browser_use", "computer_use",
               "in_app_browser", "multi_agent", "view_image", "image_generation", "code_mode_host"]
        own = ["--ignore-user-config"] if supports(codex, "exec", flag="--ignore-user-config") else []
        # without the user's config.toml the reasoning effort drops to none: a summary needs some thinking
        effort = ["-c", f"model_reasoning_effort={os.environ.get('MT_CODEX_EFFORT', 'medium')}"]
        cmd = [codex, "exec", *own, *effort, "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
               "-c", "mcp_servers={}", *[a for f in off for a in ("--disable", f)],
               "-C", tmp, "-o", str(last)] + (["-m", model] if model else []) + ["-"]
        run_cli(cmd, f"{system}\n{transcript}", "codex exec")
        if not last.exists():
            die("codex exec gave no answer")
        return last.read_text(encoding="utf-8")


def run_opencode(system, transcript, model):
    # isolation, the config check and the session cleanup: opencode_run.py
    model = model or os.environ.get("MT_OPENCODE_MODEL")
    if not model:
        die("which OpenCode model writes the summary? --model provider/model (the list: opencode models), "
            "or choose it in ./transcribe setup")
    try:
        return opencode_run.run(find_bin("opencode"), model, f"{system}\n{transcript}", TIMEOUT, log)
    except opencode_run.Failed as e:
        die(f"OpenCode: {e}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would follow a redirect with the Authorization header, to any host: the key could leak."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        die(f"{req.full_url} redirects to {newurl}: set MT_LLM_BASE_URL to the final address (a redirect is not "
            "followed, so the API key is not sent anywhere else)")


OPENER = urllib.request.build_opener(NoRedirect)


def post(url, body, headers=None, what=None):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **(headers or {})})
    what = what or url
    try:
        with OPENER.open(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        die(f"{what} returned HTTP {e.code}: {e.read().decode(errors='replace')[:500]}")
    except urllib.error.URLError as e:
        die(f"cannot connect to {what}: {e.reason}")
    except TimeoutError:
        die(f"{what} gave no answer in {TIMEOUT} s (MT_LLM_TIMEOUT); a local model may need more time or a "
            "smaller context")
    except json.JSONDecodeError as e:
        die(f"{what} answered with something that is not JSON: {e}")


def save_raw(answer):
    RAW.parent.mkdir(exist_ok=True)
    RAW.write_text(answer, encoding="utf-8")


def check_read_all(prompt_tokens, text, where):
    """Servers with a small context window silently cut the beginning of the transcript off. Tokenizers we know
    take at most ~5 characters of English and ~3.6 of Cyrillic per token, so fewer tokens than that means the
    server did not read it all."""
    letters = [c for c in text if c.isalpha()]
    cyrillic = sum("Ѐ" <= c <= "ӿ" for c in letters) / max(len(letters), 1)
    floor = len(text) / (5 - 1.4 * cyrillic)
    if prompt_tokens and prompt_tokens < floor:
        die(f"{where} read only {prompt_tokens} tokens of a ~{int(floor * 1.3)} token transcript: its context window "
            "is too small. Load the model with a larger context (LM Studio: Context Length 32768; "
            "Ollama: OLLAMA_CONTEXT_LENGTH=32768) and run summarize again.")


def run_api(system, transcript, model):
    base = os.environ.get("MT_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("MT_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    model = model or os.environ.get("MT_LLM_MODEL")
    if not model:
        die("--llm api needs a model: --model or MT_LLM_MODEL (for example gpt-5-mini, or the model id in LM Studio)")
    body = {"model": model, "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": transcript}]}
    if os.environ.get("MT_LLM_TEMPERATURE"):  # reasoning models reject a custom temperature, so only on request
        body["temperature"] = float(os.environ["MT_LLM_TEMPERATURE"])
    u = urlparse(base)
    if key and u.scheme != "https" and u.hostname not in LOOPBACK:
        die(f"{base} is plain http: the API key would cross the network unencrypted. Use https, or a server on "
            "this machine (localhost)")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = post(f"{base}/chat/completions", body, headers, what=base)
    try:
        choice = data["choices"][0]
        answer = choice["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        die(f"unexpected response from {base}: {json.dumps(data, ensure_ascii=False)[:500]}")
    if choice.get("finish_reason") not in (None, "stop"):
        save_raw(answer)
        die(f"{base} stopped the answer early (finish_reason={choice.get('finish_reason')}): the summary would be "
            f"cut off, nothing was changed. Its answer: {RAW}")
    check_read_all((data.get("usage") or {}).get("prompt_tokens"), system + transcript, base)
    return answer


def ollama_models():
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5) as r:
            return {m["name"] for m in json.load(r).get("models", [])}
    except (urllib.error.URLError, OSError, ValueError):
        return None


def run_local(system, transcript, model):
    model = model or os.environ.get("MT_LOCAL_MODEL", LOCAL_MODEL)
    have = ollama_models()
    if have is None:
        die(f"Ollama is not running at {OLLAMA}: start it (open the Ollama app or run `ollama serve`); "
            "install: https://ollama.com/download or `brew install ollama`")
    if model not in have and f"{model}:latest" not in have:
        die(f"the model {model} is not downloaded: `ollama pull {model}` or ./transcribe setup")
    # Ollama's default context is a few thousand tokens and it cuts the rest off silently: size it to the text.
    est = (len(system) + len(transcript)) // 3
    ctx = min(131072, max(16384, (est + 8192 + 4095) // 4096 * 4096))
    for think in (True, False):
        # With thinking the summary is better, but a small model sometimes thinks in circles: cap the length and
        # fall back to an answer without thinking.
        body = {"model": model, "stream": False, "think": think, "keep_alive": "2m",
                "options": {"num_ctx": ctx, "num_predict": 12000 if think else 6000, "temperature": 0.2},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": transcript}]}
        data = post(f"{OLLAMA}/api/chat", body, what=f"Ollama ({model})")
        if data.get("error"):
            die(f"Ollama ({model}): {data['error']}")
        check_read_all(data.get("prompt_eval_count"), system + transcript, f"Ollama ({model})")
        answer = (data.get("message") or {}).get("content") or ""
        if data.get("done_reason") != "length" and answer.strip():
            return answer
        if think:
            log("the model ran out of room while thinking — asking again without thinking")
    save_raw(answer)
    die(f"Ollama ({model}) ran out of room for the answer twice: the summary would be cut off, nothing was "
        f"changed. Its answer: {RAW}")


def where_data_goes(llm, model):
    if llm == "opencode":
        provider = (model or os.environ.get("MT_OPENCODE_MODEL") or "?").split("/")[0]
        return f"goes to the provider '{provider}' as set up in your OpenCode"
    if llm == "claude":
        return "goes to Anthropic through your Claude Code"
    if llm == "codex":
        return "goes to OpenAI through your Codex CLI"
    if llm == "local":
        return "stays on this machine (Ollama)"
    host = urlparse(os.environ.get("MT_LLM_BASE_URL", "https://api.openai.com/v1")).hostname or "?"
    return "stays on this machine: the server is local" if host in LOOPBACK \
        else f"goes to {host}"


UTT = re.compile(r"^### (.+?) · \[(\d+(?::\d\d)+) — (\d+(?::\d\d)+)\]$")


def transcript_text(md, lb):
    """Utterances from transcript.md as "[start-end] Speaker: text" lines: transcript.md is what people fix by
    hand (names, terms), so the summary is made from it and not from the .txt merge wrote."""
    part = re.split(rf"^## {re.escape(lb['transcript'])}\s*$", md, maxsplit=1, flags=re.M)
    if len(part) < 2:
        die(f"transcript.md has no '## {lb['transcript']}' section — is it a file from merge?")
    lines, head, body = [], None, []

    def flush():
        if head and body:
            lines.append(f"[{head[1]}-{head[2]}] {head[0]}: {' '.join(body)}")

    for ln in part[1].splitlines():
        m = UTT.match(ln.strip())
        if m:
            flush()
            head, body = m.groups(), []
        elif ln.strip() and not ln.lstrip().startswith(("![", "<")):
            body.append(ln.strip())
    flush()
    if not lines:
        die("transcript.md has no utterances ('### Speaker · [MM:SS — MM:SS]') — is it a file from merge?")
    return "\n".join(lines)


def complete(summary, lb):
    """All headings in the order given: a missing last one means the model stopped in the middle."""
    return all(h.strip() in summary for h in lb["headings"].split(" / "))


def clean_answer(answer, lb):
    """The summary block only: from its heading on, without a transcript heading, images or a closing fence."""
    start = answer.find(f"## {lb['summary']}")
    if start < 0:
        return None
    text = answer[start:].strip().removesuffix("```").strip()
    # a model must not be able to fake the transcript or pull files into the HTML
    text = re.split(rf"^## {re.escape(lb['transcript'])}\s*$", text, flags=re.M)[0]
    text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("!["))
    return text.strip()


def insert_summary(md, summary, lb):
    """Replace the previous summary or insert a new one before the transcript heading."""
    tr, sm = f"## {lb['transcript']}", f"## {lb['summary']}"
    if not re.search(rf"^{re.escape(tr)}$", md, re.M):
        die(f"transcript.md has no '{tr}' section — is it a file from merge?")
    md = re.sub(rf"^{re.escape(sm)}\n.*?(?=^{re.escape(tr)}$)", "", md, flags=re.S | re.M)
    return re.sub(rf"^{re.escape(tr)}$", lambda m: summary + f"\n\n{tr}", md, count=1, flags=re.M)


def main():
    # a kill (TERM, HUP) unwinds like Ctrl+C, so the OpenCode session with the meeting text is still deleted
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda n, f: sys.exit(128 + n))
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?")
    ap.add_argument("--llm", choices=BACKENDS, default=os.environ.get("MT_LLM") or None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--check", action="store_true", help="one short request: does the model answer?")
    ap.add_argument("--list", action="store_true", help="models the isolated OpenCode run can use")
    args = ap.parse_args()
    if not args.llm:
        die(f"choose what writes the summary: --llm {'|'.join(BACKENDS)} (or MT_LLM). The meeting text goes to "
            "that service; fully offline: --llm local (Ollama) or --llm api with LM Studio.")
    run = {"claude": run_claude, "codex": run_codex, "opencode": run_opencode, "local": run_local,
           "api": run_api}[args.llm]
    global RAW
    if args.list:
        if args.llm != "opencode":
            die("--list is for --llm opencode")
        try:
            print("\n".join(opencode_run.models(find_bin("opencode"))))
        except opencode_run.Failed as e:
            die(f"OpenCode: {e}")
        return
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            RAW = Path(tmp) / "answer.md"  # backends save a broken answer there
            answer = run("Reply with the word OK.", "", args.model).strip()
        print(answer[:200])
        sys.exit(0 if answer else 1)
    if not args.out_dir:
        ap.error("the output folder is missing")
    out = Path(args.out_dir)
    md_path = out / "transcript.md"
    if not md_path.exists():
        die(f"{out} has no transcript.md — run merge or auto first")
    RAW = out / "raw" / "summary_answer.md"
    md = md_path.read_text(encoding="utf-8")
    lb = LABELS[detect(md)]
    lang = meeting_language(md)
    header = "\n".join(md.splitlines()[:4])  # title, date, speakers
    # languages without labels get English headings (export finds the sections by them), but the placeholders
    # are text for people: those the model writes in the meeting language
    own = lang in LABELS
    system = (f"{PROMPT.read_text(encoding='utf-8')}\nMeeting language: {lang}. Headings (use exactly these): "
              f"{lb['headings']}\nPlaceholders ({'use exactly these' if own else 'in the meeting language'}): "
              f"no owner — \"{lb['unassigned']}\", no deadline — \"{lb['no_deadline']}\"\n")
    transcript = f"=== TRANSCRIPT ===\n{header}\n\n{transcript_text(md, lb)}"

    log(f"writing the summary with {args.llm}: the meeting text {where_data_goes(args.llm, args.model)}")
    answer = run(system, transcript, args.model)
    save_raw(answer)  # kept even if the format is wrong
    summary = clean_answer(answer, lb)
    if not summary:
        die(f"the model answered in the wrong format (no '## {lb['summary']}'), its answer: {RAW}")
    if not complete(summary, lb):
        die(f"the summary is incomplete (not all sections from '{lb['headings']}'): the model may have stopped "
            f"early; nothing was changed. Its answer: {RAW}")
    md_path.write_text(insert_summary(md, summary, lb), encoding="utf-8")
    log(f"summary written to {md_path}; rebuilding HTML and PDF")
    r = subprocess.run([sys.executable, "-I", str(HERE / "export.py"), str(out)])
    if r.returncode:
        die(f"export failed — the summary is saved in {md_path}, run export again")


if __name__ == "__main__":
    main()

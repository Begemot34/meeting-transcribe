"""Models for asr: find them locally or download them with progress and resume.

For every Hugging Face model (speech recognition, word alignment):
  1. a folder in the repo's models/ — put a model here by hand if the network blocks huggingface.co;
  2. a complete snapshot in the Hugging Face cache (same path huggingface_hub uses: HF_HOME, HF_HUB_CACHE,
     XDG_CACHE_HOME);
  3. a download into that cache.
For en/fr/de/es/it the alignment model is a torchaudio checkpoint in the torch cache; plus the nltk punkt_tab data.

  models.py ensure --whisper large-v3 --language ru   # asr/setup: stdout is KEY=VALUE lines for bash eval
  models.py status --whisper large-v3 --language ru   # doctor: what lives where, downloads nothing
"""
import argparse
import ast
import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from fnmatch import fnmatch
from pathlib import Path

from huggingface_hub import constants as hf_constants  # light module: the hub's own cache paths and endpoint

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
HUB = Path(hf_constants.HF_HUB_CACHE)
ENDPOINT = hf_constants.ENDPOINT

STALL_S = int(os.environ.get("MT_DOWNLOAD_STALL_S", "120"))  # no progress for this long — restart the download
ATTEMPTS = int(os.environ.get("MT_DOWNLOAD_ATTEMPTS", "5"))
TICK_S = 10
CHUNK = 1024 * 1024  # the hub writes 10 MiB chunks by default: on a slow link that looks like a hang

# Weight sizes of the default models: they catch a file copied by hand only partially. Others are checked for
# being non-empty only.
KNOWN_SIZES = {
    ("Systran/faster-whisper-large-v3", "model.bin"): 3087284237,
    ("jonatasgrosman/wav2vec2-large-xlsr-53-russian", "pytorch_model.bin"): 1262093719,
    ("mlx-community/whisper-large-v3-turbo", "weights.safetensors"): 1613977612,
}
# The default models are pinned to the commit the sizes above belong to: a re-upload of the repo must not turn a
# good download into "incomplete" forever. Other repos follow their main branch.
REVISIONS = {
    "Systran/faster-whisper-large-v3": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
    "jonatasgrosman/wav2vec2-large-xlsr-53-russian": "2329100508896c6d9b157019803ab5601e6f3406",
    "mlx-community/whisper-large-v3-turbo": "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
}
# Recognition on the Apple GPU (mlx-whisper, scripts/asr_mlx.py): ~6× faster than whisperx on the CPU. The words
# get their timestamps from whisper itself, so there is no separate word alignment model.
MLX_MODELS = {"mlx-large-v3-turbo": "mlx-community/whisper-large-v3-turbo"}

ALIGN = "word alignment"
ASR = "speech recognition"


def log(msg):
    Progress.end_line()
    print(f"[transcribe] {msg}", file=sys.stderr, flush=True)


def die(msg, code=1):
    Progress.end_line()
    print(f"[transcribe] {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def gb(n):
    """Human size: 75 MB, 1.3 GB."""
    return f"{n / 1e6:.0f} MB" if n < 1e9 else f"{n / 1e9:.1f} GB"


class Progress:
    """Download progress: one bar redrawn in place on a terminal, a log line every TICK_S otherwise."""
    tty = sys.stderr.isatty()
    open_line = False  # a bar is drawn and the cursor is still on its line

    def __init__(self, label, total):
        self.label, self.total, self.last_log = f"{label} model", total, 0.0
        self.poll = 1 if self.tty else TICK_S

    def update(self, now, speed):
        size = f"{gb(now)} of {gb(self.total)}" if self.total else gb(now)
        if self.tty:
            width = 28
            if self.total:
                fill = int(width * min(now / self.total, 1))
                bar = f"[{'█' * fill}{'░' * (width - fill)}] {min(100, 100 * now / self.total):3.0f}%"
            else:
                bar = ""
            sys.stderr.write(f"\r\033[K[transcribe]   ↓ {self.label} {bar}  {size}  {speed:.1f} MB/s")
            sys.stderr.flush()
            Progress.open_line = True
        elif time.time() - self.last_log >= TICK_S:
            self.last_log = time.time()
            pct = f" ({min(100, 100 * now / self.total):.0f}%)" if self.total else ""
            log(f"  {self.label}: {size}{pct}, {speed:.1f} MB/s")

    @staticmethod
    def end_line():
        if Progress.open_line:
            sys.stderr.write("\n")
            Progress.open_line = False


def source_assignments(package, module):
    """Top-level assignments from a package's source without importing it (import whisperx pulls torch, ~5 s)."""
    src = Path(importlib.util.find_spec(package).origin).parent / f"{module}.py"
    out = {}
    for node in ast.parse(src.read_text()).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value
    return out


def package_dict(package, module, name):
    return ast.literal_eval(source_assignments(package, module)[name])


# ---------- Hugging Face models ----------

class HFModel:
    def __init__(self, title, repo, files, required, size, local=None, explicit=False):
        self.title, self.repo, self.files, self.required, self.size = title, repo, files, required, size
        self.local = local or MODELS_DIR / repo.split("/")[-1]
        self.explicit = explicit  # folder given explicitly via MT_*_MODEL: never downloaded or looked up in the cache
        self.cache = HUB / ("models--" + repo.replace("/", "--")) if repo else None


def looks_like_path(name):
    return name.startswith(("/", "./", "../", "~")) or Path(name).expanduser().is_dir()


def is_mlx(name):
    return name in MLX_MODELS


def whisper_model(name):
    if is_mlx(name):
        files = ["config.json", "weights.safetensors"]
        return HFModel(ASR, MLX_MODELS[name], files, files, 1.6e9)
    required = ["config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
    if looks_like_path(name):
        folder = Path(name).expanduser().resolve()
        if not folder.is_dir():
            die(f"MT_WHISPER_MODEL={name}: no such folder. Point it to a faster-whisper model folder "
                f"(config.json, model.bin, tokenizer.json, vocabulary.json) or use a model name such as large-v3.")
        if "large-v3" in folder.name:
            required.append("preprocessor_config.json")
        return HFModel(ASR, None, [], required, 0, local=folder, explicit=True)
    repo = package_dict("faster_whisper", "utils", "_MODELS").get(name, name)
    if "large-v3" in repo:  # without it faster-whisper uses 80 mel features instead of 128 and fails on shapes
        required.append("preprocessor_config.json")
    sizes = [("turbo", 1.6), ("distil", 1.5), ("tiny", 0.08), ("base", 0.15), ("small", 0.5), ("medium", 1.5),
             ("large", 3.1)]
    size = next((v for k, v in sizes if k in repo), 3.1)
    files = ["config.json", "model.bin", "tokenizer.json", "vocabulary.*", "preprocessor_config.json"]
    return HFModel(ASR, repo, files, required, size * 1e9)


ALIGN_FILES = ["config.json", "preprocessor_config.json", "vocab.json", "special_tokens_map.json",
               "tokenizer_config.json", "added_tokens.json", "pytorch_model.bin", "model.safetensors"]
ALIGN_REQUIRED = ["config.json", "preprocessor_config.json", "vocab.json", "pytorch_model.bin|model.safetensors"]


def torch_bundles():
    return {k for k in source_assignments("torchaudio", "pipelines/_wav2vec2/impl") if k.isupper()}


def align_model(language):
    """Alignment model: a custom one from MT_ALIGN_MODEL or the whisperx default for the language."""
    custom = os.environ.get("MT_ALIGN_MODEL", "").strip()
    if custom:
        if looks_like_path(custom):
            folder = Path(custom).expanduser().resolve()
            if not folder.is_dir():
                die(f"MT_ALIGN_MODEL={custom}: no such folder. It must contain a wav2vec2 model "
                    f"(config.json, preprocessor_config.json, vocab.json, pytorch_model.bin or model.safetensors).")
            return HFModel(ALIGN, None, [], ALIGN_REQUIRED, 0, local=folder, explicit=True)
        if custom in torch_bundles():
            return TorchModel(language, custom)
        return HFModel(ALIGN, custom, ALIGN_FILES, ALIGN_REQUIRED, 1.3e9)
    hf = package_dict("whisperx", "alignment", "DEFAULT_ALIGN_MODELS_HF")
    torch_models = package_dict("whisperx", "alignment", "DEFAULT_ALIGN_MODELS_TORCH")
    if language in torch_models:
        return TorchModel(language, torch_models[language])
    if language not in hf:
        die(f"whisperx has no default word alignment model for language '{language}'. Defaults exist for: "
            f"{', '.join(sorted({*hf, *torch_models}))}.\n"
            f"For another language, set a wav2vec2 model with a CTC head for it: MT_ALIGN_MODEL=<huggingface.co "
            f"repo or folder>; search huggingface.co for 'wav2vec2 {language}'.")
    return HFModel(ALIGN, hf[language], ALIGN_FILES, ALIGN_REQUIRED, 1.3e9)


def missing_files(m, folder):
    """What the model folder lacks, as human-readable strings; empty means complete."""
    missing = []
    for pattern in m.required:
        found = [f for alt in pattern.split("|") for f in folder.glob(alt) if f.is_file()]
        if not found or all(f.stat().st_size == 0 for f in found):
            missing.append(pattern.replace("|", " or ").replace("vocabulary.*", "vocabulary.json"))
            continue
        for f in found:
            want = KNOWN_SIZES.get((m.repo, f.name))
            if want and f.stat().st_size != want:
                missing.append(f"{f.name} (incomplete: {gb(f.stat().st_size)} of {gb(want)})")
    return missing


def cached_snapshot(m):
    """A complete snapshot in the HF cache, or None. A snapshot symlink appears only after its blob is fully
    downloaded, so checking that the required files are there is enough."""
    names = [REVISIONS[m.repo]] if m.repo in REVISIONS else []
    ref = m.cache / "refs" / "main"
    if ref.is_file():
        names.append(ref.read_text().strip())
    for name in names:
        snap = m.cache / "snapshots" / name
        if snap.is_dir() and not missing_files(m, snap):
            return snap
    return None


def find(m):
    """(path, where) or (None, None)."""
    if m.local.is_dir():
        missing = missing_files(m, m.local)
        if not missing:
            return m.local, "folder" if m.explicit else "models/ folder"
        if m.explicit:
            die(f"{m.local} is missing model files: {', '.join(missing)}")
        log(f"{m.local} is missing model files: {', '.join(missing)} — checking the Hugging Face cache")
    snap = cached_snapshot(m)
    return (snap, "HF cache") if snap else (None, None)


def incomplete_bytes(m):
    blobs = m.cache / "blobs"
    return sum(f.stat().st_size for f in blobs.glob("*.incomplete")) if blobs.is_dir() else 0


def human_error(e):
    import requests
    status = getattr(getattr(e, "response", None), "status_code", None)
    name = type(e).__name__
    if status in (401, 403):
        return (f"{ENDPOINT} denied access (HTTP {status}): a company filter or proxy, a wrong HF_TOKEN, "
                f"or the model requires authorization")
    if status == 404 or name in ("RepositoryNotFoundError", "HFValidationError"):
        return f"{ENDPOINT} has no such model — check MT_WHISPER_MODEL / MT_ALIGN_MODEL"
    if status:
        return f"{ENDPOINT} returned HTTP {status}"
    if isinstance(e, requests.exceptions.SSLError):
        return f"certificate error connecting to {ENDPOINT} — looks like a company proxy that replaces SSL"
    if isinstance(e, requests.exceptions.Timeout):
        return f"{ENDPOINT} did not respond within 15 s"
    if isinstance(e, requests.exceptions.ConnectionError):
        return f"cannot connect to {ENDPOINT} — no internet, a VPN/proxy is needed, or the site is blocked"
    return f"error contacting {ENDPOINT}: {name}"


def remote_files(m):
    """([(name, size, blob)], None) from HF metadata, or (None, reason). Also checks that the server is reachable."""
    try:
        from huggingface_hub import HfApi
        info = HfApi().model_info(m.repo, revision=REVISIONS.get(m.repo), files_metadata=True, timeout=15)
    except Exception as e:  # noqa: BLE001 — any cause goes into the human-readable error
        return None, human_error(e)
    files = [(s.rfilename, s.size or 0, s.lfs.sha256 if s.lfs else s.blob_id)
             for s in info.siblings if any(fnmatch(s.rfilename, p) for p in m.files)]
    names = {f[0] for f in files}
    if {"model.safetensors", "pytorch_model.bin"} <= names:  # both weight formats: one is enough, take safetensors
        files = [f for f in files if f[0] != "pytorch_model.bin"]
    return files, None


def progress_bytes(m, files):
    """How much of the needed files is on disk, including partial .incomplete blobs."""
    if not files:
        return incomplete_bytes(m)
    blobs, done = m.cache / "blobs", 0
    for _, size, blob in files:
        if (blobs / blob).is_file():
            done += size
        elif (blobs / f"{blob}.incomplete").is_file():
            done += (blobs / f"{blob}.incomplete").stat().st_size
    return done


def manual_hint(m, patterns=None):
    patterns = patterns or m.files
    names = [f for f in patterns if not f.endswith(("tokenizer_config.json", "added_tokens.json"))]
    shown = ", ".join(f.replace("vocabulary.*", "vocabulary.json") for f in names)
    include = " ".join(shlex.quote(f) for f in patterns)
    rev = REVISIONS.get(m.repo, "main")
    rev_flag = f" --revision {rev}" if m.repo in REVISIONS else ""
    return (
        f"What to check: internet; VPN or proxy (HTTPS_PROXY); whether {ENDPOINT} opens in a browser.\n"
        f"If your company has a Hugging Face mirror: HF_ENDPOINT=<mirror url> ./transcribe setup\n"
        f"Or download the model on a machine with access and put it into {m.local}/ :\n"
        f"  - from https://huggingface.co/{m.repo}/tree/{rev} — files {shown} (one weights file is enough);\n"
        f"  - or with: {ROOT}/.venv/bin/hf download {m.repo}{rev_flag} --local-dir {m.local} --include {include}\n"
        f"Then ./transcribe doctor shows whether the model was found."
    )


def child_download(repo, files, chunk):
    """Runs in a separate process: it can be killed on a stall, the .incomplete stays and resumes."""
    from huggingface_hub import constants, snapshot_download
    constants.DOWNLOAD_CHUNK_SIZE = chunk
    snapshot_download(repo, allow_patterns=files, revision=REVISIONS.get(repo))


def classify_child_error(text):
    """(reason, whether to retry)."""
    import re
    errors = [ln.strip() for ln in text.splitlines() if re.match(r"^[\w.]*(Error|Exception)\b", ln.strip())]
    last = errors[-1][:300] if errors else ""  # the exception line, not the last warning on stderr
    if "No space left" in text or "Errno 28" in text:
        return f"out of disk space in {HUB}. Free up space or use another disk: HF_HOME=/path", False
    if "Permission denied" in text or "Errno 13" in text:
        return f"no write permission for {HUB}. Check permissions or use another folder: HF_HOME=/path", False
    network = ("LocalEntryNotFoundError", "ConnectionError", "MaxRetryError", "ReadTimeout", "ConnectTimeout",
               "SSLError", "ProxyError", "ChunkedEncodingError")
    if any(n in text for n in network):  # the server check (api_err) explains a network cause better
        return "", True
    return last, True


def download(m):
    if hf_constants.HF_HUB_OFFLINE:
        die(f"{m.title} model {m.repo} is missing and downloads are disabled by HF_HUB_OFFLINE=1.\n"
            f"Unset it or put the model in place by hand.\n" + manual_hint(m))
    files, api_err = remote_files(m)
    total = sum(f[1] for f in files) if files else None
    patterns = [f[0] for f in files] if files else m.files
    have = progress_bytes(m, files)
    need = (total or m.size) - have
    HUB.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(HUB).free
    if free < need * 1.1:
        die(f"not enough space for the {m.title} model {m.repo}: ~{gb(need)} more needed, {gb(free)} free "
            f"in {HUB}.\nFree up space or use another disk: HF_HOME=/path ./transcribe setup")
    if api_err:
        log(f"{api_err}; trying to download anyway")
    resumed = f", {gb(have)} already here" if have else ""
    log(f"downloading the {m.title} model {m.repo} (~{gb(total or m.size)}{resumed}) to {m.cache} — once, "
        f"offline afterwards")
    if total:
        Progress(m.title, total).update(have, 0)
    # plain HTTP instead of xet: writes .incomplete sequentially and resumes via Range, so progress and resume
    # are predictable; with xet a dropped connection at 1 of 3 GB hung without a single log line
    env = {"HF_HUB_DISABLE_XET": "1", **os.environ, "HF_HUB_DISABLE_PROGRESS_BARS": "1"}
    code = (f"import sys; sys.path.insert(0, {str(Path(__file__).parent)!r}); import models; "
            f"models.child_download({m.repo!r}, {patterns!r}, {CHUNK})")
    fd, err_path = tempfile.mkstemp(prefix="mt-download-", suffix=".log")
    os.close(fd)
    err_log = Path(err_path)
    proc, reason = None, ""
    try:
        for attempt in range(1, ATTEMPTS + 1):
            with open(err_log, "w") as err_f:  # not PIPE: a chatty stderr would fill the buffer and hang the download
                proc = subprocess.Popen([sys.executable, "-I", "-c", code], env=env, stdout=err_f, stderr=err_f)
            start = last = progress_bytes(m, files)
            t0 = last_change = time.time()
            stalled = False
            bar = Progress(m.title, total)
            while proc.poll() is None:
                time.sleep(bar.poll)
                now = progress_bytes(m, files)
                if now != last:
                    last, last_change = now, time.time()
                bar.update(now, (now - start) / max(time.time() - t0, 1) / 1e6)
                if time.time() - last_change > STALL_S:
                    proc.kill()
                    proc.wait()
                    stalled = True
                    break
            gained = progress_bytes(m, files) - start
            if stalled:
                reason, retry = f"no progress for {STALL_S} s", True
            else:
                snap = cached_snapshot(m)
                if proc.returncode == 0 and snap:
                    log(f"  {m.title}: done")
                    err_log.unlink(missing_ok=True)
                    return snap
                raw, retry = classify_child_error(err_log.read_text()) if proc.returncode else ("", True)
                if proc.returncode and not raw and not api_err:
                    raw = f"the download process exited with code {proc.returncode}"
                if not retry:
                    die(f"could not download the {m.title} model {m.repo}: {raw}")
                # without network huggingface_hub may exit with 0, handing back whatever is in the cache
                reason = raw or api_err or f"connection to {ENDPOINT} dropped"
                if api_err and raw and api_err not in raw:
                    reason = f"{raw} (before that: {api_err})"
            if api_err and gained == 0:
                break  # the server was unreachable from the start and nothing arrived — retries only waste time
            if attempt < ATTEMPTS:
                log(f"  {reason}; attempt {attempt + 1} of {ATTEMPTS}, resuming where it stopped")
                time.sleep(min(5 * attempt, 30))
    except KeyboardInterrupt:
        if proc and proc.poll() is None:
            proc.kill()
        got = progress_bytes(m, files)
        err_log.unlink(missing_ok=True)
        die(f"download interrupted; {gb(got)} downloaded — the next run resumes from here", 130)
    finally:
        if proc and proc.poll() is None:
            proc.kill()
    got = progress_bytes(m, files)
    tail = f"\nTechnical details: {err_log}" if err_log.stat().st_size else ""
    die(f"could not download the {m.title} model {m.repo}: {reason}.\n"
        + (f"Downloaded {gb(got)}{' of ' + gb(total) if total else ''} — the next run resumes from here.\n"
           if got else "")
        + manual_hint(m, patterns if files else None) + tail)


# ---------- torchaudio models (alignment for en/fr/de/es/it) ----------

class TorchModel:
    def __init__(self, language, bundle):
        self.title, self.language, self.bundle = ALIGN, language, bundle
        call = source_assignments("torchaudio", "pipelines/_wav2vec2/impl")[bundle]
        args = [k.value for k in call.keywords if k.arg == "_path"] or call.args[:1]  # _path=… or first positional
        path = ast.literal_eval(args[0])
        self.url = f"https://download.pytorch.org/torchaudio/models/{path}"
        torch_home = os.environ.get("TORCH_HOME") or Path(
            os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "torch"
        self.file = Path(torch_home).expanduser() / "hub" / "checkpoints" / path

    def find(self):
        return (self.file, "torch cache") if self.file.is_file() and self.file.stat().st_size else (None, None)

    def download(self):
        """Download into .part with Range resume and the same progress lines as Hugging Face models."""
        import urllib.error
        import urllib.request
        part = self.file.with_name(self.file.name + ".part")
        self.file.parent.mkdir(parents=True, exist_ok=True)
        log(f"downloading the {self.title} model for '{self.language}', torchaudio {self.bundle}, to {self.file} — "
            f"once, offline afterwards")
        reason = ""
        try:
            for attempt in range(1, ATTEMPTS + 1):
                have = part.stat().st_size if part.exists() else 0
                req = urllib.request.Request(self.url, headers={"Range": f"bytes={have}-"} if have else {})
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:  # 30 s without data — retry
                        if have and resp.status != 206:
                            have = 0  # no Range support on the server — start over
                        total = have + int(resp.headers.get("Content-Length", 0) or 0)
                        t0, start, last_draw = time.time(), have, 0.0
                        bar = Progress(self.title, total)
                        with open(part, "ab" if have else "wb") as f:
                            while chunk := resp.read(CHUNK):
                                f.write(chunk)
                                have += len(chunk)
                                if time.time() - last_draw >= bar.poll:
                                    last_draw = time.time()
                                    bar.update(have, (have - start) / max(time.time() - t0, 1) / 1e6)
                    if not total or have >= total:
                        part.replace(self.file)
                        log(f"  {self.title}: done")
                        return self.file
                    reason = "the server cut the transfer"
                except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                    if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
                        die(f"out of disk space in {self.file.parent}")
                    reason = str(getattr(e, "reason", e))
                if attempt < ATTEMPTS:
                    log(f"  {reason}; attempt {attempt + 1} of {ATTEMPTS}, resuming where it stopped")
                    time.sleep(min(5 * attempt, 30))
        except KeyboardInterrupt:
            got = part.stat().st_size if part.exists() else 0
            die(f"download interrupted; {gb(got)} downloaded — the next run resumes from here", 130)
        die(f"could not download the alignment model {self.bundle}: {reason}.\n"
            f"What to check: internet; VPN or proxy (HTTPS_PROXY); whether download.pytorch.org opens.\n"
            f"Or download {self.url} by hand\nand put the file here: {self.file}")


# ---------- nltk ----------

def punkt_name(language):
    return package_dict("whisperx", "utils", "PUNKT_LANGUAGES").get(language, "english")


def punkt_ready(language):
    import nltk
    try:  # the same call whisperx makes: checks the data loads, not just that the folder exists
        nltk.data.load(f"tokenizers/punkt_tab/{punkt_name(language)}.pickle")
        return True
    except Exception:  # noqa: BLE001 — missing, broken or partially unpacked data
        return False


def ensure_punkt(language):
    if punkt_ready(language):
        return
    import nltk
    log("downloading the nltk punkt_tab sentence tokenizer data (~5 MB)")
    nltk.download("punkt_tab", quiet=True, force=True, raise_on_error=False)
    if not punkt_ready(language):
        die("could not download nltk punkt_tab (needed for word alignment).\n"
            "Download https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/tokenizers/punkt_tab.zip "
            "and unpack it into ~/nltk_data/tokenizers/ so that ~/nltk_data/tokenizers/punkt_tab/ exists.")


# ---------- commands ----------

def status(models, language):
    for m in models:
        path, where = (m.find() if isinstance(m, TorchModel) else find(m))
        if isinstance(m, TorchModel):
            name = f"{m.title} ({language}) torchaudio {m.bundle}"
            state = f"{where}: {path}" if path else "missing, downloads on first run (~0.4 GB)"
        else:
            name = f"{m.title} {m.repo or m.local}" + (" (MT_ALIGN_MODEL)" if os.environ.get("MT_ALIGN_MODEL")
                                                       and m.title == ALIGN else "")
            state = f"{where}: {path}" if path else f"missing, downloads on first run (~{gb(m.size)})"
            if not path and incomplete_bytes(m):
                state = f"partially downloaded ({gb(incomplete_bytes(m))}), the next run resumes"
        print(f"model:     {name} — {state}")
    if language is None:  # mlx: no word alignment, so no punkt either
        return
    ok = punkt_ready(language)
    print(f"model:     nltk punkt_tab — {'present' if ok else 'missing, downloads on first run (~5 MB)'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["ensure", "status"])
    ap.add_argument("--whisper", default="large-v3")
    ap.add_argument("--language", default="ru")
    args = ap.parse_args()

    whisper = whisper_model(args.whisper)
    if is_mlx(args.whisper):
        if args.action == "status":
            status([whisper], None)
        else:
            path, _ = find(whisper)
            print(f"WHISPER_PATH={shlex.quote(str(path or download(whisper)))}")
        return
    align = align_model(args.language)
    if args.action == "status":
        status([whisper, align], args.language)
        return

    path, _ = find(whisper)
    out = {"WHISPER_PATH": str(path or download(whisper))}
    if isinstance(align, TorchModel):
        align.find()[0] or align.download()
        out["ALIGN_PATH"] = align.bundle  # torchaudio model name: whisperx takes it from the torch cache
    else:
        path, _ = find(align)
        out["ALIGN_PATH"] = str(path or download(align))
    ensure_punkt(args.language)
    for k, v in out.items():
        print(f"{k}={shlex.quote(v)}")


if __name__ == "__main__":
    main()

"""Shared paths and helpers. Scripts are imported from scripts/ (pyproject: pythonpath) for unit tests and run
the way ./transcribe runs them — `python -I scripts/x.py` — for integration tests."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
DATA = Path(__file__).resolve().parent / "data"
GOLDEN = Path(__file__).resolve().parent / "golden"


def run_script(name, *args, cwd):
    """Runs scripts/<name>.py in isolated mode from another folder, as ./transcribe does."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MT_", "PYTHON"))} | {"PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, "-I", str(SCRIPTS / f"{name}.py"), *map(str, args)],
                          cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8")


def run_cli(*args, cwd=None, env_extra=None):
    """Runs a CLI (transcribe, transcribe-container) with a clean environment: a minimal PATH so an install
    step that starts by mistake fails loudly, no MT_*/host leakage; captured output, a 30 s timeout, stdin
    from DEVNULL. env_extra (HOME, PATH additions, MT_* …) is merged over the base environment."""
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONUTF8": "1"}
    if env_extra:
        env |= {k: str(v) for k, v in env_extra.items()}
    return subprocess.run([*map(str, args)], cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=30, stdin=subprocess.DEVNULL)


@pytest.fixture
def meeting(tmp_path):
    """A copy of the synthetic meeting's work folder (whisperx/, diarization.json, frames.json, frames/) under a
    path with a space and non-ASCII letters."""
    work = tmp_path / "встреча 1" / ".work"
    shutil.copytree(DATA / "meeting", work)
    return work


def utt(speaker, start, end, *words):
    """An utterance as merge builds it: words spread evenly over [start, end]."""
    step = (end - start) / len(words)
    ws = [{"word": w, "start": start + k * step, "end": start + (k + 1) * step, "speaker": speaker}
          for k, w in enumerate(words)]
    return {"start": start, "end": end, "speaker": speaker, "words": ws}


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


FAKE_TRANSCRIBE = DATA / "fake_transcribe"


def wait_for(cond, timeout=10.0, step=0.02):
    """Polls cond() until it is truthy (and returns it) or fails the test after `timeout` seconds."""
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    raise AssertionError(f"condition not met in {timeout} s")

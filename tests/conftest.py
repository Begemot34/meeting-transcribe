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

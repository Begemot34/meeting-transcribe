"""./transcribe argument handling: mistakes must fail fast, before anything is installed or downloaded.

On macOS it runs under the system /bin/bash 3.2 — the shell people really have there."""
import os
import subprocess
import sys

import pytest
from conftest import ROOT

BASH = "/bin/bash" if sys.platform == "darwin" else "bash"
COMMANDS = ["auto", "asr", "frames", "badges", "strip", "merge", "summarize", "export", "setup", "doctor",
            "install-skill", "uninstall-skill"]


def transcribe(*args, home):
    # no uv, brew or a GPU on PATH, an empty HOME: an install step that starts by mistake fails loudly
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8"}
    return subprocess.run([BASH, str(ROOT / "transcribe"), *args], env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=30, stdin=subprocess.DEVNULL)


@pytest.fixture
def home(tmp_path):
    return tmp_path


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS system bash")
def test_system_bash_is_3_2():
    out = subprocess.run([BASH, "-c", "echo $BASH_VERSION"], capture_output=True, text=True).stdout
    assert out.startswith("3.2")


@pytest.mark.parametrize("args", [[], ["help"], ["--help"], ["-h"]])
def test_help_lists_every_command(args, home):
    r = transcribe(*args, home=home)
    assert r.returncode == 0, r.stderr
    for cmd in COMMANDS:
        assert f"\n  {cmd} " in r.stdout, cmd


@pytest.mark.parametrize(("args", "says"), [
    (["frobnicate"], "unknown command: frobnicate"),
    (["auto", "only-video.mp4"], "usage: auto <video> <out_dir>"),
    (["merge", "work"], "usage: merge <work> <out_dir>"),
    (["export"], "usage: export"),
    (["setup", "--device", "tpu"], "setup: --device cpu|cuda"),
    (["setup", "--summary", "gpt"], "setup: --summary claude|codex|opencode|local|api|none"),
    (["setup", "--bogus"], "setup: unknown argument --bogus"),
    (["doctor", "--bogus"], "unknown argument --bogus"),
])
def test_mistakes_fail_before_installing(args, says, home):
    r = transcribe(*args, home=home)
    assert r.returncode != 0
    assert says in r.stderr
    assert "installing" not in r.stderr and "plan:" not in r.stderr
    assert not os.listdir(home)


def test_doctor_without_an_environment(home, tmp_path):
    # a copy of the CLI next to no .venv: doctor reports and installs nothing
    cli = tmp_path / "app" / "transcribe"
    cli.parent.mkdir()
    cli.write_bytes((ROOT / "transcribe").read_bytes())
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8"}
    r = subprocess.run([BASH, str(cli), "doctor"], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "not installed — run ./transcribe setup" in r.stdout
    assert sorted(os.listdir(cli.parent)) == ["transcribe"]

"""./transcribe argument handling: mistakes must fail fast, before anything is installed or downloaded.

On macOS it runs under the system /bin/bash 3.2 — the shell people really have there."""
import os
import re
import subprocess
import sys

import pytest
from conftest import ROOT, run_cli

BASH = "/bin/bash" if sys.platform == "darwin" else "bash"
COMMANDS = ["auto", "asr", "frames", "badges", "strip", "merge", "summarize", "export", "setup", "doctor",
            "install-skill", "uninstall-skill"]


def transcribe(*args, home, mt_config=None):
    # a thin wrapper over the shared runner: an empty HOME, no uv/brew/GPU on PATH (an install step that
    # starts by mistake fails loudly)
    extra = {"HOME": str(home)}
    if mt_config is not None:
        extra["MT_CONFIG"] = str(mt_config)  # sourced at the very top (line 12), before help or any install
    return run_cli(BASH, str(ROOT / "transcribe"), *args, env_extra=extra)


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
    r = run_cli(BASH, cli, "doctor", env_extra={"HOME": str(home)})
    assert r.returncode == 0, r.stderr
    assert "not installed — run ./transcribe setup" in r.stdout
    assert sorted(os.listdir(cli.parent)) == ["transcribe"]


# MT_CONFIG: transcribe sources this file at the very top (line 12), before help or any install.
# The [ -f ] guard means a missing file is harmless; a broken one must fail loudly, which also
# proves the file is really read rather than silently ignored.


@pytest.mark.parametrize("content", [
    "MT_LANGUAGE=ru\n",
    "export MT_WHISPER_MODEL=large-v3\n",
])
def test_mt_config_valid_is_sourced_before_help(content, home, tmp_path):
    cfg = tmp_path / "config.env"
    cfg.write_text(content)
    r = transcribe("help", home=home, mt_config=cfg)
    assert r.returncode == 0, r.stderr


def test_mt_config_syntax_error_is_fatal(home, tmp_path):
    # a broken config must fail loudly — proves the file is really read, not ignored
    cfg = tmp_path / "broken.env"
    cfg.write_text("this is ) not valid bash\n")
    r = transcribe("help", home=home, mt_config=cfg)
    assert r.returncode != 0
    assert r.stderr.strip(), "bash reported nothing for a broken MT_CONFIG"


def test_mt_config_missing_is_not_fatal(home, tmp_path):
    # an absent MT_CONFIG must not break help: the [ -f ] guard skips sourcing
    cfg = tmp_path / "does-not-exist.env"
    r = transcribe("help", home=home, mt_config=cfg)
    assert r.returncode == 0, r.stderr


# lock_work: one asr at a time per work folder; a lock of a process that is gone is taken over.
# Unit-tested by extracting the function from transcribe and sourcing it with a die() stub.


def _extract_lock_work():
    # extracts lock_work from transcribe; update it if the function moves
    out = subprocess.run(["sed", "-n", "/^lock_work()/,/^}/p", str(ROOT / "transcribe")],
                         capture_output=True, text=True, check=True).stdout
    assert "lock_work()" in out, "lock_work extraction changed — update the sed range"
    return out


def _make_lock(work, pid, host):
    lock = work / ".asr.lock"
    lock.mkdir()
    (lock / "pid").write_text(str(pid))
    (lock / "host").write_text(host)
    return lock


def _hostname():
    return subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()


def _run_lock_work(work, func_file):
    # source the extracted lock_work with a die() stub and report the resulting lock contents
    script = """
source "$1"
die() { echo "[transcribe] $*" >&2; exit 1; }
work="$2"
lock_work "$work"
p=$(cat "$work/.asr.lock/pid")
h=$(cat "$work/.asr.lock/host")
echo "PID=$p"
echo "HOST=$h"
echo "SHELLPID=$$"
"""
    return subprocess.run([BASH, "-c", script, "locktest", str(func_file), str(work)],
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_lock_work_takes_over_foreign_host(tmp_path):
    # extracts lock_work from transcribe; update it if the function moves.
    # a lock left by another host (e.g. a container, whose $$ is 1) is taken over: pid and host
    # are refreshed to this run.
    func_file = tmp_path / "lock_work.sh"
    func_file.write_text(_extract_lock_work())
    work = tmp_path / "work"
    work.mkdir()
    _make_lock(work, 1, "other-host")  # stale lock from a different host
    r = _run_lock_work(work, func_file)
    assert r.returncode == 0, r.stderr
    pid = re.search(r"PID=(\S+)", r.stdout).group(1)
    host = re.search(r"HOST=(\S+)", r.stdout).group(1)
    assert host == _hostname()
    assert pid != "1"  # refreshed, not the stale container pid
    assert pid == re.search(r"SHELLPID=(\S+)", r.stdout).group(1)


def test_lock_work_refuses_live_process(tmp_path):
    # extracts lock_work from transcribe; update it if the function moves.
    # a lock whose pid is alive on this host is a real competitor -> die "another asr"
    func_file = tmp_path / "lock_work.sh"
    func_file.write_text(_extract_lock_work())
    work = tmp_path / "work"
    work.mkdir()
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        _make_lock(work, sleeper.pid, _hostname())
        r = _run_lock_work(work, func_file)
        assert r.returncode == 1, r.stdout
        assert "another asr" in r.stderr
    finally:
        sleeper.terminate()
        try:
            sleeper.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sleeper.kill()


def test_lock_work_takes_over_dead_pid(tmp_path):
    # extracts lock_work from transcribe; update it if the function moves.
    # a lock on this host but with a dead pid (999999) is taken over.
    func_file = tmp_path / "lock_work.sh"
    func_file.write_text(_extract_lock_work())
    work = tmp_path / "work"
    work.mkdir()
    _make_lock(work, 999999, _hostname())  # dead pid on this host
    r = _run_lock_work(work, func_file)
    assert r.returncode == 0, r.stderr
    pid = re.search(r"PID=(\S+)", r.stdout).group(1)
    host = re.search(r"HOST=(\S+)", r.stdout).group(1)
    assert host == _hostname()
    assert pid != "999999"  # refreshed, not the dead pid
    assert pid == re.search(r"SHELLPID=(\S+)", r.stdout).group(1)

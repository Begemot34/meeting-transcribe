"""./transcribe-container flag parsing: bad input must fail before podman runs anything —
a stray build, a path outside the current directory, or a colon in $PWD must not start a
container or a multi-minute image build.

On macOS the wrapper refuses to run at all (Linux only); the tests are skipped there.

All guards live before the first `podman` call (transcribe-container lines 22–100), so the
"no podman needed" cases below never touch podman. The "needs podman on PATH" cases only call
`podman image exists` and stop at "is not here" — no build, no volume, no container."""
import os
import shutil
import sys

import pytest
from conftest import ROOT, run_cli

BASH = "/bin/bash" if sys.platform == "darwin" else "bash"

# a bare directory, as the wrapper's HOME: an empty HOME means no config or cache is read from the host
@pytest.fixture
def home(tmp_path):
    return tmp_path


# podman sits on PATH on the test machine; the "needs podman" cases add its directory so
# `podman image exists` can run and report a missing foreign image.


def _podman_dir():
    pod = shutil.which("podman")
    return os.path.dirname(pod) if pod else "/usr/bin:/bin"


def container(*args, cwd, home, podman=False, mt_device=None):
    # a thin wrapper over the shared runner: a clean environment, an empty HOME. Guards that fire first
    # need nothing; the podman cases add podman's directory so `podman image exists` can run.
    extra = {"HOME": str(home), "PATH": _podman_dir() if podman else "/usr/bin:/bin"}
    if mt_device is not None:
        extra["MT_DEVICE"] = mt_device
    return run_cli(BASH, str(ROOT / "transcribe-container"), *args, cwd=cwd, env_extra=extra)


@pytest.mark.skipif(sys.platform == "darwin", reason="transcribe-container is Linux-only")
def test_unknown_flag(home, tmp_path):
    # no podman needed: the flag is rejected before any podman call
    r = container("--nope", cwd=tmp_path, home=home)
    assert r.returncode == 2, r.stderr
    assert "unknown flag" in r.stderr


@pytest.mark.skipif(sys.platform == "darwin", reason="transcribe-container is Linux-only")
def test_build_and_pull_together(home, tmp_path):
    # no podman needed: --build and --pull are mutually exclusive
    r = container("--build", "--pull", cwd=tmp_path, home=home)
    assert r.returncode == 2, r.stderr
    assert "use --build or --pull, not both" in r.stderr


@pytest.mark.skipif(sys.platform == "darwin", reason="transcribe-container is Linux-only")
def test_colon_in_cwd(home, tmp_path):
    # no podman needed: podman cannot mount a -v src:dst whose source holds a colon
    colondir = tmp_path / "rec:ording"
    colondir.mkdir()
    r = container("doctor", cwd=colondir, home=home)
    assert r.returncode == 2, r.stderr
    assert "podman cannot mount it" in r.stderr


@pytest.mark.skipif(sys.platform == "darwin", reason="transcribe-container is Linux-only")
def test_positional_path_outside_cwd(home, tmp_path):
    # no podman needed: a recording/output outside the mounted directory would be lost with --rm
    r = container("auto", "/etc/hostname", "out", cwd=tmp_path, home=home)
    assert r.returncode == 2, r.stderr
    assert "resolves outside the current directory" in r.stderr


@pytest.mark.skipif(sys.platform == "darwin", reason="transcribe-container is Linux-only")
@pytest.mark.skipif(shutil.which("podman") is None, reason="podman not installed")
def test_flag_value_is_not_a_positional_path(home, tmp_path):
    # needs podman on PATH: --speakers /tmp/x.json is a flag value, not a positional path, so the
    # path guard lets it through; the foreign image is then absent -> exit 1 "is not here"
    # (proves the guard did not reject it — a positional outside would have exited 2 first).
    r = container("--image", "some-foreign:tag", "merge", "work", "out", "--speakers", "/tmp/x.json",
                  cwd=tmp_path, home=home, podman=True)
    assert r.returncode == 1, r.stderr
    assert "is not here" in r.stderr


@pytest.mark.skipif(sys.platform == "darwin", reason="transcribe-container is Linux-only")
@pytest.mark.skipif(shutil.which("podman") is None, reason="podman not installed")
def test_foreign_image_without_build(home, tmp_path):
    # needs podman on PATH: a foreign --image is never built silently; missing -> exit 1
    r = container("--image", "some-foreign:tag", "doctor", cwd=tmp_path, home=home, podman=True)
    assert r.returncode == 1, r.stderr
    assert "is not here" in r.stderr


@pytest.mark.skipif(sys.platform == "darwin", reason="transcribe-container is Linux-only")
def test_mt_device_cuda_needs_cuda_image(home, tmp_path):
    # no podman needed: MT_DEVICE=cuda with the default (cpu) image is refused before any podman call
    r = container("doctor", cwd=tmp_path, home=home, mt_device="cuda")
    assert r.returncode == 2, r.stderr
    assert "MT_DEVICE=cuda needs a cuda image" in r.stderr

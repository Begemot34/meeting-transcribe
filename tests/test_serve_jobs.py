"""The service's job store: options, names, folders, state changes, recovery after a restart, retention."""
import json
from datetime import datetime, timedelta, timezone

from pathlib import Path

import pytest
from serve_jobs import (BadOptions, Job, JobStore, Options, allowed, options_to_args, parse_options, slugify,
                        split_name)


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs", max_attempts=2)


def recording(tmp_path, name="Board meeting.mp4", data=b"video"):
    p = tmp_path / "inbox" / name
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(data)
    return p


@pytest.mark.parametrize(("stem", "slug"), [
    ("Board meeting (final)", "board-meeting-final"),
    ("Планёрка 1", "планёрка-1"),
    ("...", "meeting"),
    ("", "meeting"),
    ("a" * 60, "a" * 40),
])
def test_slugify(stem, slug):
    assert slugify(stem) == slug


def test_split_name_and_allowed():
    assert split_name("Board meeting.MP4") == ("Board meeting", "mp4")
    assert split_name("noext") == ("noext", "")
    assert split_name(".hidden") == (".hidden", "")
    assert allowed("a.mkv") and allowed("a.M4A")
    assert not allowed("a.exe") and not allowed("a") and not allowed("a.mp4.part")


def test_parse_options_valid():
    o = parse_options({"title": " Board\x07 ", "date": "2026-11-08", "language": "ru", "summary": "none",
                       "names": "false"})
    assert o == Options(title="Board", date="2026-11-08", language="ru", summary="none", names=False)
    assert parse_options({}) == Options()
    assert parse_options({"title": "", "names": True}) == Options()


@pytest.mark.parametrize("raw", [
    {"color": "red"}, {"date": "08.11.2026"}, {"language": "russian"}, {"summary": "gpt"}, {"names": "maybe"},
    {"title": 5}, {"title": "x" * 201}, [],
])
def test_parse_options_rejects(raw):
    with pytest.raises(BadOptions):
        parse_options(raw)


def test_options_to_args():
    assert options_to_args(Options(), "Board") == ["--title", "Board"]
    assert options_to_args(Options(title="T", date="2026-01-02", language="en", summary="api", names=False), "x") \
        == ["--title", "T", "--date", "2026-01-02", "--language", "en", "--summary", "api", "--no-names"]
    assert options_to_args(Options(summary="none"), "x") == ["--title", "x", "--no-summary"]


def test_new_id_adds_a_suffix(store):
    now = datetime(2026, 11, 8, 14, 15, 0)
    store.root.mkdir(parents=True)
    first = store.new_id("Board meeting", now)
    assert first == "20261108-141500-board-meeting"
    (store.root / first).mkdir()
    assert store.new_id("Board meeting", now) == first + "-2"


def test_create_moves_recording_and_sidecar(store, tmp_path):
    src = recording(tmp_path)
    side = src.with_name(src.name + ".json")
    side.write_text('{"title": "T"}')
    job = store.create(src, src.name, Options(title="T"), "inbox", side)
    d = store.job_dir(job.id)
    assert not src.exists() and not side.exists()
    assert (d / "source.mp4").read_bytes() == b"video" and (d / "sidecar.json").exists()
    saved = json.loads((d / "job.json").read_text())
    assert saved["state"] == "queued" and saved["source"] == {"name": "Board meeting.mp4", "size": 5,
                                                              "origin": "inbox"}
    assert saved["out_dir"] == "board-meeting" and saved["options"]["title"] == "T"
    assert store.load(job.id) == job


def test_move_copies_across_filesystems(tmp_path, monkeypatch):
    import errno
    import os

    import serve_jobs
    src, dst = tmp_path / "a.mp4", tmp_path / "b.mp4"
    src.write_bytes(b"video")
    real = os.replace

    def exdev(a, b):
        if Path(a) == src:
            raise OSError(errno.EXDEV, "cross-device link")
        return real(a, b)

    monkeypatch.setattr(os, "replace", exdev)
    serve_jobs.move(src, dst)
    assert dst.read_bytes() == b"video" and not src.exists() and not (tmp_path / "b.mp4.part").exists()


def test_move_keeps_the_source_when_it_cannot_be_deleted(tmp_path, monkeypatch):
    import errno
    import os

    import serve_jobs
    src, dst = tmp_path / "a.mp4", tmp_path / "b.mp4"
    src.write_bytes(b"video")
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError(errno.EXDEV, "x"))
                        if Path(a) == src else real(a, b))
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: (_ for _ in ()).throw(PermissionError("ro"))
                        if self == src else os.remove(self))
    with pytest.raises(PermissionError):
        serve_jobs.move(src, dst)
    assert src.exists() and not dst.exists()


def test_failed_intake_leaves_nothing_behind(store, tmp_path, monkeypatch):
    import serve_jobs
    src = recording(tmp_path)
    side = src.with_name(src.name + ".json")
    side.write_text("{}")
    real = serve_jobs.move

    def fail_on_source(a, b):
        if a == src:
            raise OSError(28, "No space left on device")
        return real(a, b)

    monkeypatch.setattr(serve_jobs, "move", fail_on_source)
    with pytest.raises(OSError):
        store.create(src, src.name, Options(), "inbox", side)
    assert src.exists() and side.exists()  # both still in the inbox
    assert store.all() == [] and list(store.incoming.iterdir()) == []


def test_recover_finishes_or_drops_staged_jobs(store, tmp_path):
    from serve_jobs import Job
    store.incoming.mkdir(parents=True)
    for job_id, with_source in (("20260101-000000-a", True), ("20260101-000000-b", False)):
        d = store.incoming / job_id
        d.mkdir()
        JobStore._write(d, Job(id=job_id, state="queued", created="2026-01-01T00:00:00+00:00",
                               source={"name": "a.mp4", "size": 5, "origin": "inbox"}, options={}, out_dir="a"))
        if with_source:
            (d / "source.mp4").write_bytes(b"video")
        else:
            (d / "source.mp4.part").write_bytes(b"vi")
    assert store.recover() == ["20260101-000000-a"]
    assert (store.root / "20260101-000000-a" / "source.mp4").exists()
    assert list(store.incoming.iterdir()) == []


def test_one_service_per_jobs_folder(tmp_path):
    a, b = JobStore(tmp_path / "jobs"), JobStore(tmp_path / "jobs")
    a.acquire()
    with pytest.raises(RuntimeError, match="another service"):
        b.acquire()


def test_create_failed(store, tmp_path):
    src = recording(tmp_path)
    job = store.create_failed(src, src.name, "inbox", "bad sidecar: x")
    assert store.load(job.id).state == "failed" and job.error == "bad sidecar: x" and not src.exists()


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", "", ".incoming", "x" * 200, "a\0b"])
def test_job_dir_rejects(store, bad):
    with pytest.raises(KeyError):
        store.job_dir(bad)


def test_transitions():
    job = Job(id="j", state="queued", created="", source={"name": "a.mp4"}, options={}, out_dir="a")
    for new in ("running", "done", "queued", "running", "failed", "queued"):
        JobStore.transition(job, new)
    job.state = "done"
    with pytest.raises(ValueError):
        JobStore.transition(job, "running")
    job.state = "failed"
    with pytest.raises(ValueError):
        JobStore.transition(job, "done")


def test_retry_resets_a_finished_job(store, tmp_path):
    job = store.create(recording(tmp_path), "a.mp4", Options(), "upload")
    store.update(job.id, state="running")
    with pytest.raises(ValueError):
        store.retry(job.id)
    store.update(job.id, state="failed", error="boom", attempts=2, progress=40, exit_code=1)
    again = store.retry(job.id)
    assert (again.state, again.error, again.attempts, again.progress, again.exit_code) == ("queued", None, 0, 0, None)


def test_retry_refuses_without_the_recording(store, tmp_path):
    job = store.create(recording(tmp_path), "a.mp4", Options(), "upload")
    store.update(job.id, state="done")
    store.source_path(job).unlink()
    with pytest.raises(ValueError, match="deleted"):
        store.retry(job.id)


def test_recover(store, tmp_path):
    a = store.create(recording(tmp_path, "a.mp4"), "a.mp4", Options(), "inbox")
    b = store.create(recording(tmp_path, "b.mp4"), "b.mp4", Options(), "inbox")
    c = store.create(recording(tmp_path, "c.mp4"), "c.mp4", Options(), "inbox")
    store.update(a.id, state="running", attempts=1, pid=123, created="2026-01-01T00:00:03+00:00")
    store.update(b.id, state="running", attempts=2, created="2026-01-01T00:00:01+00:00")
    store.update(c.id, created="2026-01-01T00:00:02+00:00")
    store.incoming.mkdir(exist_ok=True)
    (store.incoming / "x.part").write_bytes(b"half")
    assert store.recover() == [c.id, a.id]
    assert store.load(a.id).state == "queued" and store.load(a.id).pid == 123  # kept: the next start checks it
    assert store.load(b.id).state == "failed" and "interrupted 2 times" in store.load(b.id).error
    assert list(store.incoming.iterdir()) == []


def test_list_newest_first_and_skips_foreign(store, tmp_path):
    a = store.create(recording(tmp_path, "a.mp4"), "a.mp4", Options(), "inbox")
    b = store.create(recording(tmp_path, "b.mp4"), "b.mp4", Options(), "inbox")
    store.update(a.id, created="2026-01-01T00:00:00+00:00")
    store.update(b.id, created="2026-01-02T00:00:00+00:00")
    (store.root / "junk").mkdir()
    (store.root / "broken").mkdir()
    (store.root / "broken" / "job.json").write_text("{")
    assert [j.id for j in store.all()] == [b.id, a.id]
    assert [j.id for j in store.all(limit=1)] == [b.id]


def test_expire(store, tmp_path):
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    old = store.create(recording(tmp_path, "a.mp4"), "a.mp4", Options(), "inbox")
    new = store.create(recording(tmp_path, "b.mp4"), "b.mp4", Options(), "inbox")
    queued = store.create(recording(tmp_path, "c.mp4"), "c.mp4", Options(), "inbox")
    store.update(old.id, state="done", finished=(now - timedelta(days=10)).isoformat())
    store.update(new.id, state="failed", finished=(now - timedelta(days=1)).isoformat())
    assert store.expire(0, now) == []
    assert store.expire(7, now) == [old.id]
    assert {j.id for j in store.all()} == {new.id, queued.id}


def test_artifact_path(store, tmp_path):
    job = store.create(recording(tmp_path), "a.mp4", Options(), "upload")
    d = store.job_dir(job.id)
    (d / "a").mkdir()
    (d / "a" / "transcript.md").write_text("# t")
    (d / "log.txt").write_text("log")
    job.artifacts = store.artifacts_of(job)
    assert job.artifacts == ["transcript.md"]
    assert store.artifact_path(job, "transcript.md") == d / "a" / "transcript.md"
    assert store.artifact_path(job, "log.txt") == d / "log.txt"
    for bad in ("job.json", "../job.json", "a.pdf", "source.mp4"):
        with pytest.raises(KeyError):
            store.artifact_path(job, bad)

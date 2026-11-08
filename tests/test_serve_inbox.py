"""The service's inbox: a file is taken only once it stopped changing; temporary and foreign names stay."""
import json
import os
import threading

from conftest import wait_for
from serve_inbox import Scanner, ignored, scan_once
from serve_jobs import JobStore


def put(inbox, name, data=b"video", mtime=None):
    inbox.mkdir(exist_ok=True)
    p = inbox / name
    p.write_bytes(data)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def test_ignored():
    for name in ("a.mp4.part", ".a.mp4", "~a.mp4", "a.mp4.json", "a.txt", "a.crdownload", "$a.mp4", "A.MP4.TMP"):
        assert ignored(name), name
    for name in ("a.mp4", "Встреча.MKV", "a.m4a"):
        assert not ignored(name), name


def test_ready_only_after_stable(tmp_path):
    inbox, seen = tmp_path / "inbox", {}
    p = put(inbox, "a.mp4", mtime=1000)
    assert scan_once(inbox, seen, now=0, stable_s=10) == []  # first sight
    assert scan_once(inbox, seen, now=5, stable_s=10) == []
    assert scan_once(inbox, seen, now=10, stable_s=10) == [p]


def test_growing_file_restarts_the_clock(tmp_path):
    inbox, seen = tmp_path / "inbox", {}
    p = put(inbox, "a.mp4", b"v", mtime=1000)
    scan_once(inbox, seen, 0, 10)
    put(inbox, "a.mp4", b"vv", mtime=1001)
    assert scan_once(inbox, seen, 11, 10) == []
    assert scan_once(inbox, seen, 20, 10) == []
    assert scan_once(inbox, seen, 21, 10) == [p]


def test_skips_empty_ignored_and_folders(tmp_path):
    inbox, seen = tmp_path / "inbox", {}
    put(inbox, "empty.mp4", b"")
    put(inbox, "a.mp4.part")
    put(inbox, "notes.txt")
    (inbox / "sub.mp4").mkdir()
    for now in (0, 100):
        assert scan_once(inbox, seen, now, 1) == []


def test_removed_file_is_forgotten(tmp_path):
    inbox, seen = tmp_path / "inbox", {}
    p = put(inbox, "a.mp4")
    scan_once(inbox, seen, 0, 1)
    p.unlink()
    assert scan_once(inbox, seen, 5, 1) == [] and seen == {}


def test_missing_inbox_is_empty(tmp_path):
    assert scan_once(tmp_path / "nope", {}, 0, 1) == []


def run_scanner(tmp_path):
    store = JobStore(tmp_path / "jobs")
    submitted, stop = [], threading.Event()
    sc = Scanner(tmp_path / "inbox", store, submitted.append, poll_s=0.02, stable_s=0.05, stop=stop)
    sc.start()
    return store, submitted, stop, sc


def test_scanner_takes_recording_with_sidecar(tmp_path):
    inbox = tmp_path / "inbox"
    put(inbox, "Board.mp4.json", json.dumps({"title": "Board", "summary": "none"}).encode())
    put(inbox, "Board.mp4")
    store, submitted, stop, sc = run_scanner(tmp_path)
    try:
        wait_for(lambda: submitted)
    finally:
        stop.set()
        sc.join(2)
    job = store.load(submitted[0])
    assert job.state == "queued" and job.options["title"] == "Board" and job.options["summary"] == "none"
    assert list(inbox.iterdir()) == []
    assert (store.job_dir(job.id) / "sidecar.json").exists() and len(submitted) == 1


def test_scanner_fails_a_bad_sidecar(tmp_path):
    inbox = tmp_path / "inbox"
    put(inbox, "a.mp4.json", b"{not json")
    put(inbox, "a.mp4")
    store, submitted, stop, sc = run_scanner(tmp_path)
    try:
        jobs = wait_for(store.all)
    finally:
        stop.set()
        sc.join(2)
    assert jobs[0].state == "failed" and jobs[0].error.startswith("bad sidecar") and submitted == []
    assert list(inbox.iterdir()) == []


def test_scanner_two_drops_of_one_name(tmp_path):
    inbox = tmp_path / "inbox"
    store, submitted, stop, sc = run_scanner(tmp_path)
    try:
        put(inbox, "a.mp4")
        wait_for(lambda: len(submitted) == 1)
        put(inbox, "a.mp4", b"other")
        wait_for(lambda: len(submitted) == 2)
    finally:
        stop.set()
        sc.join(2)
    assert len(set(submitted)) == 2

"""The service's workers, against a fake ./transcribe: progress from the log, end states, timeout, cancel,
shutdown, parallel workers, the webhook and the S3 upload."""
import http.server
import json
import os
import threading
import time

import pytest
from conftest import FAKE_TRANSCRIBE, wait_for
from serve_jobs import JobStore, Options
from serve_worker import S3Sink, WorkerPool, build_command, parse_progress, warning_in

E = "\033[1;34m[transcribe]\033[0m "


@pytest.mark.parametrize(("line", "before", "after"), [
    (E + "recognition (ru, large-v3) and diarization run in parallel", ("audio", 2), ("asr", 5)),
    (E + "  text: loading model · speakers: 0% · 0:01", ("asr", 5), ("asr", 5)),
    (E + "  text: recognizing 40% · speakers: 20% · 0:30 of ~5:00", ("asr", 5), ("asr", 18)),
    (E + "  text: aligning words 90% · speakers: done · 4:00", ("asr", 5), ("asr", 63)),
    (E + "  text: done · speakers: done · 5:00", ("asr", 5), ("asr", 70)),
    ("turns=3 speakers=['SPEAKER_00'] time=1s", ("asr", 70), ("frames", 72)),
    ("kept=2", ("frames", 72), ("frames", 75)),
    ("sheets=1", ("frames", 75), ("frames", 78)),
    ("utterances=5 frames=2 duration=00:10", ("frames", 78), ("names", 85)),
    (E + "names from the screen (vision): SPEAKER_00 → Anna", ("names", 85), ("names", 87)),
    (E + "looking for introductions in the text with local: …", ("names", 85), ("names", 87)),
    (E + "writing the summary with api: the meeting text goes to …", ("names", 87), ("summary", 90)),
    (E + "summary written to out/transcript.md; rebuilding HTML and PDF", ("summary", 90), ("export", 96)),
    ("html: out/x.html (1.0 MB)", ("export", 96), ("export", 98)),
    ("pdf: out/x.pdf (1.0 MB)", ("export", 98), ("done", 100)),
    ("kept=2", ("names", 85), ("names", 85)),  # never back
    (E + "  text: recognizing 10% · speakers: 10% · 0:10", ("asr", 40), ("asr", 40)),
    ("something else", ("audio", 2), ("audio", 2)),
])
def test_parse_progress(line, before, after):
    assert parse_progress(line, *before) == after


def test_warning_in():
    assert warning_in(E + "the summary failed (see above) — building the transcript without it") \
        == "the summary failed (see above) — building the transcript without it"
    assert warning_in(E + "naming the speakers failed (see above)").startswith("naming the speakers")
    assert warning_in("html: x") is None


def make_job(store, tmp_path, name="Board meeting.mp4", **opts):
    src = tmp_path / name
    src.write_bytes(b"video")
    return store.create(src, name, Options(**opts), "upload")


def test_build_command(tmp_path):
    store = JobStore(tmp_path / "jobs")
    job = make_job(store, tmp_path, summary="none", names=False)
    d = store.job_dir(job.id)
    assert build_command(FAKE_TRANSCRIBE, job, d) == [
        str(FAKE_TRANSCRIBE), "auto", str(d / "source.mp4"), str(d / "board-meeting"),
        "--title", "Board meeting", "--no-summary", "--no-names"]


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs")


def pool_for(store, **kw):
    kw.setdefault("tick", 0.05)
    kw.setdefault("grace_s", 2)
    return WorkerPool(store, FAKE_TRANSCRIBE, **kw)


def test_run_job_done(store, tmp_path):
    job = make_job(store, tmp_path)
    pool_for(store).run_job(job)
    job = store.load(job.id)
    d = store.job_dir(job.id)
    assert (job.state, job.stage, job.progress, job.exit_code, job.error) == ("done", "done", 100, 0, None)
    assert job.artifacts == ["transcript.md", "board-meeting.html", "board-meeting.pdf"]
    assert job.attempts == 1 and job.pid is None and job.started and job.finished
    assert not (d / "board-meeting" / ".work").exists() and (d / "source.mp4").exists()
    assert (d / "board-meeting.title").read_text().strip() == "Board meeting"
    log = (d / "log.txt").read_text()
    assert log.startswith("--- attempt 1 ") and "text: recognizing 40%" in log


def test_run_job_keeps_work_and_drops_source(store, tmp_path):
    job = make_job(store, tmp_path)
    pool_for(store, keep_work=True, keep_source=False).run_job(job)
    d = store.job_dir(job.id)
    assert (d / "board-meeting" / ".work").is_dir() and not (d / "source.mp4").exists()


def test_run_job_failed(store, tmp_path):
    job = make_job(store, tmp_path)
    pool_for(store, env={"FAKE_FAIL": "1"}).run_job(job)
    job = store.load(job.id)
    assert job.state == "failed" and job.exit_code == 1 and job.error.startswith("speech recognition failed")
    assert job.stage == "asr" and job.artifacts == []


def test_run_job_warning(store, tmp_path):
    job = make_job(store, tmp_path)
    pool_for(store, env={"FAKE_SUMMARY_FAIL": "1"}).run_job(job)
    job = store.load(job.id)
    assert job.state == "done" and job.warnings == ["the summary failed (see above) — building the transcript "
                                                    "without it"]


def test_run_job_timeout(store, tmp_path):
    job = make_job(store, tmp_path)
    t0 = time.monotonic()
    pool_for(store, timeout_s=1, env={"FAKE_SLEEP": "30"}).run_job(job)
    job = store.load(job.id)
    assert job.state == "failed" and job.error == "timeout after 1 s" and time.monotonic() - t0 < 10


def gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_cancel_running(store, tmp_path):
    job = make_job(store, tmp_path)
    pool = pool_for(store, env={"FAKE_SLEEP": "30"})
    t = threading.Thread(target=pool.run_job, args=(job,))
    t.start()
    pid = wait_for(lambda: store.load(job.id).pid)
    pool.cancel(job.id)
    t.join(10)
    job = store.load(job.id)
    assert job.state == "failed" and job.error == "cancelled"
    wait_for(lambda: gone(pid), timeout=5)


def test_stop_group_waits_for_the_whole_group():
    import subprocess

    from serve_worker import group_alive, stop_group
    # bash dies on SIGTERM, the step it started ignores it: only SIGKILL ends the group
    proc = subprocess.Popen(["bash", "-c", "(trap '' TERM; sleep 30) & sleep 30"], start_new_session=True)
    time.sleep(0.3)
    stop_group(proc.pid, 0.5, proc)
    assert not group_alive(proc.pid)


def test_a_new_attempt_stops_the_old_one_and_its_lock(store, tmp_path):
    import subprocess
    job = make_job(store, tmp_path)
    d = store.job_dir(job.id)
    # a leftover attempt: its command line names the job folder (`; true` keeps bash from exec-ing sleep)
    old = subprocess.Popen(["bash", "-c", f"sleep 30; true {d}"], start_new_session=True)
    lock = d / "board-meeting" / ".work" / ".asr.lock"
    lock.mkdir(parents=True)
    store.update(job.id, pid=old.pid)
    pool_for(store).run_job(store.load(job.id))
    old.wait(5)
    assert store.load(job.id).state == "done" and not lock.exists()


def test_an_unrelated_process_with_the_old_pid_is_left_alone(store, tmp_path):
    import subprocess
    job = make_job(store, tmp_path)
    other = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        store.update(job.id, pid=other.pid)
        pool_for(store).run_job(store.load(job.id))
        assert other.poll() is None and store.load(job.id).state == "done"
    finally:
        other.kill()
        other.wait()


def test_a_worker_error_stops_the_pipeline(store, tmp_path, monkeypatch):
    job = make_job(store, tmp_path)
    pool = pool_for(store, env={"FAKE_SLEEP": "30"})
    monkeypatch.setattr(pool, "_follow", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        pool.run_job(job)
    pid = store.load(job.id).pid
    wait_for(lambda: gone(pid), timeout=5)
    assert job.id not in pool.running


def test_cancel_without_a_process_only_for_queued(store, tmp_path):
    job = make_job(store, tmp_path)
    store.update(job.id, state="running")
    with pytest.raises(ValueError):
        pool_for(store).cancel(job.id)
    assert store.load(job.id).state == "running"


def test_child_env_drops_service_secrets(monkeypatch):
    from serve_worker import child_env
    monkeypatch.setenv("MT_SERVE_TOKEN", "t")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "s")
    monkeypatch.setenv("MT_LANGUAGE", "ru")
    env = child_env()
    assert "MT_SERVE_TOKEN" not in env and "AWS_SECRET_ACCESS_KEY" not in env and env["MT_LANGUAGE"] == "ru"


def test_cancel_queued(store, tmp_path):
    job = make_job(store, tmp_path)
    pool = pool_for(store)
    assert pool.cancel(job.id).state == "failed"
    with pytest.raises(ValueError):
        pool.cancel(job.id)


def test_shutdown_requeues(store, tmp_path):
    job = make_job(store, tmp_path)
    pool = pool_for(store, env={"FAKE_SLEEP": "30"})
    pool.submit(job.id)
    pool.start()
    wait_for(lambda: store.load(job.id).pid)
    pool.shutdown()
    job = store.load(job.id)
    assert job.state == "queued" and job.attempts == 0 and job.pid is None
    assert store.recover() == [job.id]


def test_skips_deleted_and_cancelled(store, tmp_path):
    a, b = make_job(store, tmp_path, "a.mp4"), make_job(store, tmp_path, "b.mp4")
    store.delete(a.id)
    store.update(b.id, state="failed")
    pool = pool_for(store)
    for j in (a.id, b.id):
        pool.submit(j)
    pool.start()
    wait_for(pool.queue.empty)
    time.sleep(0.2)
    pool.shutdown()
    assert store.load(b.id).attempts == 0


@pytest.mark.parametrize(("workers", "low", "high"), [(1, 2.0, 30), (2, 0, 1.9)])
def test_workers_run_in_parallel(store, tmp_path, workers, low, high):
    jobs = [make_job(store, tmp_path, f"{n}.mp4") for n in range(2)]
    pool = pool_for(store, workers=workers, env={"FAKE_SLEEP": "1"})
    t0 = time.monotonic()
    for j in jobs:
        pool.submit(j.id)
    pool.start()
    try:
        wait_for(lambda: all(store.load(j.id).state == "done" for j in jobs), timeout=30)
    finally:
        pool.shutdown()
    assert low <= time.monotonic() - t0 < high


def test_webhook(store, tmp_path):
    got = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        job = make_job(store, tmp_path)
        pool_for(store, webhook_url=f"http://127.0.0.1:{server.server_port}/hook").run_job(job)
    finally:
        server.shutdown()
    assert len(got) == 1 and got[0]["event"] == "job.done" and got[0]["job"]["id"] == job.id


def test_webhook_failure_does_not_fail_the_job(store, tmp_path):
    job = make_job(store, tmp_path)
    pool_for(store, webhook_url="http://127.0.0.1:9/nothing").run_job(job)
    assert store.load(job.id).state == "done"


class FakeS3:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def upload_file(self, path, bucket, key, ExtraArgs):  # noqa: N803 — boto3's name
        if self.fail:
            raise OSError("no network")
        self.calls.append((os.path.basename(path), bucket, key, ExtraArgs["ContentType"]))


def test_s3_upload(store, tmp_path):
    job = make_job(store, tmp_path)
    client = FakeS3()
    pool_for(store, s3=S3Sink("bkt", "meetings/", client=client)).run_job(job)
    job = store.load(job.id)
    assert job.s3 == [f"s3://bkt/meetings/{job.id}/{n}" for n in job.artifacts]
    assert client.calls[0] == ("transcript.md", "bkt", f"meetings/{job.id}/transcript.md",
                               "text/markdown; charset=utf-8")
    assert client.calls[2][3] == "application/pdf"


def test_s3_failure_is_a_warning(store, tmp_path):
    job = make_job(store, tmp_path)
    pool_for(store, s3=S3Sink("bkt", client=FakeS3(fail=True))).run_job(job)
    job = store.load(job.id)
    assert job.state == "done" and job.s3 == [] and job.warnings[0].startswith("S3 upload failed: OSError")

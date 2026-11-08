"""Workers of the service: each runs `transcribe auto` for one job as a child process, follows its log for the stage
and percent, and on the end cleans up, uploads to S3 and calls the webhook."""
import json
import mimetypes
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from serve_jobs import Job, JobStore, Options, now_utc, options_to_args

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PREFIX = "[transcribe] "
STATUS = re.compile(r"^\[transcribe\]\s+text: (?P<t>.+?) · speakers: (?P<s>.+?) · ")
# (regex, stage, percent): the lines the pipeline prints, in the order they come
STEPS = [
    (re.compile(r"^\[transcribe\] recognition \("), "asr", 5),
    (re.compile(r"^turns=\d+ speakers="), "frames", 72),
    (re.compile(r"^candidates=\d+"), "frames", 72),
    (re.compile(r"^kept=\d+"), "frames", 75),
    (re.compile(r"^sheets=\d+"), "frames", 78),
    (re.compile(r"^utterances=\d+ frames=\d+"), "names", 85),
    (re.compile(r"^\[transcribe\] (looking for introductions|names from the (screen|text))"), "names", 87),
    (re.compile(r"^\[transcribe\] writing the summary with "), "summary", 90),
    (re.compile(r"^\[transcribe\] summary written to "), "export", 96),
    (re.compile(r"^html: "), "export", 98),
    (re.compile(r"^pdf: "), "done", 100),
]
WARNING = re.compile(r"^\[transcribe\] (naming the speakers failed|the summary failed)")


def log(msg):
    print(f"\033[1;34m[transcribe]\033[0m {msg}", file=sys.stderr, flush=True)


def clean(line: str) -> str:
    return ANSI.sub("", line).rstrip()


def _part_pct(text: str) -> int:
    """Percent of one half of the status line: "recognizing 42%", "done", "loading model", "17%"."""
    if text.strip() == "done":
        return 100
    m = re.search(r"(\d+)%\s*$", text)
    return int(m.group(1)) if m else 0


def parse_progress(line: str, stage: str, pct: int) -> tuple[str, int]:
    """The (stage, percent) after one log line; the percent never goes back."""
    line = clean(line)
    m = STATUS.match(line)
    if m:
        new = 5 + int(min(_part_pct(m["t"]), _part_pct(m["s"])) * 0.65)
        return ("asr", new) if new > pct else (stage, pct)
    for rx, st, p in STEPS:
        if rx.match(line):
            return (st, p) if p > pct else (stage, pct)
    return stage, pct


def warning_in(line: str) -> str | None:
    line = clean(line)
    return line.removeprefix(PREFIX) if WARNING.match(line) else None


def last_line(path: Path, limit: int = 300) -> str:
    try:
        lines = [clean(x) for x in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    except FileNotFoundError:
        return ""
    lines = [x for x in lines if x.strip() and not x.startswith("--- attempt ")]
    return lines[-1].removeprefix(PREFIX)[:limit] if lines else ""


def build_command(transcribe: Path, job: Job, job_dir: Path) -> list[str]:
    opts = Options(**job.options)
    src = job_dir / (f"source.{job.ext}" if job.ext else "source")
    return [str(transcribe), "auto", str(src), str(job_dir / job.out_dir), *options_to_args(opts, job.stem)]


class S3Sink:
    """Copies a finished job's artifacts to s3://bucket/<prefix><job id>/<name>; MinIO through `endpoint`."""

    def __init__(self, bucket: str, prefix: str = "", endpoint: str | None = None, region: str | None = None,
                 client=None):
        self.bucket, self.prefix = bucket, prefix
        if client is None:
            import boto3  # only when S3 is configured: the extra `serve` brings it
            client = boto3.client("s3", endpoint_url=endpoint or None, region_name=region or None)
        self.client = client

    def upload(self, job: Job, job_dir: Path) -> list[str]:
        urls = []
        for name in job.artifacts:
            key = f"{self.prefix}{job.id}/{name}"
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if name.endswith(".md"):
                ctype = "text/markdown; charset=utf-8"
            self.client.upload_file(str(job_dir / job.out_dir / name), self.bucket, key,
                                    ExtraArgs={"ContentType": ctype})
            urls.append(f"s3://{self.bucket}/{key}")
        return urls


class WorkerPool:
    def __init__(self, store: JobStore, transcribe: Path, workers: int = 1, timeout_s: int = 0,
                 keep_work: bool = False, keep_source: bool = True, webhook_url: str | None = None,
                 s3: S3Sink | None = None, stop: threading.Event | None = None, env: dict | None = None,
                 tick: float = 1.0, grace_s: float = 10.0):
        self.store, self.transcribe, self.workers = store, Path(transcribe), workers
        self.timeout_s, self.keep_work, self.keep_source = timeout_s, keep_work, keep_source
        self.webhook_url, self.s3, self.env = webhook_url, s3, env or {}
        self.stop = stop or threading.Event()
        self.tick, self.grace_s = tick, grace_s
        self.queue: queue.Queue[str] = queue.Queue()
        self.running: dict[str, subprocess.Popen] = {}
        self.cancelled: set[str] = set()
        self.threads: list[threading.Thread] = []

    def submit(self, job_id: str) -> None:
        self.queue.put(job_id)

    def start(self) -> None:
        for n in range(self.workers):
            t = threading.Thread(target=self._loop, name=f"worker-{n + 1}", daemon=True)
            t.start()
            self.threads.append(t)

    def _loop(self):
        while not self.stop.is_set():
            try:
                job_id = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                job = self.store.load(job_id)
            except KeyError:
                continue  # deleted while it waited
            if job.state != "queued":
                continue  # cancelled while it waited, or a duplicate submit
            try:
                self.run_job(job)
            except Exception as e:  # the worker thread must survive any job
                log(f"job {job_id}: worker error {e.__class__.__name__}: {e}")
                try:
                    self.store.update(job_id, state="failed", error=f"service error: {e}", finished=now_utc(),
                                      pid=None)
                except KeyError:
                    pass

    def _start(self, job: Job) -> tuple[Job, subprocess.Popen, int] | None:
        """queued → running and the child started, or None when the job is no longer queued."""
        d = self.store.job_dir(job.id)
        with self.store.lock:
            job = self.store.load(job.id)
            if job.state != "queued":
                return None
            self.store.transition(job, "running")
            job.started, job.attempts, job.stage, job.progress = now_utc(), job.attempts + 1, "audio", 2
            job.error = job.exit_code = None
            job.warnings = []
            with open(d / "log.txt", "ab") as f:
                f.write(f"--- attempt {job.attempts} {job.started} ---\n".encode())
                offset = f.tell()
                proc = subprocess.Popen(build_command(self.transcribe, job, d), stdout=f, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, cwd=d, start_new_session=True,
                                        env=os.environ | {"PYTHONUNBUFFERED": "1"} | self.env)
            job.pid = proc.pid
            self.running[job.id] = proc
            self.store.save(job)
        log(f"job {job.id}: started (attempt {job.attempts})")
        return job, proc, offset

    def run_job(self, job: Job) -> None:
        started = self._start(job)
        if started is None:
            return
        job, proc, offset = started
        d = self.store.job_dir(job.id)
        logfile = d / "log.txt"
        t0, rest, reason = time.monotonic(), b"", None
        stage, pct, warnings = job.stage, job.progress, list(job.warnings)

        def follow():
            nonlocal offset, rest, stage, pct
            with open(logfile, "rb") as f:
                f.seek(offset)
                data = f.read()
                offset = f.tell()
            *lines, rest_ = (rest + data).split(b"\n")
            rest = rest_
            changed = False
            for raw in lines:
                line = raw.decode("utf-8", errors="replace")
                s, p = parse_progress(line, stage, pct)
                w = warning_in(line)
                if (s, p) != (stage, pct) or w:
                    stage, pct, changed = s, p, True
                if w:
                    warnings.append(w)
            if changed:
                self.store.update(job.id, stage=stage, progress=pct, warnings=warnings)

        while True:
            try:
                proc.wait(timeout=self.tick)
                break
            except subprocess.TimeoutExpired:
                pass
            follow()
            if reason is None and self.timeout_s and time.monotonic() - t0 > self.timeout_s:
                reason = "timeout"
                self._terminate(proc)
            elif reason is None and self.stop.is_set():
                reason = "shutdown"
                self._terminate(proc)
        follow()
        with self.store.lock:
            self.running.pop(job.id, None)
            cancelled = job.id in self.cancelled
            self.cancelled.discard(job.id)
            try:
                job = self.store.load(job.id)
            except KeyError:
                return  # deleted while running
        rc = proc.returncode
        job.pid, job.exit_code, job.stage, job.progress, job.warnings = None, rc, stage, pct, warnings
        out = d / job.out_dir
        if reason == "shutdown" and not cancelled:
            self.store.transition(job, "queued")  # the next start runs it again; the attempt is not counted
            job.attempts, job.stage = max(job.attempts - 1, 0), "queued"
            self.store.save(job)
            log(f"job {job.id}: stopped by the service shutdown, queued again")
            return
        if cancelled:
            self.store.transition(job, "failed")
            job.error = "cancelled"
        elif reason == "timeout":
            self.store.transition(job, "failed")
            job.error = f"timeout after {self.timeout_s} s"
        elif rc == 0 and (out / "transcript.md").is_file():
            self.store.transition(job, "done")
            job.stage, job.progress = "done", 100
            job.artifacts = self.store.artifacts_of(job)
            self.cleanup(job, d)
            if self.s3:
                try:
                    job.s3 = self.s3.upload(job, d)
                except Exception as e:
                    job.warnings.append(f"S3 upload failed: {e.__class__.__name__}: {e}")
        else:
            self.store.transition(job, "failed")
            job.error = last_line(logfile) or f"exit {rc}"
        job.finished = now_utc()
        self.store.save(job)
        log(f"job {job.id}: {job.state}" + (f" ({job.error})" if job.error else ""))
        self.webhook(job)

    def _terminate(self, proc: subprocess.Popen) -> None:
        """The whole process group: bash, and the whisperx / diarize / step it runs."""
        for sig, wait in ((signal.SIGTERM, self.grace_s), (signal.SIGKILL, 5)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                return
            try:
                proc.wait(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                continue

    def cancel(self, job_id: str) -> Job:
        """A queued job fails at once; a running one is stopped and fails as "cancelled"."""
        with self.store.lock:
            job = self.store.load(job_id)
            proc = self.running.get(job_id)
            if proc is None:
                self.store.transition(job, "failed")  # ValueError unless queued
                job.error, job.finished, job.stage = "cancelled", now_utc(), "failed"
                self.store.save(job)
                return job
            self.cancelled.add(job_id)
        threading.Thread(target=self._terminate, args=(proc,), daemon=True).start()
        return job

    def is_running(self, job_id: str) -> bool:
        return job_id in self.running

    def cleanup(self, job: Job, job_dir: Path) -> None:
        if not self.keep_work:
            shutil.rmtree(job_dir / job.out_dir / ".work", ignore_errors=True)
        if not self.keep_source:
            self.store.source_path(job).unlink(missing_ok=True)

    def webhook(self, job: Job) -> None:
        if not self.webhook_url:
            return
        body = json.dumps({"event": f"job.{job.state}", "job": job.to_dict()}, ensure_ascii=False).encode()
        req = urllib.request.Request(self.webhook_url, data=body, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
        except Exception as e:
            log(f"job {job.id}: webhook {self.webhook_url} failed: {e}")

    def shutdown(self) -> None:
        """Stops the running jobs (they go back to the queue) and waits for the workers."""
        self.stop.set()
        for t in self.threads:
            t.join(self.grace_s + 10)


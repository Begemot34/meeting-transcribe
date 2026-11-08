"""Jobs of the service (./transcribe serve): one folder per recording under <data>/jobs, its state in job.json.
No database: the folders are the queue, and a restart reads them back."""
import dataclasses
import errno
import fcntl
import json
import os
import re
import shutil
import sys
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

ALLOWED_EXT = {"mp4", "m4v", "mov", "mkv", "webm", "avi", "mp3", "m4a", "wav", "flac", "ogg", "opus", "aac"}
SUMMARY_BACKENDS = {"claude", "codex", "opencode", "local", "api", "none"}
STATES = ("queued", "running", "done", "failed")
ID_RE = re.compile(r"^[\w.-]{1,120}$")  # unicode \w: slugs keep Cyrillic letters
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LANG_RE = re.compile(r"^[a-z]{2,3}$")
INCOMING = ".incoming"
# allowed state changes; anything else is a programming or API error (409)
TRANSITIONS = {("queued", "running"), ("queued", "failed"), ("running", "done"), ("running", "failed"),
               ("running", "queued"), ("failed", "queued"), ("done", "queued")}


def log(msg):
    print(f"\033[1;34m[transcribe]\033[0m {msg}", file=sys.stderr, flush=True)


class BadOptions(ValueError):
    pass


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclasses.dataclass
class Options:
    title: str | None = None
    date: str | None = None
    language: str | None = None
    summary: str | None = None  # a backend name, "none", or None = the setup default (MT_LLM)
    names: bool = True


def _flag(value, key):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "1", "yes"):
        return True
    if isinstance(value, str) and value.lower() in ("false", "0", "no"):
        return False
    raise BadOptions(f"{key}: expected true or false, got {value!r}")


def parse_options(raw: dict) -> Options:
    """Options of a job from a sidecar file or the upload's query: unknown keys and bad values are errors."""
    if not isinstance(raw, dict):
        raise BadOptions("expected a JSON object")
    fields = {f.name for f in dataclasses.fields(Options)}
    unknown = sorted(set(raw) - fields)
    if unknown:
        raise BadOptions(f"unknown option(s): {', '.join(unknown)}")
    o = Options()
    title = raw.get("title")
    if title not in (None, ""):
        if not isinstance(title, str):
            raise BadOptions("title: expected a string")
        title = "".join(c for c in title if unicodedata.category(c)[0] != "C").strip()
        if not title or len(title) > 200:
            raise BadOptions("title: 1–200 characters")
        o.title = title
    for key, rx in (("date", DATE_RE), ("language", LANG_RE)):
        v = raw.get(key)
        if v in (None, ""):
            continue
        if not isinstance(v, str) or not rx.match(v):
            raise BadOptions(f"{key}: bad value {v!r}")
        setattr(o, key, v)
    summary = raw.get("summary")
    if summary not in (None, ""):
        if summary not in SUMMARY_BACKENDS:
            raise BadOptions(f"summary: one of {', '.join(sorted(SUMMARY_BACKENDS))}")
        o.summary = summary
    if raw.get("names") not in (None, ""):
        o.names = _flag(raw["names"], "names")
    return o


def options_to_args(o: Options, default_title: str) -> list[str]:
    """The `transcribe auto` flags for these options; the title is always passed (the file is renamed)."""
    args = ["--title", o.title or default_title]
    if o.date:
        args += ["--date", o.date]
    if o.language:
        args += ["--language", o.language]
    if o.summary == "none":
        args.append("--no-summary")
    elif o.summary:
        args += ["--summary", o.summary]
    if not o.names:
        args.append("--no-names")
    return args


def slugify(stem: str, limit: int = 40) -> str:
    s = unicodedata.normalize("NFC", stem).lower()
    s = re.sub(r"[^\w]+", "-", s).strip("-._")[:limit].strip("-._")
    return s or "meeting"


def split_name(filename: str) -> tuple[str, str]:
    """("Board meeting", "mp4") for "Board meeting.MP4"; the extension is lowercased, "" when there is none."""
    stem, dot, ext = filename.rpartition(".")
    if not dot or not stem:
        return filename, ""
    return stem, ext.lower()


def allowed(filename: str) -> bool:
    return split_name(filename)[1] in ALLOWED_EXT


def move(src: Path, dst: Path) -> None:
    """Atomic within one filesystem. Across filesystems (an inbox on another mount) a copy under a .part name,
    renamed when complete — so `dst` is never a partial file — and then the source is deleted."""
    try:
        os.replace(src, dst)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        part = dst.with_name(dst.name + ".part")
        try:
            shutil.copyfile(src, part)
            os.replace(part, dst)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        try:
            src.unlink()
        except BaseException:
            dst.unlink(missing_ok=True)  # the recording stays where it was, not in two places
            raise


@dataclasses.dataclass
class Job:
    id: str
    state: str
    created: str
    source: dict  # {"name": the original filename, "size": bytes, "origin": "inbox" | "upload"}
    options: dict
    out_dir: str  # the slug: <job folder>/<out_dir> is what `transcribe auto` writes
    started: str | None = None
    finished: str | None = None
    stage: str = "queued"  # queued|audio|asr|frames|names|summary|export|done
    progress: int = 0
    attempts: int = 0
    error: str | None = None
    exit_code: int | None = None
    pid: int | None = None
    artifacts: list[str] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)
    s3: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

    @property
    def ext(self) -> str:
        return split_name(self.source["name"])[1]

    @property
    def stem(self) -> str:
        return split_name(self.source["name"])[0]


class JobStore:
    def __init__(self, root: Path, max_attempts: int = 2):
        self.root = Path(root)
        self.max_attempts = max_attempts
        self.lock = threading.RLock()
        self._owner = None  # the open .lock file while this process owns the store

    def acquire(self) -> None:
        """One service per jobs folder: a second one would requeue the first one's running jobs and delete its
        uploads in progress. The OS drops the lock when the process ends, however it ends."""
        self.root.mkdir(parents=True, exist_ok=True)
        f = open(self.root / ".lock", "w")  # held open for the life of the process
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise RuntimeError(f"another service already works with {self.root}") from None
        self._owner = f

    def release(self) -> None:
        if self._owner is not None:
            self._owner.close()
            self._owner = None

    @property
    def incoming(self) -> Path:
        return self.root / INCOMING

    def job_dir(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not ID_RE.match(job_id) or job_id.startswith("."):
            raise KeyError(job_id)
        d = self.root / job_id
        if d.resolve().parent != self.root.resolve():
            raise KeyError(job_id)
        return d

    def new_id(self, stem: str, now: datetime | None = None) -> str:
        """YYYYMMDD-HHMMSS-<slug>, then -2, -3… while the folder exists. Local time: TZ sets the names."""
        base = f"{(now or datetime.now()):%Y%m%d-%H%M%S}-{slugify(stem)}"
        job_id, n = base, 1
        while (self.root / job_id).exists() or (self.incoming / job_id).exists():
            n += 1
            job_id = f"{base}-{n}"
        return job_id

    @staticmethod
    def _write(d: Path, job: Job) -> None:
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / "job.json")

    def _publish(self, stage: Path, job: Job) -> None:
        """A staged job becomes visible in one rename (both under jobs/, one filesystem)."""
        with self.lock:
            if (self.root / job.id).exists():  # only after a restart: the name was taken meanwhile
                job.id = self.new_id(job.stem)
                self._write(stage, job)
            os.replace(stage, self.root / job.id)

    def _new(self, src: Path, name: str, origin: str, options: dict, sidecar: Path | None,
             error: str | None = None) -> Job:
        """Builds the job in jobs/.incoming/<id>/ — job.json first, the sidecar, the recording last — and publishes
        the folder when it is complete. A crash before the end leaves a staged folder that recover() finishes
        (the recording is there) or drops (it is still in the inbox); nothing gets lost or stranded."""
        stem, ext = split_name(name)
        size = src.stat().st_size
        self.incoming.mkdir(parents=True, exist_ok=True)
        with self.lock:
            job_id = self.new_id(stem)
            stage = self.incoming / job_id
            stage.mkdir()
        job = Job(id=job_id, state="queued", created=now_utc(),
                  source={"name": name, "size": size, "origin": origin}, options=options, out_dir=slugify(stem))
        if error:
            job.state, job.stage, job.error, job.finished = "failed", "failed", error, now_utc()
        side = stage / "sidecar.json"
        try:
            self._write(stage, job)
            if sidecar is not None and sidecar.exists():
                move(sidecar, side)
            move(src, stage / (f"source.{ext}" if ext else "source"))  # = source_path()
        except BaseException:
            if side.exists() and sidecar is not None:
                try:
                    move(side, sidecar)  # back next to the recording, which did not move
                except OSError:
                    pass
            shutil.rmtree(stage, ignore_errors=True)
            raise
        self._publish(stage, job)
        return job

    def create(self, src: Path, name: str, options: Options, origin: str, sidecar: Path | None = None) -> Job:
        """Moves the recording (and its sidecar) into a new job folder; the job is queued."""
        return self._new(src, name, origin, dataclasses.asdict(options), sidecar)

    def create_failed(self, src: Path, name: str, origin: str, error: str, sidecar: Path | None = None) -> Job:
        """A recording that cannot run as given (a bad sidecar): it leaves the inbox as a failed job."""
        return self._new(src, name, origin, dataclasses.asdict(Options()), sidecar, error=error)

    def load(self, job_id: str) -> Job:
        p = self.job_dir(job_id) / "job.json"
        try:
            return Job.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except FileNotFoundError:
            raise KeyError(job_id) from None

    def save(self, job: Job) -> None:
        d = self.job_dir(job.id)
        with self.lock:
            self._write(d, job)

    def update(self, job_id: str, **fields) -> Job:
        with self.lock:
            job = self.load(job_id)
            for k, v in fields.items():
                setattr(job, k, v)
            self.save(job)
            return job

    @staticmethod
    def transition(job: Job, new_state: str) -> None:
        if (job.state, new_state) not in TRANSITIONS:
            raise ValueError(f"job {job.id} is {job.state}, cannot become {new_state}")
        job.state = new_state

    def retry(self, job_id: str) -> Job:
        with self.lock:
            job = self.load(job_id)
            if job.state not in ("done", "failed"):
                raise ValueError(f"job {job.id} is {job.state}: only a finished job can run again")
            if not self.source_path(job).is_file():
                raise ValueError(f"job {job.id}: the recording was deleted after the run, upload it again")
            self.transition(job, "queued")
            job.attempts, job.error, job.progress, job.stage, job.exit_code = 0, None, 0, "queued", None
            job.artifacts, job.s3, job.warnings, job.started, job.finished = [], [], [], None, None
            self.save(job)
            return job

    def all(self, limit: int | None = None) -> list[Job]:
        jobs = []
        if not self.root.is_dir():
            return jobs
        for d in self.root.iterdir():
            if d.name.startswith(".") or not (d / "job.json").is_file():
                continue
            try:
                jobs.append(self.load(d.name))
            except (KeyError, ValueError, TypeError):
                continue  # a half-written or foreign folder
        jobs.sort(key=lambda j: (j.created, j.id), reverse=True)
        return jobs[:limit] if limit else jobs

    def delete(self, job_id: str) -> None:
        d = self.job_dir(job_id)
        if not d.is_dir():
            raise KeyError(job_id)
        shutil.rmtree(d)

    def recover(self) -> list[str]:
        """At startup: jobs that were running go back to the queue (or fail after max_attempts starts); partial
        uploads are removed; a job staged when the service died is published if its recording got there, else
        dropped (the recording is still in the inbox). Returns the queued ids, oldest first. The pid of an
        interrupted job stays: the next start stops what is left of that run."""
        if self.incoming.is_dir():
            for p in self.incoming.iterdir():
                if not p.is_dir():
                    p.unlink(missing_ok=True)  # an upload cut off
                    continue
                try:
                    job = Job.from_dict(json.loads((p / "job.json").read_text(encoding="utf-8")))
                    ext = job.ext
                    complete = (p / (f"source.{ext}" if ext else "source")).is_file()
                except (OSError, ValueError, TypeError, KeyError):
                    complete = False
                if complete:
                    self._publish(p, job)
                else:
                    shutil.rmtree(p, ignore_errors=True)
        queued = []
        for job in sorted(self.all(), key=lambda j: (j.created, j.id)):
            if job.state == "running":
                if job.attempts < self.max_attempts:
                    job.state, job.stage = "queued", "queued"
                else:
                    job.state, job.finished = "failed", now_utc()
                    job.error = f"interrupted {job.attempts} times by a service restart"
                self.save(job)
            if job.state == "queued":
                queued.append(job.id)
        return queued

    def expire(self, keep_days: int, now: datetime | None = None) -> list[str]:
        if keep_days <= 0:
            return []
        limit = (now or datetime.now(timezone.utc)) - timedelta(days=keep_days)
        gone = []
        for old in self.all():
            with self.lock:  # again under the lock: a retry may have queued it since the listing
                try:
                    job = self.load(old.id)
                except KeyError:
                    continue
                if job.state in ("done", "failed") and job.finished and datetime.fromisoformat(job.finished) < limit:
                    self.delete(job.id)
                    gone.append(job.id)
        return gone

    def source_path(self, job: Job) -> Path:
        return self.job_dir(job.id) / (f"source.{job.ext}" if job.ext else "source")

    def artifacts_of(self, job: Job) -> list[str]:
        out = self.job_dir(job.id) / job.out_dir
        names = ["transcript.md", f"{job.out_dir}.html", f"{job.out_dir}.pdf"]
        return [n for n in names if (out / n).is_file()]

    def artifact_path(self, job: Job, name: str) -> Path:
        d = self.job_dir(job.id)
        if name == "log.txt" and (d / "log.txt").is_file():
            return d / "log.txt"
        if name in job.artifacts and name in self.artifacts_of(job):
            return d / job.out_dir / name
        raise KeyError(name)

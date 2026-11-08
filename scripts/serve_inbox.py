"""The inbox of the service: polling, not inotify — events do not cross bind mounts, SMB or NFS reliably. A file is
taken once its size and mtime have not changed for `stable_s` seconds, so a copy in progress is left alone."""
import dataclasses
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

from serve_jobs import BadOptions, JobStore, allowed, log, parse_options

IGNORED_SUFFIXES = (".part", ".tmp", ".crdownload", ".filepart", ".download", ".json")
RETRY_AFTER_S = 600  # a file that could not be taken (no permission, disk full) is tried again this much later


def ignored(name: str) -> bool:
    """Temporary names of downloads and copy tools, hidden files, sidecars and unknown formats."""
    return name.startswith((".", "~", "$")) or name.lower().endswith(IGNORED_SUFFIXES) or not allowed(name)


@dataclasses.dataclass
class Seen:
    size: int
    mtime: float
    since: float  # monotonic time this (size, mtime) was first seen


def scan_once(inbox: Path, seen: dict[str, Seen], now: float, stable_s: float) -> list[Path]:
    """Updates `seen` from a listing of the inbox and returns the files ready to take, oldest first."""
    present, ready = set(), []
    try:
        entries = list(inbox.iterdir())
    except FileNotFoundError:
        entries = []
    for p in entries:
        name = p.name
        if ignored(name):
            continue
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        if not p.is_file():
            continue
        present.add(name)
        s = seen.get(name)
        if s is None or (s.size, s.mtime) != (st.st_size, st.st_mtime):
            seen[name] = Seen(st.st_size, st.st_mtime, now)
            continue
        if st.st_size > 0 and now - s.since >= stable_s:
            ready.append((st.st_mtime, p))
    for name in set(seen) - present:
        del seen[name]
    return [p for _, p in sorted(ready)]


def sidecar_for(video: Path) -> Path | None:
    p = video.with_name(video.name + ".json")
    return p if p.is_file() else None


class Scanner(threading.Thread):
    def __init__(self, inbox: Path, store: JobStore, submit: Callable[[str], None], poll_s: float, stable_s: float,
                 stop: threading.Event, notify: Callable | None = None, retry_after_s: float = RETRY_AFTER_S):
        super().__init__(name="inbox", daemon=True)
        self.inbox, self.store, self.submit, self.notify = Path(inbox), store, submit, notify
        self.poll_s, self.stable_s, self.stop, self.retry_after_s = poll_s, stable_s, stop, retry_after_s
        self.seen: dict[str, Seen] = {}
        self.failed: dict[str, float] = {}  # name → monotonic time it may be tried again

    def take(self, path: Path) -> None:
        sidecar = sidecar_for(path)
        try:
            raw = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar else {}
            options = parse_options(raw)
        except (ValueError, BadOptions) as e:  # json.JSONDecodeError is a ValueError
            job = self.store.create_failed(path, path.name, "inbox", f"bad sidecar: {e}", sidecar)
            log(f"inbox: {path.name} → job {job.id} failed: {job.error}")
            if self.notify:
                self.notify(job)
            return
        job = self.store.create(path, path.name, options, "inbox", sidecar)
        log(f"inbox: {path.name} → job {job.id}")
        self.submit(job.id)

    def scan(self) -> None:
        now = time.monotonic()
        for path in scan_once(self.inbox, self.seen, now, self.stable_s):
            if self.failed.get(path.name, 0) > now:
                continue
            self.seen.pop(path.name, None)
            try:
                self.take(path)
                self.failed.pop(path.name, None)
            except FileNotFoundError:
                continue  # removed between the listing and the move
            except Exception as e:  # one bad file must not stop the inbox, nor be retried every few seconds
                self.failed[path.name] = now + self.retry_after_s
                log(f"inbox: {path.name} not taken, next try in {self.retry_after_s / 60:.0f} min: "
                    f"{e.__class__.__name__}: {e}")

    def run(self):
        while not self.stop.is_set():
            try:
                self.scan()
            except Exception as e:
                log(f"inbox: scan failed: {e.__class__.__name__}: {e}")
            self.stop.wait(self.poll_s)

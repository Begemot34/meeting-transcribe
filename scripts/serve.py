"""./transcribe serve: watches the inbox (<data>/inbox) for recordings, runs `transcribe auto` on each in a queue with N workers,
and serves the results through a web UI and an HTTP API (plus an optional webhook and S3 upload).

    serve.py [--data DIR] [--host H] [--port N] [--workers N]

Flags win over the variables of SETTINGS.md, Service, and those over the defaults."""
import argparse
import dataclasses
import os
import signal
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from serve_inbox import Scanner  # noqa: E402
from serve_jobs import INCOMING, JobStore, log  # noqa: E402
from serve_worker import S3Sink, WorkerPool  # noqa: E402

TRUE, FALSE = ("1", "true", "yes", "on"), ("0", "false", "no", "off", "")


def die(msg):
    print(f"\033[1;31m[transcribe]\033[0m {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


@dataclasses.dataclass
class Config:
    data: Path
    inbox: Path | None = None  # None: <data>/inbox
    jobs: Path | None = None  # None: <data>/jobs
    host: str = "127.0.0.1"  # this machine only; the image sets 0.0.0.0, compose publishes on 127.0.0.1
    port: int = 8080
    workers: int = 1
    poll_s: float = 5.0
    stable_s: float = 10.0
    timeout_s: int = 0
    max_attempts: int = 2
    keep_work: bool = False
    keep_source: bool = True
    keep_days: int = 0
    max_upload_mb: float = 8192
    token: str | None = None
    webhook_url: str | None = None
    s3_bucket: str | None = None
    s3_prefix: str = ""
    s3_endpoint: str | None = None
    s3_region: str | None = None
    transcribe: Path = HERE.parent / "transcribe"

    def __post_init__(self):
        self.inbox = self.inbox or self.data / "inbox"
        self.jobs = self.jobs or self.data / "jobs"


def _num(env, name, default, kind):
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return kind(raw)
    except ValueError:
        die(f"{name}={raw!r}: expected a number")


def _bool(env, name, default):
    raw = env.get(name)
    if raw is None:
        return default
    if raw.strip().lower() in TRUE:
        return True
    if raw.strip().lower() in FALSE:
        return False
    die(f"{name}={raw!r}: expected 1 or 0")


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="transcribe serve", description=__doc__.split("\n\n")[0])
    p.add_argument("--data", type=Path, help="data folder with inbox/ and jobs/ (MT_SERVE_DATA, default ./data)")
    p.add_argument("--host", help="address to listen on (MT_SERVE_HOST, default 127.0.0.1)")
    p.add_argument("--port", type=int, help="port (MT_SERVE_PORT, default 8080)")
    p.add_argument("--workers", type=int, help="recordings processed at once (MT_SERVE_WORKERS, default 1)")
    return p.parse_args(argv)


def config_from(args, env) -> Config:
    s = lambda name: env.get(name, "").strip() or None  # noqa: E731
    path = lambda name: Path(s(name)).expanduser().absolute() if s(name) else None  # noqa: E731
    cfg = Config(
        data=Path(args.data or s("MT_SERVE_DATA") or "data").expanduser().absolute(),
        inbox=path("MT_SERVE_INBOX"),
        jobs=path("MT_SERVE_JOBS"),
        host=args.host or s("MT_SERVE_HOST") or "127.0.0.1",
        port=args.port if args.port is not None else _num(env, "MT_SERVE_PORT", 8080, int),
        workers=args.workers if args.workers is not None else _num(env, "MT_SERVE_WORKERS", 1, int),
        poll_s=_num(env, "MT_SERVE_POLL_S", 5.0, float),
        stable_s=_num(env, "MT_SERVE_STABLE_S", 10.0, float),
        timeout_s=_num(env, "MT_SERVE_JOB_TIMEOUT_S", 0, int),
        max_attempts=_num(env, "MT_SERVE_MAX_ATTEMPTS", 2, int),
        keep_work=_bool(env, "MT_SERVE_KEEP_WORK", False),
        keep_source=_bool(env, "MT_SERVE_KEEP_SOURCE", True),
        keep_days=_num(env, "MT_SERVE_KEEP_DAYS", 0, int),
        max_upload_mb=_num(env, "MT_SERVE_MAX_UPLOAD_MB", 8192, float),
        token=s("MT_SERVE_TOKEN"),
        webhook_url=s("MT_SERVE_WEBHOOK_URL"),
        s3_bucket=s("MT_SERVE_S3_BUCKET"),
        s3_prefix=env.get("MT_SERVE_S3_PREFIX", "").strip(),
        s3_endpoint=s("MT_SERVE_S3_ENDPOINT"),
        s3_region=s("MT_SERVE_S3_REGION"),
        transcribe=Path(s("MT_SERVE_TRANSCRIBE") or HERE.parent / "transcribe").absolute(),
    )
    if cfg.workers < 1:
        die(f"workers: at least 1, got {cfg.workers}")
    if not 1 <= cfg.port <= 65535:
        die(f"port: 1–65535, got {cfg.port}")
    if cfg.poll_s <= 0 or cfg.stable_s < 0:
        die("MT_SERVE_POLL_S must be above 0 and MT_SERVE_STABLE_S not below 0")
    return cfg


class Service:
    def __init__(self, cfg: Config, env: dict | None = None, s3_client=None, tick: float = 1.0):
        self.cfg = cfg
        self.stop = threading.Event()
        self.store = JobStore(cfg.jobs, max_attempts=cfg.max_attempts)
        s3 = None
        if cfg.s3_bucket:
            s3 = S3Sink(cfg.s3_bucket, cfg.s3_prefix, cfg.s3_endpoint, cfg.s3_region, client=s3_client)
        self.pool = WorkerPool(self.store, cfg.transcribe, workers=cfg.workers, timeout_s=cfg.timeout_s,
                               keep_work=cfg.keep_work, keep_source=cfg.keep_source, webhook_url=cfg.webhook_url,
                               s3=s3, stop=self.stop, env=env, tick=tick)
        self.scanner = Scanner(cfg.inbox, self.store, self.pool.submit, cfg.poll_s, cfg.stable_s, self.stop,
                               notify=self.pool.notify)
        self.ui_html = (HERE / "serve_ui.html").read_text(encoding="utf-8")

    def prepare(self) -> list[str]:
        """Folders, the write check and the jobs a previous run left; returns the ids to queue."""
        for d in (self.cfg.inbox, self.cfg.jobs / INCOMING):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                die(f"cannot create {d}: {e} — create the data folder as the user the service runs as")
        for d in (self.cfg.inbox, self.cfg.jobs):
            if not os.access(d, os.W_OK):
                die(f"{d} is not writable for uid {os.getuid()} — in a container create the folder on the host before "
                    "the first start (mkdir -p …), so docker does not create it owned by root")
        try:
            self.store.acquire()  # before recover(): it touches jobs a second instance would be running
        except RuntimeError as e:
            die(f"{e} — stop it first; two services on one folder would run every job twice")
        return self.store.recover()

    def start(self) -> None:
        for job_id in self.prepare():
            self.pool.submit(job_id)
        self.pool.start()
        self.scanner.start()
        if self.cfg.keep_days > 0:
            threading.Thread(target=self._expire, name="retention", daemon=True).start()

    def _expire(self):
        while not self.stop.is_set():
            try:
                for job_id in self.store.expire(self.cfg.keep_days):
                    log(f"job {job_id}: older than {self.cfg.keep_days} days, deleted")
            except Exception as e:
                log(f"retention failed: {e}")
            self.stop.wait(600)

    def shutdown(self) -> None:
        self.stop.set()
        self.scanner.join(5)
        self.pool.shutdown()
        self.store.release()


def main(argv=None):
    cfg = config_from(parse_args(argv), os.environ)
    if not cfg.transcribe.is_file():
        die(f"no pipeline at {cfg.transcribe} (MT_SERVE_TRANSCRIBE)")
    try:
        import uvicorn
        from serve_api import build_app
    except ImportError as e:
        die(f"the service dependencies are missing ({e.name}): run it as ./transcribe serve, which installs them")
    svc = Service(cfg)
    svc.start()
    extras = [f"webhook {cfg.webhook_url}"] * bool(cfg.webhook_url) + [f"S3 {cfg.s3_bucket}"] * bool(cfg.s3_bucket)
    log(f"watching {cfg.inbox}, results in {cfg.jobs}, {cfg.workers} worker(s); UI and API at http://{cfg.host}:{cfg.port}/"
        + (" (token required)" if cfg.token else " (no token: keep it on a trusted network)")
        + (f"; {', '.join(extras)}" if extras else ""))
    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame):
            # while it runs, uvicorn's own handler gets the signal: the jobs start stopping at once, in parallel
            # with the HTTP drain, so both fit into the container's stop timeout (compose: 30 s)
            svc.stop.set()
            super().handle_exit(sig, frame)

    # an upload in progress is cut off after 10 s instead of holding the stop until docker kills everything
    server = Server(uvicorn.Config(build_app(svc), host=cfg.host, port=cfg.port, log_level="warning",
                                   access_log=False, timeout_graceful_shutdown=10))

    def stop(sig, frame):
        svc.stop.set()
        server.should_exit = True

    # uvicorn restores the handlers it found and raises the signal again once it has stopped: with the default
    # handler that kills the process before the jobs are stopped and requeued, so the handlers it finds are these
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    try:
        server.run()  # returns on SIGTERM / SIGINT
    finally:
        log("stopping: running jobs go back to the queue")
        svc.shutdown()


if __name__ == "__main__":
    main()

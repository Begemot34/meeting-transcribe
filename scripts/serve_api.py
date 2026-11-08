"""HTTP API and web UI of the service. The upload is the raw request body (curl -T, xhr.send(file)), streamed to the
data folder (POST or PUT): no multipart, no spooling a multi-GB recording through /tmp."""
import hmac
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from starlette.concurrency import run_in_threadpool
from serve_jobs import BadOptions, allowed, parse_options
from serve_worker import clean

OPTION_KEYS = ("title", "date", "language", "summary", "names")
CHUNK = 1 << 20


def build_app(svc) -> FastAPI:
    """svc: serve.Service — .store, .pool, .cfg (token, max_upload_mb, data, workers), .ui_html."""
    store, pool, cfg = svc.store, svc.pool, svc.cfg
    # the interactive API description only without a token: FastAPI serves it outside the router that checks it
    docs = {} if cfg.token else {"docs_url": "/api/docs", "openapi_url": "/api/openapi.json"}
    app = FastAPI(title="meeting-transcribe", redoc_url=None, **({"docs_url": None, "openapi_url": None} | docs))

    def same_site(request: Request):
        """A page on another site may send simple POSTs here (a form, fetch without CORS) — without a token they
        would upload, retry and cancel. Browsers say where a request comes from; curl and scripts say nothing."""
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return
        site = request.headers.get("sec-fetch-site")
        if site is not None:
            if site not in ("same-origin", "none"):
                raise HTTPException(403, "cross-site request refused")
            return
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "cross-site request refused")

    def auth(request: Request):
        same_site(request)
        if not cfg.token:
            return
        header = request.headers.get("authorization", "")
        given = header[7:] if header.lower().startswith("bearer ") else request.query_params.get("token", "")
        if not hmac.compare_digest(given.encode(), cfg.token.encode()):
            raise HTTPException(401, "token required: Authorization: Bearer <MT_SERVE_TOKEN>")

    def job_or_404(job_id: str):
        try:
            return store.load(job_id)
        except KeyError:
            raise HTTPException(404, f"no job {job_id}") from None

    @app.get("/healthz")
    def healthz():
        jobs = store.all()
        return {"ok": True, "queued": sum(j.state == "queued" for j in jobs),
                "running": sum(j.state == "running" for j in jobs), "workers": cfg.workers,
                "inbox": str(cfg.inbox)}

    @app.get("/", response_class=HTMLResponse)
    def ui():
        return HTMLResponse(svc.ui_html)

    api = APIRouter(prefix="/api", dependencies=[Depends(auth)])

    @api.get("/jobs")
    def list_jobs(limit: int = 200):
        return {"jobs": [j.to_dict() for j in store.all(limit=max(1, min(limit, 1000)))]}

    # PUT too: `curl -T file` uploads with PUT
    @api.post("/jobs", status_code=201, operation_id="upload")
    @api.put("/jobs", status_code=201, operation_id="upload_put", include_in_schema=False)
    async def upload(request: Request):
        q = request.query_params
        name = (q.get("name") or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not name or not allowed(name):
            raise HTTPException(400, "name=<file name> with a recording extension (mp4, mkv, mov, m4a, wav, …)")
        try:
            options = parse_options({k: q[k] for k in OPTION_KEYS if k in q})
        except BadOptions as e:
            raise HTTPException(400, str(e)) from None
        limit = int(cfg.max_upload_mb * (1 << 20))
        too_large = f"the recording is larger than {cfg.max_upload_mb:g} MB (MT_SERVE_MAX_UPLOAD_MB)"
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, too_large)  # before a byte is read
        store.incoming.mkdir(parents=True, exist_ok=True)
        part = store.incoming / f"{uuid.uuid4().hex}.part"
        size = 0
        try:
            with open(part, "wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, too_large)
                    await run_in_threadpool(f.write, chunk)  # slow storage must not stall other requests
            if size == 0:
                raise HTTPException(400, "empty body: send the recording as the request body (curl -T file)")
            job = await run_in_threadpool(store.create, part, name, options, "upload")
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        pool.submit(job.id)
        return job.to_dict()

    @api.get("/jobs/{job_id}")
    def get_job(job_id: str):
        return job_or_404(job_id).to_dict()

    @api.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
    def get_log(job_id: str, tail: int = 200):
        job = job_or_404(job_id)
        p = store.job_dir(job.id) / "log.txt"
        if not p.is_file():
            return PlainTextResponse("")
        lines = [clean(x) for x in p.read_text(encoding="utf-8", errors="replace").splitlines()]
        return PlainTextResponse("\n".join(lines[-max(1, tail):]) + "\n")

    @api.get("/jobs/{job_id}/files/{name}")
    def get_file(job_id: str, name: str):
        job = job_or_404(job_id)
        try:
            path = store.artifact_path(job, name)
        except KeyError:
            raise HTTPException(404, f"no file {name} in job {job_id}") from None
        if name.endswith(".html"):
            # the page is opened from the origin of the API: sandboxed, its scripts get an origin of their own and
            # cannot read the token of the UI or send it requests; the page itself only needs its own script
            return FileResponse(path, media_type="text/html; charset=utf-8", headers={
                "Content-Security-Policy": "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox",
                "X-Content-Type-Options": "nosniff"})
        types = {".pdf": "application/pdf", ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8"}
        download = f"{job.out_dir}.md" if name == "transcript.md" else (
            f"{job.id}.log.txt" if name == "log.txt" else name)
        return FileResponse(path, media_type=types.get(path.suffix, "application/octet-stream"), filename=download)

    @api.post("/jobs/{job_id}/retry")
    def retry(job_id: str):
        job_or_404(job_id)
        try:
            job = store.retry(job_id)
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        pool.submit(job.id)
        return job.to_dict()

    @api.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        job_or_404(job_id)
        try:
            return pool.cancel(job_id).to_dict()
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    @api.delete("/jobs/{job_id}", status_code=204)
    def delete(job_id: str):
        with store.lock:
            job = job_or_404(job_id)
            if job.state == "running" or pool.is_running(job_id):
                raise HTTPException(409, f"job {job_id} is running: cancel it first")
            store.delete(job_id)
        return Response(status_code=204)

    app.include_router(api)
    return app

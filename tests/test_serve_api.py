"""The service's HTTP API through FastAPI's TestClient, with the fake ./transcribe behind the workers."""
import pytest

pytest.importorskip("fastapi")
from conftest import FAKE_TRANSCRIBE, wait_for  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from serve import Config, Service  # noqa: E402
from serve_api import build_app  # noqa: E402


def service(tmp_path, env=None, **cfg):
    svc = Service(Config(data=tmp_path / "data", transcribe=FAKE_TRANSCRIBE, poll_s=0.05, stable_s=0.05, **cfg),
                  env=env, tick=0.05)
    svc.pool.grace_s = 2
    svc.start()
    return svc


@pytest.fixture
def make(tmp_path):
    made = []

    def make_(env=None, **cfg):
        svc = service(tmp_path, env, **cfg)
        made.append(svc)
        return svc, TestClient(build_app(svc))

    yield make_
    for svc in made:
        svc.shutdown()


def upload(client, name="m.mp4", data=b"video", headers=None, **q):
    return client.post("/api/jobs", params={"name": name, **q}, content=data,
                       headers={"Content-Type": "application/octet-stream", **(headers or {})})


def done(client, job_id, state="done"):
    return wait_for(lambda: (j := client.get(f"/api/jobs/{job_id}").json())["state"] == state and j)


def test_healthz_and_ui(make):
    _, c = make()
    h = c.get("/healthz").json()
    assert h["ok"] and h["workers"] == 1 and h["inbox"].endswith("inbox")
    r = c.get("/")
    assert r.status_code == 200 and "<title>meeting-transcribe</title>" in r.text


def test_token(make):
    _, c = make(token="s3cret")
    assert c.get("/healthz").status_code == 200
    assert c.get("/api/jobs").status_code == 401
    assert c.get("/api/jobs", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/jobs", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/api/jobs", params={"token": "s3cret"}).status_code == 200


def test_docs_only_without_a_token(make):
    svc, c = make()
    assert c.get("/api/openapi.json").status_code == 200
    svc.shutdown()  # one service per folder
    _, c = make(token="t")
    assert c.get("/api/openapi.json").status_code == 404 and c.get("/api/docs").status_code == 404


def test_cross_site_writes_refused(make):
    svc, c = make()
    for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "http://evil.example"}):
        r = upload(c, headers=headers)
        assert r.status_code == 403, headers
    assert svc.store.all() == []
    assert upload(c, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 201
    assert upload(c, headers={"Origin": "http://testserver"}).status_code == 201


def test_upload_runs_to_done(make):
    svc, c = make()
    r = upload(c, "Планёрка.mp4", b"x" * 1000, title="T", summary="none", names="false")
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["source"] == {"name": "Планёрка.mp4", "size": 1000, "origin": "upload"}
    assert job["options"] == {"title": "T", "date": None, "language": None, "summary": "none", "names": False}
    job = done(c, job["id"])
    assert job["artifacts"] == ["transcript.md", "планёрка.html", "планёрка.pdf"]
    assert c.get("/api/jobs").json()["jobs"][0]["id"] == job["id"]
    md = c.get(f"/api/jobs/{job['id']}/files/transcript.md")
    assert md.status_code == 200 and md.text.startswith("# T") and "attachment" in md.headers["content-disposition"]
    html = c.get(f"/api/jobs/{job['id']}/files/планёрка.html")
    assert html.status_code == 200 and html.headers["content-type"].startswith("text/html")
    assert "attempt 1" in c.get(f"/api/jobs/{job['id']}/log").text
    assert list(svc.store.incoming.iterdir()) == []


@pytest.mark.parametrize(("params", "body"), [
    ({"name": "m.exe"}, b"x"), ({}, b"x"), ({"name": "m.mp4", "summary": "gpt"}, b"x"),
    ({"name": "m.mp4", "date": "tomorrow"}, b"x"), ({"name": "m.mp4"}, b""),
])
def test_upload_rejects(make, params, body):
    svc, c = make()
    r = c.post("/api/jobs", params=params, content=body)
    assert r.status_code == 400, r.text
    assert svc.store.all() == [] and list(svc.store.incoming.iterdir()) == []


def test_upload_too_large(make):
    svc, c = make(max_upload_mb=0.001)
    assert upload(c, data=b"x" * 5000).status_code == 413
    assert svc.store.all() == [] and list(svc.store.incoming.iterdir()) == []


def test_upload_with_put_as_curl_t(make):
    _, c = make()
    r = c.put("/api/jobs", params={"name": "m.mp4"}, content=b"video")
    assert r.status_code == 201 and r.json()["source"]["size"] == 5


def test_upload_name_is_a_basename(make):
    _, c = make()
    job = upload(c, "../../etc/m.mp4").json()
    assert job["source"]["name"] == "m.mp4"


def test_files_are_only_the_artifacts(make):
    _, c = make()
    job = done(c, upload(c).json()["id"])
    base = f"/api/jobs/{job['id']}/files/"
    assert c.get(base + "log.txt").status_code == 200
    for bad in ("job.json", "source.mp4", "..%2Fjob.json", "m.title"):
        assert c.get(base + bad).status_code == 404, bad


def test_unknown_job(make):
    _, c = make()
    for method, path in (("get", "/api/jobs/nope"), ("get", "/api/jobs/nope/log"), ("post", "/api/jobs/nope/retry"),
                         ("post", "/api/jobs/nope/cancel"), ("delete", "/api/jobs/nope"),
                         ("get", "/api/jobs/..%2F..%2Fetc/log")):
        assert getattr(c, method)(path).status_code == 404, path


def test_retry_cancel_delete(make):
    svc, c = make(env={"FAKE_SLEEP": "30"})
    job_id = upload(c).json()["id"]
    wait_for(lambda: c.get(f"/api/jobs/{job_id}").json()["state"] == "running")
    assert c.post(f"/api/jobs/{job_id}/retry").status_code == 409
    assert c.delete(f"/api/jobs/{job_id}").status_code == 409
    assert c.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    job = done(c, job_id, "failed")
    assert job["error"] == "cancelled"
    assert c.post(f"/api/jobs/{job_id}/cancel").status_code == 409
    svc.pool.env = {}
    assert c.post(f"/api/jobs/{job_id}/retry").json()["state"] == "queued"
    done(c, job_id)
    assert c.delete(f"/api/jobs/{job_id}").status_code == 204
    assert c.get(f"/api/jobs/{job_id}").status_code == 404
    assert not (svc.store.root / job_id).exists()


def test_second_service_on_one_folder_refuses(make, tmp_path):
    make()
    with pytest.raises(SystemExit):
        service(tmp_path)


def test_inbox_and_jobs_in_other_folders(tmp_path):
    from serve import config_from, parse_args
    env = {"MT_SERVE_DATA": str(tmp_path / "data"), "MT_SERVE_INBOX": str(tmp_path / "share" / "in"),
           "MT_SERVE_JOBS": str(tmp_path / "results")}
    cfg = config_from(parse_args([]), env)
    assert (cfg.inbox, cfg.jobs) == (tmp_path / "share" / "in", tmp_path / "results")
    assert config_from(parse_args([]), {"MT_SERVE_DATA": str(tmp_path / "d")}).inbox == tmp_path / "d" / "inbox"
    cfg.transcribe, cfg.poll_s, cfg.stable_s = FAKE_TRANSCRIBE, 0.05, 0.05
    svc = Service(cfg, tick=0.05)
    svc.start()
    try:
        c = TestClient(build_app(svc))
        assert c.get("/healthz").json()["inbox"] == str(tmp_path / "share" / "in")
        (tmp_path / "share" / "in" / "a.mp4").write_bytes(b"video")
        job = wait_for(lambda: c.get("/api/jobs").json()["jobs"])[0]
        assert done(c, job["id"])["state"] == "done"
        assert (tmp_path / "results" / job["id"] / "a" / "transcript.md").is_file()
        assert not (tmp_path / "data" / "jobs").exists()
    finally:
        svc.shutdown()


def test_inbox_to_api(make, tmp_path):
    svc, c = make()
    (tmp_path / "data" / "inbox" / "drop.mkv").write_bytes(b"video")
    job = wait_for(lambda: c.get("/api/jobs").json()["jobs"])[0]
    assert job["source"]["origin"] == "inbox"
    assert done(c, job["id"])["artifacts"] == ["transcript.md", "drop.html", "drop.pdf"]

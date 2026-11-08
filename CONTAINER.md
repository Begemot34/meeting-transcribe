**English** | [Русский](CONTAINER.ru.md)

# Container

The same commands as `./transcribe`, run by podman: the folder with the recording is mounted at its own path, so
every path is the same as on the host. Models and settings live in the `mt-cache` volume.

## Requirements

- Linux: `transcribe-container` refuses to run elsewhere; on macOS run `./transcribe` directly.
- podman 6+, rootless is enough.
- Disk: about 3.5 GB for the CPU image, about 9 GB for the CUDA one.
- NVIDIA GPU: nvidia-container-toolkit with CDI configured; the wrapper runs
  `podman run --device nvidia.com/gpu=all`.

## Build

```bash
podman build --build-arg DEVICE=cpu --build-arg UID=$(id -u) --build-arg GID=$(id -g) -t localhost/meeting-transcribe:latest .
podman build --build-arg DEVICE=cuda --build-arg UID=$(id -u) --build-arg GID=$(id -g) -t localhost/meeting-transcribe:cuda .
```

You rarely need this: the wrapper builds the image itself when it is missing and passes your UID/GID as build
args — one of the reasons its builds work on hosts where `id -u` is not 1000.

## First run (models)

```bash
transcribe-container setup --language ru,en --summary none --yes
```

The dependencies are already in the image: `setup` downloads the models (whisper, word alignment, EasyOCR, nltk)
once into the `mt-cache` volume and writes its settings to `/cache/config.env`, also in the volume. A repeated
`setup` skips what is already there.

## Usage

Call the wrapper from any folder — install a symlink once and drop the `./` prefix:

```bash
ln -s /path/to/repo/transcribe-container ~/.local/bin/transcribe-container
transcribe-container auto meeting.mp4 out/board-1105
```

Run it from the folder that holds the recording. Commands and options are the same as `./transcribe` — the whole
list is in [COMMANDS.md](COMMANDS.md). Flags go before the command: `--cuda`, `--image NAME`, `--build`, `--pull`.
Positional paths must stay inside the current directory: the container mounts only it, and a result written
elsewhere would be lost with `--rm` — the wrapper refuses such paths up front.

## Summary backends

- `--summary api`: set `MT_LLM_BASE_URL`, `MT_LLM_MODEL` and `MT_LLM_API_KEY` in the environment; the key is
  passed by name into the container and never baked into the image.
- `--summary none`: no summary.
- `claude`, `codex` and `opencode` are not available in the container — run
  `./transcribe summarize <out_dir> --llm …` on the host instead.
- `--summary local`: the Ollama model runs on the host — the container has no ollama binary, so
  `setup --summary local` does not work there; download the model on the host instead: `ollama pull gemma4:26b`.
  Ollama listens on 127.0.0.1 by default, which the container cannot reach, so start it with
  `OLLAMA_HOST=0.0.0.0 ollama serve`; then:

  ```bash
  OLLAMA_HOST=http://host.containers.internal:11434 transcribe-container auto meeting.mp4 out/board-1105 --summary local
  ```

## NVIDIA GPU

```bash
transcribe-container --cuda setup --language ru,en --summary none --yes
MT_DEVICE=cuda transcribe-container --cuda auto meeting.mp4 out/board-1105
```

`--cuda` takes `localhost/meeting-transcribe:cuda` and passes the host GPU. It sets `MT_DEVICE=cuda` unless you
set it yourself, and refuses to run with `MT_DEVICE=cuda` on a non-cuda image.

## Compose

```bash
docker compose build
docker compose run --rm transcribe auto /work/meeting.mp4 /work/out
# podman: the podman-only settings are in an override file
podman-compose -f compose.yaml -f compose.podman.yaml build
podman-compose -f compose.yaml -f compose.podman.yaml run --rm transcribe auto /work/meeting.mp4 /work/out
```

The repo directory is mounted at `/work`. The `mt-cache` volume has a fixed name, so the models and settings are
shared with `transcribe-container`. `compose.cuda.yaml` on top switches both services to the cuda image and the
host GPU. Compose works on macOS too (Docker Desktop, CPU only); on a Mac `./transcribe serve` without a container
is faster, since it uses the Apple GPU.

## Service

`serve` turns the image into a service that people use without a terminal: they drop a recording into a folder
or upload it in the browser, and pick up the HTML, PDF or Markdown when it is ready.

```bash
mkdir -p data/inbox        # before the first start: otherwise docker creates ./data as root
docker compose run --rm transcribe setup --language ru,en --summary none --yes   # models, once
docker compose up -d serve
docker compose logs -f serve
```

The web UI and the API are at `http://127.0.0.1:8080/`. Compose publishes the port only on this machine; to open
it to the network change `ports` in `compose.yaml` to `"8080:8080"` and set `MT_SERVE_TOKEN`. Without compose:
`transcribe-container serve` (Linux, stops with Ctrl+C), or `./transcribe serve` on the host.

**How a recording travels.** `data/` holds two folders:

```text
data/inbox/                          drop recordings here (mp4, mkv, mov, webm, m4a, mp3, wav, …)
data/jobs/20261108-141500-board/     one folder per recording
  job.json                           state: queued, running, done, failed; stage, percent, errors
  log.txt                            the full output of `auto`
  source.mp4                         the recording, moved out of the inbox
  board/                             the results: transcript.md, board.html, board.pdf, screenshots/
```

The inbox is polled every `MT_SERVE_POLL_S` seconds; a file is taken once its size and time have not changed for
`MT_SERVE_STABLE_S` seconds, so a copy over the network is not taken half-way. Temporary names (`.part`, `.tmp`,
`.crdownload`, names starting with `.` or `~`) are ignored. Polling instead of file events: events do not cross
bind mounts, SMB or NFS reliably. The inbox can be a network share mounted into the container.

Options for one recording go into a file next to it, named after it plus `.json`, written before the recording
itself:

```json
{"title": "Board meeting", "date": "2026-11-05", "language": "ru", "summary": "none", "names": true}
```

Every key is optional; `summary` is one of `local`, `api`, `none` (and `claude`, `codex`, `opencode` on the host).
A bad file fails the job with the reason, it does not stay in the inbox.

**Queue and workers.** Jobs run in the order they came, `MT_SERVE_WORKERS` at a time (default 1). One run of
`large-v3` with diarization takes 6–8 GB of RAM or most of a 12 GB GPU, so a second worker pays off only with
memory for two. A stop of the service (`docker compose stop`, a restart, a deploy) stops the running jobs and
queues them again: the next start continues them, reusing the extracted audio. A job cut off by a crash runs
again up to `MT_SERVE_MAX_ATTEMPTS` times. `MT_SERVE_JOB_TIMEOUT_S` stops a job that hangs; for long recordings
on the CPU give it hours (e.g. `21600`).

**Picking up the results.**

- in the browser: the jobs table with progress, links to HTML, PDF and Markdown, the log, and retry, cancel,
  delete;
- in the folder: `data/jobs/<id>/<name>/`, e.g. shared over SMB or synced;
- by webhook: `MT_SERVE_WEBHOOK_URL` gets a POST with `{"event": "job.done", "job": {…}}` when a job ends
  (n8n, a chat bot, your own script);
- in S3 or MinIO: with `MT_SERVE_S3_BUCKET` the results are copied to `<prefix><job id>/` in the bucket; keys come
  from `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, MinIO needs `MT_SERVE_S3_ENDPOINT`;
- through the API, below.

**API.** With `MT_SERVE_TOKEN` set, every `/api/*` call needs `Authorization: Bearer <token>` (or `?token=` in a
link). The interactive description is at `/api/docs`.

| Call | What it does |
| --- | --- |
| `POST` or `PUT /api/jobs?name=<file>&title=&date=&language=&summary=&names=` | upload a recording as the request body; returns the job |
| `GET /api/jobs` | all jobs, newest first |
| `GET /api/jobs/<id>` | one job: `state`, `stage`, `progress`, `error`, `warnings`, `artifacts`, `s3` |
| `GET /api/jobs/<id>/files/<name>` | a result from `artifacts`, or `log.txt` |
| `GET /api/jobs/<id>/log?tail=200` | the end of the log |
| `POST /api/jobs/<id>/retry` | run a finished or failed job again |
| `POST /api/jobs/<id>/cancel` | stop a queued or running job |
| `DELETE /api/jobs/<id>` | delete a finished job with its files |
| `GET /healthz` | liveness and queue size, no token needed |

```bash
curl -T meeting.mp4 -H "Authorization: Bearer $MT_SERVE_TOKEN" \
  "http://127.0.0.1:8080/api/jobs?name=meeting.mp4&title=Board&summary=none"
curl -H "Authorization: Bearer $MT_SERVE_TOKEN" http://127.0.0.1:8080/api/jobs/<id>
curl -OJ -H "Authorization: Bearer $MT_SERVE_TOKEN" http://127.0.0.1:8080/api/jobs/<id>/files/<name>.pdf
```

Behind a reverse proxy raise its body limit for uploads (nginx: `client_max_body_size 8g;`). Finished jobs are
kept until deleted; `MT_SERVE_KEEP_DAYS` deletes old ones, `MT_SERVE_KEEP_SOURCE=0` deletes the recording after
a successful run. Every setting: [settings of the service](SETTINGS.md#service).

The service dependencies (fastapi, uvicorn, boto3) are in the image. On the host `./transcribe serve` installs them
on its first start, and again after a dependency update.

## Where things live

- volume `mt-cache` is `/cache` in the container: the Hugging Face models, EasyOCR, nltk and `config.env`
  (`MT_CONFIG`);
- clear the model cache: `podman volume rm mt-cache`;
- every command: [COMMANDS.md](COMMANDS.md).

## Notes

- `MT_LLM_API_KEY` and `HF_TOKEN` are passed only into `localhost/meeting-transcribe*` images; with any other
  `--image` the wrapper warns, passes neither and mounts a separate `mt-cache-foreign` volume, so a foreign run
  cannot read the trusted `config.env` or write into the trusted model cache.
- On a host whose UID is not 1000: `transcribe-container --build` passes your UID/GID as build args itself; a
  manual build needs `--build-arg UID=$(id -u) --build-arg GID=$(id -g)`.
- `MT_*` and `HF_*` from your shell are passed into the container, except `HF_HOME`, `HF_HUB_CACHE` and
  `MT_EASYOCR_DIR`: the image pins them to `/cache`.
- `--build` and `--pull`: use one, not both.

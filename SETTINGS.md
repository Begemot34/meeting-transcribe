**English** | [Русский](SETTINGS.ru.md)

# Settings

| Variable | Default | What it does |
| --- | --- | --- |
| `MT_DEVICE` | from `setup` (`cpu`) | `cuda`: recognition and diarization on an NVIDIA GPU (Linux); `setup` offers it when it finds one |
| `MT_COMPUTE_TYPE` | `int8` on the CPU, `float16` on a GPU | recognition precision; `int8_float16` is picked for a GPU with less than 8 GB |
| `MT_DIARIZE_DEVICE` | Apple GPU (MPS) if present, else `MT_DEVICE` | where diarization runs; `cpu` forces the CPU |
| `MT_LANGUAGE` | `en` | default meeting language |
| `MT_WHISPER_MODEL` | `mlx-large-v3-turbo` on Apple Silicon, else `large-v3` | `large-v3` — whisperx on the CPU, the most accurate; `large-v3-turbo`, `medium`, `small` are faster and less accurate; a CTranslate2 repo or a folder |
| `MT_ALIGN_MODEL` | per language | your own alignment model: a wav2vec2 repo, a folder or a torchaudio model name |
| `MT_LLM` | from `setup` | summary backend for `auto`: `claude`, `codex`, `opencode`, `local`, `api`; empty — none |
| `MT_LOCAL_MODEL` | `gemma4:26b` | Ollama model for `--llm local` |
| `MT_OPENCODE_MODEL` | from `setup` | `provider/model` for `--llm opencode` |
| `MT_CODEX_EFFORT` | `medium` | Codex reasoning effort for `--llm codex` |
| `MT_LLM_TIMEOUT` | `1800` | seconds to wait for a summary |
| `MT_LLM_TEMPERATURE` | — | temperature for `--llm api` (not sent by default: reasoning models reject it) |
| `MT_LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible API address for `--llm api` |
| `MT_LLM_API_KEY` | `OPENAI_API_KEY` | key for `--llm api` (not needed for a local Ollama) |
| `MT_LLM_MODEL` | — | model for `--llm api` |
| `HF_HOME` | `~/.cache/huggingface` | where models are stored |
| `HF_ENDPOINT` | `https://huggingface.co` | Hugging Face mirror |
| `HTTPS_PROXY` | — | proxy for model downloads |
| `MT_DOWNLOAD_STALL_S` | `120` | seconds without progress before a download restarts |
| `MT_DOWNLOAD_ATTEMPTS` | `5` | download attempts per model |
| `MT_EASYOCR_DIR` | `~/.EasyOCR` | where EasyOCR models for names on screen are stored (Linux) |
| `MT_CONFIG` | `config.env` next to `./transcribe` | the settings file: `setup` writes it, every command reads it; `transcribe-container` sets it to `/cache/config.env` on the `mt-cache` volume |
| `PYANNOTE_METRICS_ENABLED` | `false` | pyannote.audio's usage metrics to pyannote.ai; off, so nothing leaves the machine |

`setup` saves its settings to the file in `MT_CONFIG`.

## Service

What [`serve`](COMMANDS.md#serve) reads ([service in a container](CONTAINER.md#service) shows them in use). Only
the data folder, the address and the number of workers have command-line options too.

| Variable | Default | What it does |
| --- | --- | --- |
| `MT_SERVE_DATA` | `./data` (in the image `/data`) | data folder: `inbox/` for new recordings, `jobs/` for the jobs and their results |
| `MT_SERVE_HOST` | `0.0.0.0` | address the web UI and the API listen on |
| `MT_SERVE_PORT` | `8080` | their port |
| `MT_SERVE_TOKEN` | — | access token for `/api/*` (`Authorization: Bearer …` or `?token=`); without it anyone who reaches the port can use the service |
| `MT_SERVE_WORKERS` | `1` | recordings processed at once; every one needs the memory of a whole run (6–8 GB RAM, a GPU with 12 GB holds one) |
| `MT_SERVE_POLL_S` | `5` | seconds between two looks into the inbox |
| `MT_SERVE_STABLE_S` | `10` | seconds a file must keep its size and time before it is taken: a copy in progress stays where it is |
| `MT_SERVE_JOB_TIMEOUT_S` | `0` | stop a job after this many seconds and mark it failed; `0` — never |
| `MT_SERVE_MAX_ATTEMPTS` | `2` | how many times a job cut off by a crash or a kill of the service runs again before it is marked failed |
| `MT_SERVE_KEEP_WORK` | `0` | `1`: keep the work folder `.work` of a finished job (audio, raw recognition), for reruns of single steps |
| `MT_SERVE_KEEP_SOURCE` | `1` | `0`: delete the recording after a successful run; such a job cannot be retried |
| `MT_SERVE_KEEP_DAYS` | `0` | delete finished jobs older than this many days; `0` — keep everything |
| `MT_SERVE_MAX_UPLOAD_MB` | `8192` | largest upload through the web UI or the API, MB |
| `MT_SERVE_WEBHOOK_URL` | — | when a job ends, POST `{"event": "job.done"\|"job.failed", "job": {…}}` to this address |
| `MT_SERVE_S3_BUCKET` | — | copy the results (`transcript.md`, HTML, PDF) to this S3 bucket; keys come from `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` |
| `MT_SERVE_S3_PREFIX` | — | key prefix: the files land at `<prefix><job id>/<file>` |
| `MT_SERVE_S3_ENDPOINT` | AWS | S3-compatible storage address, e.g. `http://minio:9000` |
| `MT_SERVE_S3_REGION` | — | bucket region |
| `MT_SERVE_TRANSCRIBE` | `transcribe` of this folder | the pipeline a job runs; the tests put a stub here |

## Speed

On an M4 Pro: with `mlx-large-v3-turbo` (the default on Apple Silicon) about 0.1× the recording length, an
hour-long meeting in about 6 minutes; with `large-v3` (whisperx on the CPU) about 0.4×, an hour in about 25 minutes.
Diarization runs on the Apple GPU in both cases. Quality is close: on a Russian and an English meeting the mlx text
differed from whisperx in 12% and 5% of the words, mostly fillers and word forms; a blind judge scored whisperx
7.5/10, mlx turbo 7.0. When accuracy matters more than time, choose `large-v3` in `./transcribe setup`.
On an NVIDIA GPU whisperx large-v3 is many times faster than on the CPU (not measured by us: no such machine).

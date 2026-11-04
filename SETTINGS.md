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
| `PYANNOTE_METRICS_ENABLED` | `false` | pyannote.audio's usage metrics to pyannote.ai; off, so nothing leaves the machine |

`setup` saves its settings to `config.env`.

## Speed

On an M4 Pro: with `mlx-large-v3-turbo` (the default on Apple Silicon) about 0.1× the recording length, an
hour-long meeting in about 6 minutes; with `large-v3` (whisperx on the CPU) about 0.4×, an hour in about 25 minutes.
Diarization runs on the Apple GPU in both cases. Quality is close: on a Russian and an English meeting the mlx text
differed from whisperx in 12% and 5% of the words, mostly fillers and word forms; a blind judge scored whisperx
7.5/10, mlx turbo 7.0. When accuracy matters more than time, choose `large-v3` in `./transcribe setup`.
On an NVIDIA GPU whisperx large-v3 is many times faster than on the CPU (not measured by us: no such machine).

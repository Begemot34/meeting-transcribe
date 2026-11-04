[English](SETTINGS.md) | **Русский**

# Настройки

| Переменная | По умолчанию | Что делает |
| --- | --- | --- |
| `MT_DEVICE` | из `setup` (`cpu`) | `cuda` — распознавание и диаризация на NVIDIA GPU (Linux); `setup` предложит сам, если найдёт карту |
| `MT_COMPUTE_TYPE` | `int8` на CPU, `float16` на GPU | точность распознавания; для GPU меньше 8 GB берётся `int8_float16` |
| `MT_DIARIZE_DEVICE` | GPU Mac (MPS), если есть, иначе `MT_DEVICE` | где считать диаризацию; `cpu` — принудительно на CPU |
| `MT_LANGUAGE` | `en` | язык встреч по умолчанию |
| `MT_WHISPER_MODEL` | `mlx-large-v3-turbo` на Apple Silicon, иначе `large-v3` | `large-v3` — whisperx на CPU, самая точная; `large-v3-turbo`, `medium`, `small` — быстрее, хуже; репозиторий CTranslate2 или папка |
| `MT_ALIGN_MODEL` | по языку | своя модель выравнивания: репозиторий wav2vec2, папка или имя модели torchaudio |
| `MT_LLM` | из `setup` | чем `auto` делает саммари: `claude`, `codex`, `opencode`, `local`, `api`; пусто — без саммари |
| `MT_LOCAL_MODEL` | `gemma4:26b` | модель Ollama для `--llm local` |
| `MT_OPENCODE_MODEL` | из `setup` | `провайдер/модель` для `--llm opencode` |
| `MT_LLM_TIMEOUT` | `1800` | сколько секунд ждать саммари |
| `MT_LLM_TEMPERATURE` | — | temperature для `--llm api` (по умолчанию не передаётся: reasoning-модели её не принимают) |
| `MT_LLM_BASE_URL` | `https://api.openai.com/v1` | адрес OpenAI-совместимого API для `--llm api` |
| `MT_LLM_API_KEY` | `OPENAI_API_KEY` | ключ для `--llm api` (для локальной Ollama не нужен) |
| `MT_LLM_MODEL` | — | модель для `--llm api` |
| `HF_HOME` | `~/.cache/huggingface` | куда класть модели |
| `HF_ENDPOINT` | `https://huggingface.co` | зеркало Hugging Face |
| `HTTPS_PROXY` | — | прокси для скачивания моделей |
| `MT_DOWNLOAD_STALL_S` | `120` | через сколько секунд без прогресса перезапускать загрузку |
| `MT_DOWNLOAD_ATTEMPTS` | `5` | сколько раз пробовать скачать модель |
| `PYANNOTE_METRICS_ENABLED` | `false` | метрики использования pyannote.audio в pyannote.ai; выключены, чтобы ничего не уходило с машины |

Настройки `setup` — в `config.env`.

## Скорость

На M4 Pro: с `mlx-large-v3-turbo` (по умолчанию на Apple Silicon) ≈ 0,1 от длины записи, час встречи —
около 6 минут; с `large-v3` (whisperx на CPU) ≈ 0,4, час — около 25 минут. Диаризация в обоих случаях на GPU Mac.
Качество близкое: на русской и английской встречах текст mlx расходится с whisperx в 12% и 5% слов, в основном
слова-паразиты и формы слов; слепой судья поставил whisperx 7,5/10, mlx turbo 7,0. Если точность важнее
времени — выберите `large-v3` в `./transcribe setup`. На NVIDIA GPU whisperx large-v3 в разы быстрее, чем на
CPU (сами не замеряли: нет такой машины).

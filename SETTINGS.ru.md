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
| `MT_CODEX_EFFORT` | `medium` | глубина рассуждений Codex для `--llm codex` |
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
| `MT_EASYOCR_DIR` | `~/.EasyOCR` | где лежат модели EasyOCR для имён на экране (Linux) |
| `MT_CONFIG` | `config.env` рядом с `./transcribe` | файл настроек: его пишет `setup` и читают все команды; `transcribe-container` ставит `/cache/config.env` на томе `mt-cache` |
| `PYANNOTE_METRICS_ENABLED` | `false` | метрики использования pyannote.audio в pyannote.ai; выключены, чтобы ничего не уходило с машины |

Настройки `setup` — в файле из `MT_CONFIG`.

## Сервис

Что читает [`serve`](COMMANDS.ru.md#serve) (как это применять — в разделе [сервис в контейнере](CONTAINER.ru.md#сервис)).
Опции командной строки есть только у папки данных, адреса и числа воркеров.

| Переменная | По умолчанию | Что делает |
| --- | --- | --- |
| `MT_SERVE_DATA` | `./data` (в образе `/data`) | папка данных: `inbox/` для новых записей, `jobs/` для задач и их результатов |
| `MT_SERVE_INBOX` | `<MT_SERVE_DATA>/inbox` | inbox по своему пути, например сетевая папка |
| `MT_SERVE_JOBS` | `<MT_SERVE_DATA>/jobs` | задачи и их результаты по своему пути |
| `MT_SERVE_HOST` | `127.0.0.1` (в образе `0.0.0.0`) | адрес, на котором слушают веб-интерфейс и API; по умолчанию только эта машина, в образе все интерфейсы, а compose публикует порт на `127.0.0.1` |
| `MT_SERVE_PORT` | `8080` | их порт |
| `MT_SERVE_TOKEN` | — | токен доступа к `/api/*` (`Authorization: Bearer …` или `?token=`); без него сервисом пользуется любой, кто достучится до порта. С токеном интерактивное `/api/docs` выключено |
| `MT_SERVE_WORKERS` | `1` | сколько записей обрабатывать одновременно; каждой нужна память целого запуска (6–8 ГБ RAM, в GPU на 12 ГБ помещается одна) |
| `MT_SERVE_POLL_S` | `5` | секунд между двумя проверками inbox |
| `MT_SERVE_STABLE_S` | `10` | сколько секунд у файла не должны меняться размер и время, прежде чем его взять: недокопированный файл остаётся на месте |
| `MT_SERVE_JOB_TIMEOUT_S` | `0` | остановить задачу через столько секунд и пометить ошибкой; `0` — никогда |
| `MT_SERVE_MAX_ATTEMPTS` | `2` | сколько всего раз запускается задача, если её обрывает перезапуск или падение сервиса; `2` — один повтор, потом задача помечается ошибкой |
| `MT_SERVE_KEEP_WORK` | `0` | `1`: хранить рабочую папку `.work` готовой задачи (звук, сырое распознавание) для перезапуска отдельных шагов |
| `MT_SERVE_KEEP_SOURCE` | `1` | `0`: удалять запись после успешного запуска; такую задачу нельзя повторить |
| `MT_SERVE_KEEP_DAYS` | `0` | удалять завершённые задачи старше стольких дней; `0` — хранить всё |
| `MT_SERVE_MAX_UPLOAD_MB` | `8192` | наибольшая загрузка через веб-интерфейс или API, МБ |
| `MT_SERVE_WEBHOOK_URL` | — | по завершении задачи POST `{"event": "job.done"\|"job.failed", "job": {…}}` на этот адрес; отменённая задача приходит как `job.failed` с ошибкой `cancelled` |
| `MT_SERVE_S3_BUCKET` | — | копировать результаты (`transcript.md`, HTML, PDF) в этот S3-бакет; ключи из `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` |
| `MT_SERVE_S3_PREFIX` | — | префикс ключей: файлы ложатся в `<prefix><id задачи>/<файл>` |
| `MT_SERVE_S3_ENDPOINT` | AWS | адрес S3-совместимого хранилища, например `https://<id аккаунта>.r2.cloudflarestorage.com` для Cloudflare R2 |
| `MT_SERVE_S3_REGION` | — | регион бакета; для Cloudflare R2 — `auto` |
| `MT_SERVE_TRANSCRIBE` | `transcribe` из этой папки | конвейер, который запускает задача; тесты подставляют сюда заглушку |

## Скорость

На M4 Pro: с `mlx-large-v3-turbo` (по умолчанию на Apple Silicon) ≈ 0,1 от длины записи, час встречи —
около 6 минут; с `large-v3` (whisperx на CPU) ≈ 0,4, час — около 25 минут. Диаризация в обоих случаях на GPU Mac.
Качество близкое: на русской и английской встречах текст mlx расходится с whisperx в 12% и 5% слов, в основном
слова-паразиты и формы слов; слепой судья поставил whisperx 7,5/10, mlx turbo 7,0. Если точность важнее
времени — выберите `large-v3` в `./transcribe setup`. На NVIDIA GPU whisperx large-v3 в разы быстрее, чем на
CPU (сами не замеряли: нет такой машины).

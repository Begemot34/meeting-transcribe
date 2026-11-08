[English](CONTAINER.md) | **Русский**

# Контейнер

Те же команды, что и у `./transcribe`, но внутри podman: папка с записью смонтирована по своему пути, поэтому
пути совпадают с хостовыми. Модели и настройки лежат в томе `mt-cache`.

## Требования

- Linux: `transcribe-container` на других системах не запускается; на macOS запускай `./transcribe` напрямую.
- podman 6+, rootless подходит.
- Диск: около 3,5 GB для CPU-образа и около 9 GB для cuda.
- NVIDIA GPU: nvidia-container-toolkit с настроенным CDI; обёртка запускает
  `podman run --device nvidia.com/gpu=all`.

## Сборка

```bash
podman build --build-arg DEVICE=cpu --build-arg UID=$(id -u) --build-arg GID=$(id -g) -t localhost/meeting-transcribe:latest .
podman build --build-arg DEVICE=cuda --build-arg UID=$(id -u) --build-arg GID=$(id -g) -t localhost/meeting-transcribe:cuda .
```

Редко когда нужно: обёртка сама соберёт образ, если его нет, и передаст твои UID/GID в build-args — поэтому её
сборки работают и там, где `id -u` не 1000.

## Первый запуск (модели)

```bash
transcribe-container setup --language ru,en --summary none --yes
```

Зависимости уже в образе: `setup` один раз скачивает модели (whisper, выравнивание, EasyOCR, nltk) в том
`mt-cache` и пишет настройки в `/cache/config.env` — тоже в том. Повторный `setup` уже скачанное не трогает.

## Запуск

Обёртку можно звать из любой папки — поставь симлинк один раз и не пиши `./`:

```bash
ln -s /path/to/repo/transcribe-container ~/.local/bin/transcribe-container
transcribe-container auto meeting.mp4 out/board-1105
```

Запускай из папки с записью. Команды и опции те же, что у `./transcribe` — весь список в
[COMMANDS.ru.md](COMMANDS.ru.md). Флаги идут перед командой: `--cuda`, `--image ИМЯ`, `--build`, `--pull`.
Позиционные пути должны оставаться внутри текущего каталога: контейнер монтирует только его, и результат,
записанный мимо, пропадёт вместе с `--rm` — обёртка заранее отвергает такие пути.

## Кто пишет саммари

- `--summary api`: задай `MT_LLM_BASE_URL`, `MT_LLM_MODEL` и `MT_LLM_API_KEY` в окружении; ключ передаётся в
  контейнер по имени и не запекается в образ.
- `--summary none`: без саммари.
- `claude`, `codex` и `opencode` в контейнере не работают — запускай
  `./transcribe summarize <out_dir> --llm …` на хосте.
- `--summary local`: модель Ollama живёт на хосте — в контейнере нет бинарника ollama, поэтому
  `setup --summary local` там не работает; скачай модель на хосте: `ollama pull gemma4:26b`. По умолчанию
  Ollama слушает 127.0.0.1, контейнеру туда не добраться: запусти `OLLAMA_HOST=0.0.0.0 ollama serve`; дальше:

  ```bash
  OLLAMA_HOST=http://host.containers.internal:11434 transcribe-container auto meeting.mp4 out/board-1105 --summary local
  ```

## NVIDIA GPU

```bash
transcribe-container --cuda setup --language ru,en --summary none --yes
MT_DEVICE=cuda transcribe-container --cuda auto meeting.mp4 out/board-1105
```

`--cuda` берёт образ `localhost/meeting-transcribe:cuda` и прокидывает GPU хоста. Он ставит `MT_DEVICE=cuda`,
если ты не задал его сам, и отказывается работать с `MT_DEVICE=cuda` на не-cuda-образе.

## Compose

```bash
docker compose build
docker compose run --rm transcribe auto /work/meeting.mp4 /work/out
# podman: настройки только для podman лежат в отдельном override-файле
podman-compose -f compose.yaml -f compose.podman.yaml build
podman-compose -f compose.yaml -f compose.podman.yaml run --rm transcribe auto /work/meeting.mp4 /work/out
```

Каталог репозитория смонтирован в `/work`. У тома `mt-cache` фиксированное имя, поэтому модели и настройки
общие с `transcribe-container`. `compose.cuda.yaml` поверх переключает оба сервиса на cuda-образ и GPU хоста.
Compose работает и на macOS (Docker Desktop, только CPU); на Mac `./transcribe serve` без контейнера быстрее,
потому что использует GPU Apple.

## Сервис

`serve` превращает образ в сервис, которым пользуются без терминала: запись кладут в папку или загружают в
браузере, а когда она готова, забирают HTML, PDF или Markdown.

```bash
mkdir -p data/inbox   # до первого запуска: иначе docker создаст ./data от root
docker compose run --rm transcribe setup --language ru,en --summary none --yes   # модели, один раз
docker compose up -d serve
docker compose logs -f serve
```

Веб-интерфейс и API — на `http://127.0.0.1:8080/`. Compose публикует порт только для этой машины; чтобы открыть
его в сеть, замените `ports` в `compose.yaml` на `"8080:8080"` и задайте `MT_SERVE_TOKEN`. Без compose:
`transcribe-container serve` (Linux, останавливается Ctrl+C) или `./transcribe serve` прямо на машине.

**Папки и настройки.** Compose читает `.env` рядом с `compose.yaml` (он в `.gitignore`): туда кладут настройки
`MT_SERVE_*`, а папку на хосте с `inbox/` и `jobs/` задаёт `MT_DATA_DIR`. Inbox в отдельной папке, например на
сетевой шаре, подключается через `compose.inbox.yaml`:

```bash
MT_DATA_DIR=/srv/meetings
MT_SERVE_WORKERS=1
MT_SERVE_TOKEN=change-me
# inbox на шаре:
COMPOSE_FILE=compose.yaml:compose.inbox.yaml
MT_INBOX_DIR=/mnt/share/meetings-in
```

Запись из отдельного inbox копируется в папку задачи, а не переносится, и затем удаляется из inbox:
переименование не проходит между точками монтирования. Без compose папки задают `MT_SERVE_INBOX` и `MT_SERVE_JOBS`. Compose проверяет `/healthz` каждые 30 секунд, так
что `docker compose ps` показывает, здоров ли сервис.

**Путь записи.** В `data/` две папки:

```text
data/inbox/                          сюда кладут записи (mp4, mkv, mov, webm, m4a, mp3, wav, …)
data/jobs/20261108-141500-board/     по папке на запись
  job.json                           состояние: queued, running, done, failed; этап, проценты, ошибки
  log.txt                            полный вывод `auto`
  source.mp4                         запись, перенесённая из inbox
  board/                             результаты: transcript.md, board.html, board.pdf, screenshots/
```

Inbox проверяется каждые `MT_SERVE_POLL_S` секунд; файл берётся, когда его размер и время не менялись
`MT_SERVE_STABLE_S` секунд, поэтому копия по сети не берётся на середине. Временные имена (`.part`, `.tmp`,
`.crdownload`, имена с `.` или `~` в начале) пропускаются. Опрос вместо событий файловой системы: события
ненадёжно проходят через bind mount, SMB и NFS. Inbox может быть сетевой папкой, смонтированной в контейнер.

Опции для одной записи — в файле рядом с ней, с её именем плюс `.json`; его кладут раньше самой записи:

```json
{"title": "Планёрка", "date": "2026-11-05", "language": "ru", "summary": "none", "names": true}
```

Все ключи необязательны; `summary` — одно из `local`, `api`, `none` (и `claude`, `codex`, `opencode` на хосте).
Ошибка в файле проваливает задачу с причиной, запись не остаётся в inbox.

**Очередь и воркеры.** Задачи идут в порядке поступления, по `MT_SERVE_WORKERS` одновременно (по умолчанию 1).
Один запуск `large-v3` с диаризацией занимает 6–8 ГБ RAM или большую часть GPU на 12 ГБ, так что второй воркер
окупается, только если памяти хватает на двоих. Остановка сервиса (`docker compose stop`, перезапуск, деплой)
останавливает идущие задачи и ставит их обратно в очередь: следующий запуск продолжает их и переиспользует уже
извлечённый звук. Задача, оборванная падением, запускается снова до `MT_SERVE_MAX_ATTEMPTS` раз.
`MT_SERVE_JOB_TIMEOUT_S` останавливает зависшую задачу; для длинных записей на CPU ставьте часы (например `21600`).

**Как забрать результаты.**

- в браузере: таблица задач с прогрессом, ссылки на HTML, PDF и Markdown, лог, кнопки повторить, отменить,
  удалить;
- из папки: `data/jobs/<id>/<имя>/`, например расшаренной по SMB или синхронизируемой;
- по webhook: на `MT_SERVE_WEBHOOK_URL` приходит POST с `{"event": "job.done", "job": {…}}`, когда задача
  закончилась (n8n, чат-бот, свой скрипт);
- в S3 или MinIO: с `MT_SERVE_S3_BUCKET` результаты копируются в бакет в `<prefix><id задачи>/`; ключи берутся из
  `AWS_ACCESS_KEY_ID` и `AWS_SECRET_ACCESS_KEY`, для MinIO нужен `MT_SERVE_S3_ENDPOINT`;
- через API, ниже.

**API.** Если задан `MT_SERVE_TOKEN`, каждый вызов `/api/*` требует `Authorization: Bearer <токен>` (или
`?token=` в ссылке). Интерактивное описание — на `/api/docs`, пока токен не задан.

| Вызов | Что делает |
| --- | --- |
| `POST` или `PUT /api/jobs?name=<файл>&title=&date=&language=&summary=&names=` | загрузить запись телом запроса; возвращает задачу |
| `GET /api/jobs` | все задачи, новые сверху |
| `GET /api/jobs/<id>` | одна задача: `state`, `stage`, `progress`, `error`, `warnings`, `artifacts`, `s3` |
| `GET /api/jobs/<id>/files/<имя>` | результат из `artifacts` или `log.txt` |
| `GET /api/jobs/<id>/log?tail=200` | конец лога |
| `POST /api/jobs/<id>/retry` | запустить завершённую или упавшую задачу снова |
| `POST /api/jobs/<id>/cancel` | остановить задачу в очереди или в работе |
| `DELETE /api/jobs/<id>` | удалить задачу, которая сейчас не выполняется, с её файлами |
| `GET /healthz` | жив ли сервис и размер очереди, без токена |

```bash
curl -T meeting.mp4 -H "Authorization: Bearer $MT_SERVE_TOKEN" \
  "http://127.0.0.1:8080/api/jobs?name=meeting.mp4&title=Board&summary=none"
curl -H "Authorization: Bearer $MT_SERVE_TOKEN" http://127.0.0.1:8080/api/jobs/<id>
curl -OJ -H "Authorization: Bearer $MT_SERVE_TOKEN" http://127.0.0.1:8080/api/jobs/<id>/files/<имя>.pdf
```

За reverse proxy поднимите его лимит тела запроса для загрузок (nginx: `client_max_body_size 8g;`). Завершённые
задачи хранятся, пока их не удалят; `MT_SERVE_KEEP_DAYS` удаляет старые, `MT_SERVE_KEEP_SOURCE=0` удаляет запись
после успешного запуска. Одну папку данных обслуживает один сервис: второй на той же `jobs/` не запустится. Все настройки: [настройки сервиса](SETTINGS.ru.md#сервис).

Зависимости сервиса (fastapi, uvicorn, boto3) уже есть в образе. На хосте `./transcribe serve` ставит их при
первом запуске и снова после обновления зависимостей.

## Где что лежит

- том `mt-cache` — это `/cache` в контейнере: модели Hugging Face, EasyOCR, nltk и `config.env` (`MT_CONFIG`);
- сбросить кэш моделей: `podman volume rm mt-cache`;
- все команды: [COMMANDS.ru.md](COMMANDS.ru.md).

## Примечания

- `MT_LLM_API_KEY` и `HF_TOKEN` уходят только в образы `localhost/meeting-transcribe*`; на любой другой
  `--image` обёртка выдаёт предупреждение, не передаёт их и монтирует отдельный том `mt-cache-foreign`, чтобы
  чужой запуск не читал доверенный `config.env` и не писал в доверенный кэш моделей.
- Машине с чужим UID: `transcribe-container --build` сам передаёт твои UID и GID в build-args; сборке руками
  нужны `--build-arg UID=$(id -u) --build-arg GID=$(id -g)`.
- `MT_*` и `HF_*` из окружения передаются в контейнер, кроме `HF_HOME`, `HF_HUB_CACHE` и `MT_EASYOCR_DIR`:
  образ привязывает их к `/cache`.
- `--build` и `--pull`: используй один из них, не оба.

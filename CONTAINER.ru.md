[English](CONTAINER.md) | **Русский**

# Контейнер

Те же команды, что и у `./transcribe`, но внутри podman: папка с записью смонтирована по своему пути, поэтому
пути совпадают с хостовыми. Модели и настройки лежат в томе `mt-cache`.

## Требования

- Linux: `./transcribe-container` на других системах не запускается; на macOS запускай `./transcribe` напрямую.
- podman 6+, rootless подходит.
- Диск: около 4 GB для CPU-образа и около 10 GB для cuda.
- NVIDIA GPU: nvidia-container-toolkit с настроенным CDI; обёртка запускает
  `podman run --device nvidia.com/gpu=all`.

## Сборка

```bash
podman build --build-arg DEVICE=cpu -t localhost/meeting-transcribe:latest .
podman build --build-arg DEVICE=cuda -t localhost/meeting-transcribe:cuda .
```

Если образа нет, `./transcribe-container` соберёт его сам и передаст твои UID/GID в build-args. Сборка руками на
машине, где `id -u` не 1000, тоже требует их:
`podman build --build-arg UID=$(id -u) --build-arg GID=$(id -g) …`.

## Первый запуск (модели)

```bash
./transcribe-container setup --language ru,en --summary none --yes
```

Зависимости уже в образе: `setup` один раз скачивает модели (whisper, выравнивание, EasyOCR, nltk) в том
`mt-cache` и пишет настройки в `/cache/config.env` — тоже в том. Повторный `setup` уже скачанное не трогает.

## Запуск

```bash
./transcribe-container auto meeting.mp4 out/board-1105
```

Запускай из папки с записью. Команды и опции те же, что у `./transcribe` — весь список в
[COMMANDS.ru.md](COMMANDS.ru.md). Флаги идут перед командой: `--cuda`, `--image ИМЯ`, `--build`, `--pull`.

## Кто пишет саммари

- `--summary api`: задай `MT_LLM_BASE_URL`, `MT_LLM_MODEL` и `MT_LLM_API_KEY` в окружении; ключ передаётся в
  контейнер по имени и не запекается в образ.
- `--summary none`: без саммари.
- `claude`, `codex` и `opencode` в контейнере не работают — запускай
  `./transcribe summarize <out_dir> --llm …` на хосте.
- `--summary local` пишет Ollama на хосте; адрес для контейнера:
  `OLLAMA_HOST=http://host.containers.internal:11434 ./transcribe-container auto meeting.mp4 out/board-1105`.

## NVIDIA GPU

```bash
./transcribe-container --cuda setup --language ru,en --summary none --yes
MT_DEVICE=cuda ./transcribe-container --cuda auto meeting.mp4 out/board-1105
```

`--cuda` берёт образ `localhost/meeting-transcribe:cuda` и прокидывает GPU хоста. Он ставит `MT_DEVICE=cuda`,
если ты не задал его сам, и отказывается работать с `MT_DEVICE=cuda` на не-cuda-образе.

## Compose

```bash
podman-compose build
podman-compose run --rm transcribe auto /work/meeting.mp4 /work/out
```

Каталог репозитория смонтирован в `/work`. У тома `mt-cache` фиксированное имя, поэтому модели и настройки
общие с `./transcribe-container`.

## Где что лежит

- том `mt-cache` — это `/cache` в контейнере: модели Hugging Face, EasyOCR, nltk и `config.env` (`MT_CONFIG`);
- сбросить кэш моделей: `podman volume rm mt-cache`;
- все команды: [COMMANDS.ru.md](COMMANDS.ru.md).

## Примечания

- `MT_LLM_API_KEY` и `HF_TOKEN` уходят только в образы `localhost/meeting-transcribe*`; на любой другой
  `--image` обёртка выдаёт предупреждение и не передаёт их.
- Машине с чужим UID: `./transcribe-container --build` сам передаёт твои UID и GID в build-args; сборке руками
  нужны `--build-arg UID=$(id -u) --build-arg GID=$(id -g)`.
- `MT_*` и `HF_*` из окружения передаются в контейнер, кроме `HF_HOME`, `HF_HUB_CACHE` и `MT_EASYOCR_DIR`:
  образ привязывает их к `/cache`.
- `--build` и `--pull`: используй один из них, не оба.

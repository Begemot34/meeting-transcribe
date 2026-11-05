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
podman-compose build
podman-compose run --rm transcribe auto /work/meeting.mp4 /work/out
```

Каталог репозитория смонтирован в `/work`. У тома `mt-cache` фиксированное имя, поэтому модели и настройки
общие с `transcribe-container`.

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

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
podman-compose build
podman-compose run --rm transcribe auto /work/meeting.mp4 /work/out
```

The repo directory is mounted at `/work`. The `mt-cache` volume has a fixed name, so the models and settings are
shared with `transcribe-container`.

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

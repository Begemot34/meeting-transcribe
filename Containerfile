# meeting-transcribe as a container image, two torch builds chosen at build time:
#   podman build --build-arg DEVICE=cpu -t localhost/meeting-transcribe:latest .   # CPU, ~2.5 GB
#   podman build --build-arg DEVICE=cuda -t localhost/meeting-transcribe:cuda .    # NVIDIA GPU, ~5 GB
# Run through ./transcribe-container: it bind-mounts the current directory and keeps
# the model caches in the named volume mt-cache. As a service: compose.yaml, `serve` (CONTAINER.md, Service).
FROM python:3.11-slim-bookworm

ARG DEVICE=cpu
# the image matches its torch build; env wins over the setup file
ENV MT_DEVICE=${DEVICE}

# what the build itself needs; the runtime cache variables come after the dependency layer
ENV PYTHONUTF8=1 \
    PYANNOTE_METRICS_ENABLED=false \
    HF_HUB_DISABLE_XET=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# uv stays in the image: ./transcribe runs `uv sync` itself when uv.lock is out of sync,
# without it the run would curl a fresh uv
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

WORKDIR /app

# dependencies live in their own layer: scripts and models change often, this layer does not
COPY pyproject.toml uv.lock ./

# ./transcribe re-syncs when uv.lock is newer than .venv/.synced or its content differs from the
# device: write the build name and equalize mtimes, so a container never re-syncs at run time.
# The ffmpeg link spares ./transcribe a write into the root-owned .venv at run time.
# the extra `serve` (fastapi, uvicorn, boto3) is the web API of ./transcribe serve: small, so every image has it
RUN uv sync --frozen --extra "$DEVICE" --extra serve --compile-bytecode --quiet \
    && echo "$DEVICE" > .venv/.synced \
    && touch -r .venv/.synced uv.lock \
    && ln -sf /usr/bin/ffmpeg .venv/bin/ffmpeg \
    && uv cache clean

COPY transcribe SKILL.md ./
COPY scripts ./scripts
COPY models ./models

# everything ./transcribe downloads on first use (whisper, alignment, EasyOCR, nltk) lands on
# /cache — a named volume, so the image and --rm containers stay small and setup runs once
ENV PATH="/app/.venv/bin:${PATH}" \
    HF_HOME=/cache/huggingface \
    MT_EASYOCR_DIR=/cache/easyocr \
    NLTK_DATA=/cache/nltk \
    XDG_CACHE_HOME=/cache/xdg \
    HOME=/cache \
    MT_SERVE_DATA=/data

# UID/GID follow the host user (podman build --build-arg UID=$(id -u) --build-arg GID=$(id -g)),
# so bind mounts and the cache volume stay writable for whoever built the image
ARG UID=1000
ARG GID=1000
RUN groupadd -g "$GID" mt \
    && useradd -l -u "$UID" -g mt -d /cache -s /usr/sbin/nologin mt \
    && mkdir -p /cache /data \
    && chown mt:mt /app /cache /data

USER mt

# ./transcribe serve: the web UI and the API
EXPOSE 8080

LABEL org.opencontainers.image.title="meeting-transcribe" \
      org.opencontainers.image.description="Local meeting transcripts by speaker with screenshots: whisperx + pyannote, CLI and service" \
      org.opencontainers.image.source="https://github.com/Begemot34/meeting-transcribe" \
      org.opencontainers.image.licenses="MIT"

ENTRYPOINT ["/app/transcribe"]
CMD ["help"]

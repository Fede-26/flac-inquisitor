FROM python:3.15-slim-trixie

# ffmpeg decodes audio and draws spectrograms. It runs as an external program;
# the integrity check hashes its output, so the flac tools are not needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    PATH="/app/.venv/bin:$PATH"

# Dependencies first, so code changes do not invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY README.md ./
COPY flac_inquisitor ./flac_inquisitor
RUN uv sync --frozen

# The library, the quarantine and the database are mounted at run time; see
# compose.example.yaml. The container does not need root: run it with the
# `user:` key of Compose as the owner of the music files.
VOLUME ["/data"]
EXPOSE 8080
CMD ["uvicorn", "flac_inquisitor.main:app", "--host", "0.0.0.0", "--port", "8080"]

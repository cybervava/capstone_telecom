# syntax=docker/dockerfile:1
# One image for every process: care-api, care-worker, care-executor, care-admin and the mock
# OSS/BSS gateway. Which one runs is decided by the container command.

FROM python:3.12-slim-bookworm AS build
# The package manager is copied in at a pinned version; it is not part of the runtime image.
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# Dependencies first, so this layer is reused until the lockfile changes.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --system care && useradd --system --gid care --home-dir /app care \
    && mkdir -p /state /data/generated && chown care:care /state /data/generated
WORKDIR /app
COPY --from=build --chown=care:care /app/.venv /app/.venv
COPY --chown=care:care data/policies ./data/policies
COPY --chown=care:care data/knowledge ./data/knowledge
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER care
EXPOSE 8080 8081
CMD ["care-api"]

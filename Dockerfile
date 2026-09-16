# syntax=docker/dockerfile:1

FROM python:3.13-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.10.2 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY app ./app
COPY rules ./rules
COPY alembic.ini ./
COPY migrations ./migrations

RUN mkdir -p /app/data \
    && useradd --create-home --uid 10001 appuser \
    && chown appuser:appuser /app/data

LABEL org.opencontainers.image.source="https://github.com/nikolan123/portal2-beta-leaderboard"

USER appuser

EXPOSE 8000
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

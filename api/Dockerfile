# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY geodiff ./geodiff
COPY migrations ./migrations
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd --system --uid 10001 geodiff
USER geodiff
EXPOSE 8000
# Migrations: compose runs them as a separate one-shot service. Hosts without a release step
# (Render free) set RUN_MIGRATIONS_ON_START=1; concurrent starts serialize on an advisory lock.
# No --proxy-headers: X-Forwarded-For is client-controlled on Render (see CLIENT_IP_HEADER).
CMD ["sh", "-c", "if [ \"$RUN_MIGRATIONS_ON_START\" = 1 ]; then python -m geodiff.migrate || exit 1; fi; exec uvicorn geodiff.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-server-header --no-access-log"]

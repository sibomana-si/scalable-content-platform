# Multi-stage: the build stage compiles wheels, the runtime stage carries none of the
# toolchain. python:3.11-slim matches the CI interpreter, so a green CI run and a running
# container agree about the language version.

FROM python:3.11-slim AS builder

WORKDIR /build

# Build-only. mysqlclient and cryptography need a compiler for any wheel that is not prebuilt
# for this platform, and none of this belongs in the image that runs in production.
RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

# Runtime dependencies only, pinned to the locked versions. `requirements.lock` is the
# frozen set for the whole project, so installing it directly would ship mkdocs, pytest and
# ruff into the runtime image. Using it as a constraints file instead resolves only the
# runtime tree while still pinning every version to the one this image was tested with.
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt -c requirements.lock


FROM python:3.11-slim AS runtime

# PYTHONDONTWRITEBYTECODE: a read-only filesystem cannot host .pyc files.
# PYTHONUNBUFFERED: logs must reach the collector as they happen, not when a buffer fills.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_PORT=8000

# curl is the HEALTHCHECK's only dependency; installing it here keeps the check inside the
# container rather than relying on the orchestrator to reach in.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root, and it owns nothing it runs. A compromised process cannot rewrite its own code.
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* \
    && rm -rf /wheels

# Application code only. `.dockerignore` keeps tests, docs, `.venv/` and `.env` out.
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser migrations ./migrations
COPY --chown=appuser:appuser app ./app

USER appuser

EXPOSE 8000

# Liveness has no dependencies, so a container that fails this is genuinely broken rather
# than merely waiting on MySQL. Readiness is the orchestrator's job, not the daemon's.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS "http://localhost:${APP_PORT}/health/live" || exit 1

# One worker per container. Scaling is horizontal — more replicas, not more workers — so the
# orchestrator sees real per-instance load and can act on it.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT}"]

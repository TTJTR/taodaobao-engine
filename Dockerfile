FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m pip wheel --wheel-dir /wheels .


FROM node:22-slim AS pptx-builder

WORKDIR /renderer
COPY sidecar/pptx-renderer/package.json sidecar/pptx-renderer/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts
COPY sidecar/pptx-renderer/render.mjs ./render.mjs


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/home/app/.local/bin:${PATH}"

RUN apt-get update \
    && apt-get install --no-install-recommends --yes nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /home/app app

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app static ./static
COPY --from=pptx-builder --chown=app:app /renderer ./sidecar/pptx-renderer
COPY --chown=app:app start.sh ./start.sh
RUN mkdir -p /app/exports \
    && chown app:app /app/exports \
    && chmod 755 ./start.sh

USER app

EXPOSE 8000

ENTRYPOINT ["./start.sh"]

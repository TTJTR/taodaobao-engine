FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m pip download --dest /wheels \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
        'torch==2.8.0+cpu' 'torchvision==0.23.0+cpu' \
    && python -m pip install --no-index --find-links /wheels \
        'torch==2.8.0+cpu' 'torchvision==0.23.0+cpu' \
    && printf 'torch==2.8.0+cpu\ntorchvision==0.23.0+cpu\n' > /tmp/parser-constraints.txt \
    && python -m pip wheel --wheel-dir /wheels --find-links /wheels \
        --constraint /tmp/parser-constraints.txt '.[v2-parser]'


FROM node:22-slim AS pptx-builder

WORKDIR /renderer
COPY sidecar/pptx-renderer/package.json sidecar/pptx-renderer/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts
COPY sidecar/pptx-renderer/render.mjs ./render.mjs


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TORCHDYNAMO_DISABLE=1 \
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
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libxcb1 \
    && rm -rf /var/lib/apt/lists/*
RUN rapidocr_models="$(python -c 'from pathlib import Path; import rapidocr; print(Path(rapidocr.__file__).parent / "models")')" \
    && mkdir -p "${rapidocr_models}" \
    && chown -R app:app "${rapidocr_models}"

COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app static ./static
COPY --from=pptx-builder --chown=app:app /renderer ./sidecar/pptx-renderer
COPY --chown=app:app start.sh ./start.sh
RUN mkdir -p /app/data/uploads /app/data/exports /app/data/integrations \
    && chown -R app:app /app/data \
    && chmod 755 ./start.sh

USER app

EXPOSE 8000

ENTRYPOINT ["./start.sh"]

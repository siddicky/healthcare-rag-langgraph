# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12.12
ARG UV_VERSION=0.11.16

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY healthcare_rag ./healthcare_rag
COPY server ./server
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable \
    && /opt/venv/bin/python -c \
      "from healthcare_rag.processors.privacy import PrivacySanitizer; PrivacySanitizer().initialize()"

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

COPY --from=uv /uv /uvx /usr/local/bin/

ENV PATH=/opt/venv/bin:$PATH \
    PRESIDIO_DEVICE=cpu \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    SERVER_LOCAL_DEV=0

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# langgraph.json:28-32 dockerfile_lines parity EXACTLY
RUN pip install --no-cache-dir presidio-analyzer==2.2.364 spacy==3.8.15 'en-core-web-sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl#sha256=1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85'
RUN python -c "import spacy; from presidio_analyzer import AnalyzerEngine; spacy.load('en_core_web_sm'); AnalyzerEngine()"

COPY data/chunks_lipitor.json data/chunks_metformin.json ./data/
COPY langgraph.json ./langgraph.json
COPY healthcare_rag ./healthcare_rag
COPY server ./server

USER app

CMD ["python", "-m", "server"]

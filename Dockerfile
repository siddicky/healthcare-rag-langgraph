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
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable \
    && /opt/venv/bin/python -c \
      "from healthcare_rag.processors.privacy import PrivacySanitizer; PrivacySanitizer().initialize()"

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PATH=/opt/venv/bin:$PATH \
    PRESIDIO_DEVICE=cpu \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY data/chunks_lipitor.json data/chunks_metformin.json ./data/

USER app

CMD ["python", "-m", "healthcare_rag"]

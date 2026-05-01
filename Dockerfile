# syntax=docker/dockerfile:1.23

FROM harbor.zkm.de/dockerhub-cache/library/python:3.14-slim AS builder
ENV POETRY_VERSION=1.8.4 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1
WORKDIR /app
RUN pip install "poetry==${POETRY_VERSION}"
COPY pyproject.toml poetry.lock* ./
RUN poetry install --only main --no-root
COPY imap_oidc_bridge ./imap_oidc_bridge
RUN poetry install --only main

FROM harbor.zkm.de/dockerhub-cache/library/python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --home /app app \
    && mkdir -p /data && chown app:app /data
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/imap_oidc_bridge /app/imap_oidc_bridge
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status==200 else 1)"
ENTRYPOINT ["python", "-m", "imap_oidc_bridge"]

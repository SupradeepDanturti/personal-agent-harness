# syntax=docker/dockerfile:1
# Digests pin the complete image indexes; refresh deliberately with CI validation.
FROM node:24-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
COPY frontend/tests ./tests
RUN npm run check && npm run build

FROM python:3.14-slim-bookworm@sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/data/.assistant/home \
    UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    PATH=/app/.venv/bin:$PATH \
    HARNESS_ROOT=/app ASSISTANT_DATA_DIR=/data/.assistant \
    FRONTEND_DIST_DIR=/app/frontend/dist BROWSER_CHANNEL=chromium \
    CONTAINER_MODE=true LOCAL_PORT=8787 ENABLE_DOCKER_EXECUTION=false
WORKDIR /app
# Playwright's bundled Node must start before --with-deps can install browsers.
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 libstdc++6 \
    && python -m pip install --no-cache-dir uv==0.10.7
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/src ./backend/src
RUN uv sync --locked --all-packages --no-dev --no-editable \
    && python -m playwright install --with-deps chromium \
    && uv cache clean \
    && rm -rf /var/lib/apt/lists/*
COPY --from=frontend-build /build/frontend/dist ./frontend/dist
RUN groupadd --gid 10001 harness \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /data/.assistant/home harness \
    && mkdir -p /data/.assistant/home /test-work \
    && chown 10001:10001 /data/.assistant /data/.assistant/home /test-work
USER 10001:10001
EXPOSE 8787
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8787/health', timeout=3).read()"]
CMD ["harness-server"]

# Test entrypoints and Node's historical SQLite fixture are absent from runtime.
FROM runtime AS acceptance
USER root
COPY --from=frontend-build /usr/local/bin/node /usr/local/bin/node
COPY backend/tests /app/backend/tests
COPY legacy/typescript/src/domain.ts /app/legacy/typescript/src/domain.ts
COPY legacy/typescript/src/local/store.ts legacy/typescript/src/local/workflows.ts /app/legacy/typescript/src/local/
COPY scripts/container-acceptance.py scripts/container-fixture.py /app/scripts/
ENV PYTHONPATH=/app/backend HOME=/tmp/harness
WORKDIR /test-work
USER 10001:10001
CMD ["python", "-u", "/app/scripts/container-acceptance.py"]

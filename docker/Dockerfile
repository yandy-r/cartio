# Cartio images for compose.dev.yml (target dev) and compose.yml (target production, the default).
# The production target is an example deployment of the controller. The packaged collector is
# YAN-1641.
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    # The parsers' ttp library caches into its own package unless told otherwise.
    TTPCACHEFOLDER=/tmp

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY docker/entrypoint.sh docker/seed.sh docker/collector.sh docker/serve.sh docker/migrate-watch.sh \
    /usr/local/bin/
RUN useradd --create-home --uid 1000 cartio \
    && mkdir /keys /collector /app/static && chown cartio /keys /collector /app/static
ENTRYPOINT ["entrypoint.sh"]

# Editable install: compose.dev.yml mounts ./packages over /app/packages, and runserver reloads
# on every change. Rebuild only when dependencies change.
FROM base AS dev
ENV UV_COMPILE_BYTECODE=0 \
    PYTHONDONTWRITEBYTECODE=1
RUN uv sync --locked --no-dev
# The sanitized lab, for load_lab. Nothing else from tests.
COPY tests/fixtures/sanitized tests/fixtures/sanitized
USER cartio
CMD ["cartio-controller", "runserver", "0.0.0.0:8000"]

FROM base AS production
RUN uv sync --locked --no-dev --no-editable
USER cartio
CMD ["serve.sh"]

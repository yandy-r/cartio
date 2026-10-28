# A Cartio image for trying the controller (and a collector) locally with compose.yaml.
# Not the packaged collector or a production controller: that is YAN-1641.
FROM python:3.12-slim

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
RUN uv sync --locked --no-dev --no-editable

# The sanitized lab, for load_lab. Nothing else from tests.
COPY tests/fixtures/sanitized tests/fixtures/sanitized
COPY docker/entrypoint.sh docker/seed.sh docker/collector.sh /usr/local/bin/

RUN useradd --create-home cartio && mkdir /keys /collector && chown cartio /keys /collector
USER cartio

ENTRYPOINT ["entrypoint.sh"]
CMD ["cartio-controller", "runserver", "0.0.0.0:8000"]

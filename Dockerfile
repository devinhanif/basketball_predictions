# Minimal CPU-only image for running the smoke test in CI and locally.
# Pinned base, deps installed from the uv lockfile for reproducibility.
FROM python:3.12.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

# `make` is required for the `make smoke` entrypoint below; the slim base
# does not ship it.
RUN apt-get update && apt-get install --no-install-recommends -y make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --extra dev --frozen --no-install-project

COPY nba/ nba/
COPY research/ research/
COPY tests/ tests/
COPY Makefile ./
RUN uv sync --extra dev --frozen

ENV PATH="/opt/venv/bin:${PATH}"

CMD ["make", "smoke"]

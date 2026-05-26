FROM python:3.12-slim

# Install uv from its official distroless image (small, pinned, fast).
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Cache the dependency layer separately from source. uv resolves from the
# lockfile and won't touch the network if it's complete.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY collectors ./collectors
COPY forecasts ./forecasts
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

# Cloud Run Jobs override args per Job, e.g. ["collectors.bls_employment"].
ENTRYPOINT ["python", "-m"]

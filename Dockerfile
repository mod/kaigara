FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen
COPY . .
# Use venv Python directly so uv doesn't try to recreate the venv at runtime
# (critical for read-only root filesystem on sandbox container)
ENV PATH="/app/.venv/bin:$PATH"

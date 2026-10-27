# syntax=docker/dockerfile:1
FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS backend-base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./

FROM backend-base AS backend-development
CMD ["/opt/venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM backend-base AS production
COPY --from=frontend-build /frontend/dist ./static
ENV PORT=8000
CMD ["sh", "-c", "exec /opt/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]


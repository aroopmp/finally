# Stage 1: build the Next.js static export
FROM node:20-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: FastAPI serving API + static frontend on one port
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
COPY --from=frontend /build/out /app/static

ENV STATIC_DIR=/app/static \
    DB_PATH=/app/db/finally.db \
    PYTHONUNBUFFERED=1
RUN mkdir -p /app/db
EXPOSE 8000
CMD ["uv", "run", "--frozen", "--no-dev", "uvicorn", "app.main:create_default_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

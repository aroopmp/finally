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
ENV PATH="/app/backend/.venv/bin:$PATH"
RUN mkdir -p /app/db
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "app.main:create_default_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

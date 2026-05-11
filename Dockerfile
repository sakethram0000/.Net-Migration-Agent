# ── Stage 1: Build React frontend ────────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./

# Override outDir to a fixed location inside this stage
RUN npx vite build --outDir /frontend-dist --emptyOutDir


# ── Stage 2: Python + .NET 8 ─────────────────────────────────────────────
FROM python:3.11-slim-bookworm

# Install .NET 8 SDK
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget apt-transport-https ca-certificates \
    && wget https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb -O /tmp/dotnet.deb \
    && dpkg -i /tmp/dotnet.deb \
    && rm /tmp/dotnet.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends dotnet-sdk-8.0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY MigrationAgent.API/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Backend source
COPY MigrationAgent.API/ .

# Frontend build output → where FastAPI serves it from
COPY --from=frontend-builder /frontend-dist ./frontend/dist

# Runtime directories
RUN mkdir -p uploads outputs/migrated

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

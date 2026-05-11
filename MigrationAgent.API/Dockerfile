# ── Stage 1: Build React frontend ────────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /app/frontend

COPY ../frontend/package*.json ./
RUN npm ci

COPY ../frontend ./
RUN npm run build
# Output lands in /app/MigrationAgent.API/frontend/dist via vite.config.js
# But inside Docker we override outDir to a known location
RUN ls -la /app/frontend/


# ── Stage 2: Python + .NET runtime ───────────────────────────────────────
FROM python:3.11-slim-bookworm

# Install .NET 8 SDK for dotnet build/run in validator and runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    apt-transport-https \
    ca-certificates \
    && wget https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb -O packages-microsoft-prod.deb \
    && dpkg -i packages-microsoft-prod.deb \
    && rm packages-microsoft-prod.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends dotnet-sdk-8.0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY . .

# Copy built frontend from stage 1 into the location FastAPI serves from
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Create required runtime directories
RUN mkdir -p uploads outputs/migrated

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

# .NET Migration Agent

Agentic .NET application migration tool built with React, FastAPI, Microsoft Agent Framework-ready orchestration, and optional OpenAI/Azure OpenAI model support.

## Stack

- Frontend: React + Vite
- Backend: Python + FastAPI
- Agent framework: Microsoft Agent Framework adapter with local workflow fallback
- LLM providers: Azure OpenAI, OpenAI, optional Groq
- Validation: .NET SDK CLI (`dotnet restore`, `dotnet build`)

## Current Workflow

1. Upload a `.zip`, `.sln`, `.csproj`, `.cs`, or fetch a public GitHub repo.
2. Inventory agent scans frameworks, project files, packages, and migration blockers.
3. Migration planner selects target framework.
4. Project upgrade tools update `.csproj` files and remove obsolete package references.
5. Code cleanup tools apply deterministic source fixes.
6. Optional LLM rewrite runs when credentials are configured.
7. Build validator runs restore/build.
8. Report and migrated zip are generated.

## Run Locally

Install backend dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Install/build frontend:

```powershell
cd frontend
npm install
npm run build
cd ..
```

Run:

```powershell
python -B run_fastapi.py
```

Open:

```text
http://127.0.0.1:8050
```

## Optional LLM Configuration

OpenAI:

```powershell
$env:OPENAI_API_KEY="your-key"
$env:OPENAI_MODEL="gpt-4.1"
```

Azure OpenAI:

```powershell
$env:AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com"
$env:AZURE_OPENAI_API_KEY="your-key"
$env:AZURE_OPENAI_DEPLOYMENT="your-deployment"
$env:AZURE_OPENAI_API_VERSION="2024-10-21"
```

## Microsoft Agent Framework

The backend has a Microsoft Agent Framework adapter and planned multi-agent roles. If the Microsoft Agent Framework package is installed in the environment, the adapter reports it as available; otherwise the same workflow runs locally through deterministic tools.

## Deploy On Render

This project is Render-ready using Docker. Docker is recommended because the agent needs:

- Python/FastAPI for backend APIs
- Node.js for the React/Vite build
- .NET SDK 8 for migrated app build/run/smoke-test features

### Files Used By Render

- `Dockerfile`
- `render.yaml`
- `run_fastapi.py`

### Render Blueprint Deployment

1. Push this folder to GitHub.
2. In Render, choose **New +**.
3. Choose **Blueprint**.
4. Select the GitHub repository.
5. Render will read `render.yaml`.
6. Create the service.

### Manual Render Web Service Deployment

Use these settings if you do not use Blueprint:

- **Environment**: Docker
- **Dockerfile Path**: `./Dockerfile`
- **Health Check Path**: `/health`

If your GitHub repository root is the parent folder, set:

```text
Root Directory: migration agent
```

If your repository root is already this folder, leave Root Directory blank.

### Optional LLM Environment Variables

OpenAI:

```text
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-4.1
```

Azure OpenAI:

```text
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_DEPLOYMENT=your-deployment
AZURE_OPENAI_API_VERSION=2024-10-21
```

Without LLM keys, the agent still runs with deterministic local migration analysis and reports.

### Render Runtime Notes

Uploaded source, workspaces, and generated reports are stored on the container filesystem. On Render free/standard ephemeral services, these files can disappear after redeploy/restart. For long-term report persistence, add external storage later.

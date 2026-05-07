from __future__ import annotations

import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agents.inventory import inventory
from .agents.orchestrator import MigrationOrchestrator
from .agents.runtime import RuntimeManager
from .agents.tools import report_to_csv, report_to_html, safe_extract


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST = ROOT / "frontend" / "dist"


class AnalyzeRequest(BaseModel):
    from_version: str = ".NET Framework 4.8"
    to_version: str = ".NET 8"


class MigrationRequest(BaseModel):
    from_version: str = ".NET Framework 4.8"
    to_version: str = ".NET 8"
    scopes: dict[str, bool] = {}


class GithubRequest(BaseModel):
    url: str


app = FastAPI(title=".NET Migration Agent API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
orchestrator = MigrationOrchestrator(ROOT)
runtime_manager = RuntimeManager()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "agent": ".NET Migration Agent", "runtime": orchestrator.runtime()}


@app.post("/api/files/upload")
async def upload(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    if orchestrator.uploads.exists():
        shutil.rmtree(orchestrator.uploads)
    orchestrator.uploads.mkdir(parents=True, exist_ok=True)
    uploaded = []
    for file in files:
        name = Path(file.filename or "upload.bin").name
        data = await file.read()
        if name.lower().endswith(".zip"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as temp:
                temp.write(data)
                temp_path = Path(temp.name)
            safe_extract(temp_path, orchestrator.uploads)
            temp_path.unlink(missing_ok=True)
            uploaded.append({"name": name, "type": "zip", "size": len(data)})
        else:
            target = orchestrator.uploads / name
            target.write_bytes(data)
            uploaded.append({"name": name, "type": "file", "size": len(data)})
    return {"success": True, "files": uploaded}


@app.post("/api/files/github")
async def github(request: GithubRequest) -> dict[str, Any]:
    import re

    match = re.match(r"https?://github\.com/([^/]+)/([^/]+)", request.url.strip().rstrip("/"))
    if not match:
        raise HTTPException(status_code=400, detail="Use a public GitHub URL like https://github.com/owner/repo")
    owner, repo = match.group(1), match.group(2)
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        zip_url = ""
        branch = ""
        for candidate_branch in ["main", "master"]:
            candidate = f"https://github.com/{owner}/{repo}/archive/refs/heads/{candidate_branch}.zip"
            response = await client.head(candidate)
            if response.status_code == 200:
                zip_url = candidate
                branch = candidate_branch
                break
        if not zip_url:
            raise HTTPException(status_code=404, detail="Could not find main or master branch")
        data = (await client.get(zip_url)).content
    if orchestrator.uploads.exists():
        shutil.rmtree(orchestrator.uploads)
    orchestrator.uploads.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as temp:
        temp.write(data)
        temp_path = Path(temp.name)
    safe_extract(temp_path, orchestrator.uploads)
    temp_path.unlink(missing_ok=True)
    inv = inventory(orchestrator.uploads, ".NET Framework 4.8", ".NET 8")
    return {"success": True, "repo": f"{owner}/{repo}", "branch": branch, "inventory": inv}


@app.post("/api/migration/analyze")
def analyze(request: AnalyzeRequest) -> dict[str, Any]:
    return orchestrator.analyze_current_upload(request.from_version, request.to_version)


@app.post("/api/migration/start")
def start_migration(request: MigrationRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    if not orchestrator.uploads.exists() or not any(orchestrator.uploads.rglob("*")):
        raise HTTPException(status_code=400, detail="Upload a project zip or source files first")
    job_id = orchestrator.new_job(orchestrator.uploads, request.from_version, request.to_version, request.scopes)
    background_tasks.add_task(orchestrator.run, job_id)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/migration/status/{job_id}")
def status(job_id: str) -> dict[str, Any]:
    job = orchestrator.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/migration/report/{job_id}")
def report(job_id: str) -> dict[str, Any]:
    job = orchestrator.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.get("report") or job


@app.get("/api/migration/download/{job_id}")
def download(job_id: str):
    job = orchestrator.jobs.get(job_id)
    if not job or not job.get("download_path"):
        raise HTTPException(status_code=404, detail="Download is not ready")
    return FileResponse(job["download_path"], media_type="application/zip", filename="migrated-project.zip")


@app.get("/api/reports/{job_id}/{kind}/preview")
def report_preview(job_id: str, kind: str):
    report = get_job_report(job_id)
    return HTMLResponse(report_to_html(report, kind))


@app.get("/api/reports/{job_id}/{kind}/download")
def report_download(job_id: str, kind: str, format: str = "csv"):
    report = get_job_report(job_id)
    if format.lower() == "html":
        return HTMLResponse(report_to_html(report, kind), headers={"Content-Disposition": f'attachment; filename="{kind}-report.html"'})
    csv_text = report_to_csv(report, kind)
    return Response(csv_text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{kind}-report.csv"'})


def get_job_report(job_id: str) -> dict[str, Any]:
    job = orchestrator.jobs.get(job_id)
    if not job or not job.get("report"):
        raise HTTPException(status_code=404, detail="Report is not ready")
    return job["report"]


@app.post("/api/runtime/start/{job_id}")
def start_runtime(job_id: str) -> dict[str, Any]:
    job = orchestrator.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.get("output"):
        raise HTTPException(status_code=400, detail="Migrated output is not available")
    return runtime_manager.start(job_id, job["output"])


@app.post("/api/runtime/stop/{job_id}")
def stop_runtime(job_id: str) -> dict[str, Any]:
    return runtime_manager.stop(job_id)


@app.get("/api/runtime/status/{job_id}")
def runtime_status(job_id: str) -> dict[str, Any]:
    return runtime_manager.status(job_id)


@app.post("/api/runtime/smoke-test/{job_id}")
def smoke_test(job_id: str) -> dict[str, Any]:
    job = orchestrator.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.get("output"):
        raise HTTPException(status_code=400, detail="Migrated output is not available")
    runtime = runtime_manager.start(job_id, job["output"])
    if runtime.get("status") != "running" or not runtime.get("url"):
        return {"status": "failed", "summary": "Application did not start.", "runtime": runtime, "checks": []}
    checks = run_smoke_checks(runtime["url"])
    passed = sum(1 for check in checks if check["passed"])
    result = {
        "status": "passed" if passed == len(checks) else "needs_review",
        "summary": f"{passed}/{len(checks)} smoke checks passed.",
        "url": runtime["url"],
        "checks": checks,
        "runtime": runtime,
    }
    job["smoke_test"] = result
    if job.get("report"):
        job["report"]["smoke_test"] = result
    return result


def run_smoke_checks(base_url: str) -> list[dict[str, Any]]:
    candidates = [
        ("Home page", "/"),
        ("Orders API", "/api/orders"),
        ("Health endpoint", "/health"),
    ]
    results = []
    for name, path in candidates:
        url = f"{base_url.rstrip('/')}{path}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "MigrationAgentSmokeTest/1.0"})
            with urllib.request.urlopen(request, timeout=12) as response:
                body = response.read(800).decode("utf-8", errors="ignore")
                status = int(response.status)
            results.append({"name": name, "path": path, "status_code": status, "passed": 200 <= status < 400, "sample": body[:240]})
        except urllib.error.HTTPError as exc:
            optional = path == "/health"
            results.append({"name": name, "path": path, "status_code": exc.code, "passed": optional, "optional": optional, "sample": exc.reason})
        except Exception as exc:
            optional = path == "/health"
            results.append({"name": name, "path": path, "status_code": 0, "passed": optional, "optional": optional, "sample": str(exc)})
    return results


if FRONTEND_DIST.exists():
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")


@app.get("/")
def index():
    index_file = FRONTEND_DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "Build the React frontend with npm run build."}


@app.get("/{path:path}")
def spa(path: str):
    if path.startswith("api/") or path == "health":
        raise HTTPException(status_code=404, detail="Not found")
    index_file = FRONTEND_DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "Frontend build not found."}

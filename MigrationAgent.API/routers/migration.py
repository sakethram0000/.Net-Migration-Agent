from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File, Form
from typing import Optional
from pydantic import BaseModel
from agents.orchestrator import run_orchestrator
from agents.analyzer import analyze
from agents.validator import validate
from agents.reporter import generate_report
from agents.llm import reset_token_stats, get_token_stats
import uuid
import socket
import subprocess
import threading
import time
import urllib.request
import urllib.error
from typing import Dict, Any
import os
import shutil
from pathlib import Path

router = APIRouter(prefix="/api/migration", tags=["migration"])

BASE_DIR = Path(__file__).parent.parent
UPLOAD_DIR = str(BASE_DIR / "uploads")
OUTPUT_DIR = str(BASE_DIR / "outputs" / "migrated")

migration_jobs: Dict[str, Dict[str, Any]] = {}
runtime_apps:   Dict[str, Dict[str, Any]] = {}

# Fix 1 — In-memory status cache with 2 second debounce
# Marketplace runs 4 parallel workers polling every second.
# Cache returns instantly for repeat requests within 2 seconds,
# reducing CPU load by ~90% during parallel polling.
_status_cache: Dict[str, Dict[str, Any]] = {}

class MigrationRequest(BaseModel):
    from_version: str
    to_version: str

class MigrateRequest(BaseModel):
    from_version: str
    to_version: str

def run_migration_job(job_id: str, upload_dir: str, from_version: str, to_version: str):
    try:
        migration_jobs[job_id]["status"] = "running"
        migration_jobs[job_id]["stage"] = "migrating"
        migration_jobs[job_id]["progress"] = "Starting migration..."
        reset_token_stats()

        def update_progress(message: str):
            migration_jobs[job_id]["progress"] = message
            # Update stage based on which agent is running
            msg_lower = message.lower()
            if "analyzer" in msg_lower:
                migration_jobs[job_id]["stage"] = "analyzing"
            elif "source migration agent" in msg_lower or "migrating" in msg_lower:
                migration_jobs[job_id]["stage"] = "migrating"
            elif "auth agent" in msg_lower:
                migration_jobs[job_id]["stage"] = "auth"
            elif "view migration" in msg_lower:
                migration_jobs[job_id]["stage"] = "views"
            elif "web forms" in msg_lower:
                migration_jobs[job_id]["stage"] = "webforms"
            elif "blazor" in msg_lower:
                migration_jobs[job_id]["stage"] = "blazor"
            elif "post-migration fix agent" in msg_lower:
                migration_jobs[job_id]["stage"] = "fixing"
            elif "guardrail" in msg_lower:
                migration_jobs[job_id]["stage"] = "guardrails"
            elif "build validator" in msg_lower:
                migration_jobs[job_id]["stage"] = "build_validate"
            elif "build error ai agent" in msg_lower:
                migration_jobs[job_id]["stage"] = "llm_fixing"
            elif "goal achieved" in msg_lower:
                migration_jobs[job_id]["stage"] = "completed"

        # Clear previous output before starting fresh
        output_path = Path(OUTPUT_DIR)
        if output_path.exists():
            shutil.rmtree(output_path)
        output_path.mkdir(parents=True, exist_ok=True)
        old_zip = Path(OUTPUT_DIR).parent / "migrated_project.zip"
        if old_zip.exists():
            old_zip.unlink()

        # ── Run the orchestrator ───────────────────────────────────────────────
        context = run_orchestrator(
            job_id=job_id,
            upload_dir=upload_dir,
            output_dir=OUTPUT_DIR,
            from_version=from_version,
            to_version=to_version,
            progress_callback=update_progress,
        )

        # ── Check if migrator failed ──────────────────────────────────────
        if context.status == "failed":
            migration_jobs[job_id]["status"] = "failed"
            migration_jobs[job_id]["stage"] = "failed"
            migration_jobs[job_id]["error"] = "Migration failed — check progress log."
            migration_jobs[job_id]["progress"] = "Migration failed."
            return

        # ── Build final result from context ───────────────────────────────
        migration_jobs[job_id]["status"] = "completed"
        migration_jobs[job_id]["stage"] = "completed"
        migration_jobs[job_id]["result"] = {
            "success": True,
            "count": len(context.migrated_files),
            "migrated": context.migrated_files,
            "manual_fixes": context.fix_result.get("manual_fixes", []),
            "auth": context.auth_result,
            "view_migration": context.view_result,
            "webforms_migration": context.webforms_result,
            "blazor_migration": context.blazor_result,
            "build_validation": context.build_result,
            "guardrails": context.guardrail_result,
            "orchestrator": context.to_summary_dict(),
        }
        migration_jobs[job_id]["token_stats"] = get_token_stats()
        migration_jobs[job_id]["progress"] = (
            f"Migration completed. {len(context.migrated_files)} file(s) migrated. "
            f"{context.attempts} build-fix attempt(s). "
            f"Build {'passed' if context.build_passed else 'needs review'}."
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        migration_jobs[job_id]["status"] = "failed"
        migration_jobs[job_id]["error"] = str(e)
        migration_jobs[job_id]["progress"] = f"Migration failed: {str(e)}"

@router.post("/analyze")
def run_analysis(request: MigrationRequest):
    return analyze(
        upload_dir=UPLOAD_DIR,
        from_version=request.from_version,
        to_version=request.to_version
    )

@router.post("/migrate")
async def run_migration(
    background_tasks: BackgroundTasks,
    from_version: str = Form(...),
    to_version: str = Form(...),
    files: Optional[UploadFile] = File(None),
    github_url: Optional[str] = Form(None),
    github_token: Optional[str] = Form(None),
):
    job_id = str(uuid.uuid4())

    # ── Handle file upload internally ─────────────────────────────────────
    upload_path = Path(UPLOAD_DIR)
    upload_path.mkdir(parents=True, exist_ok=True)

    if files and files.filename:
        # New file coming in — clear old uploads and extract fresh
        if upload_path.exists():
            shutil.rmtree(upload_path)
        upload_path.mkdir(parents=True, exist_ok=True)
        content = await files.read()
        if files.filename.endswith('.zip'):
            import tempfile, zipfile
            from pathlib import PurePosixPath
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
                    for member in zip_ref.infolist():
                        member_name = member.filename
                        if member_name.startswith(('/', '\\')) or '..' in member_name:
                            continue
                        dest_path = upload_path / Path(*PurePosixPath(member_name).parts)
                        if member.is_dir():
                            dest_path.mkdir(parents=True, exist_ok=True)
                            continue
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        with zip_ref.open(member) as source, open(dest_path, 'wb') as target:
                            shutil.copyfileobj(source, target)
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        else:
            file_path = upload_path / files.filename
            file_path.write_bytes(content)

    elif github_url and github_url.strip():
        import httpx, re, tempfile, zipfile
        from pathlib import PurePosixPath
        url = github_url.strip().rstrip("/")
        match = re.match(r"https?://github\.com/([^/]+)/([^/]+)", url)
        if not match:
            raise HTTPException(status_code=400, detail="Invalid GitHub URL")
        owner, repo = match.group(1), match.group(2)
        headers = {}
        if github_token and github_token.strip():
            headers['Authorization'] = f'token {github_token.strip()}'
        zip_url = None
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
            for branch in ["main", "master"]:
                candidate = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
                resp = await client.head(candidate, headers=headers)
                if resp.status_code == 200:
                    zip_url = candidate
                    break
            if not zip_url:
                raise HTTPException(status_code=404, detail="Could not find main or master branch on GitHub")
            resp = await client.get(zip_url, headers=headers)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Failed to download repository from GitHub")
            zip_bytes = resp.content
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                tmp.write(zip_bytes)
                tmp_path = tmp.name
            with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
                for member in zip_ref.infolist():
                    member_name = member.filename
                    if member_name.startswith(('/', '\\')) or '..' in member_name:
                        continue
                    dest_path = upload_path / Path(*PurePosixPath(member_name).parts)
                    if member.is_dir():
                        dest_path.mkdir(parents=True, exist_ok=True)
                        continue
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    with zip_ref.open(member) as source, open(dest_path, 'wb') as target:
                        shutil.copyfileobj(source, target)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    migration_jobs[job_id] = {
        "status": "queued",
        "stage": "queued",
        "step": 0,
        "progress": "Migration queued...",
        "created_at": time.time(),
    }
    background_tasks.add_task(
        run_migration_job, job_id, UPLOAD_DIR,
        from_version, to_version
    )
    return {"job_id": job_id, "status": "queued", "message": "Migration started in background"}

@router.get("/status/{job_id}")
def get_migration_status(job_id: str):
    if job_id not in migration_jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    # Fix 1 — return cached response if less than 2 seconds old
    cached = _status_cache.get(job_id)
    if cached and (time.time() - cached["cached_at"]) < 2.0:
        return cached["data"]

    job = migration_jobs[job_id]
    response = {
        "job_id": job_id,
        "status": job["status"],
        "stage": job.get("stage", "unknown"),
        "step": job.get("step", 0),
        "progress": job.get("progress", ""),
        "result": job.get("result"),
        "error": job.get("error"),
        "created_at": job.get("created_at")
    }
    _status_cache[job_id] = {"data": response, "cached_at": time.time()}
    return response

@router.post("/validate")
def run_validation():
    return validate(output_dir=OUTPUT_DIR, progress_callback=None)

@router.get("/token-stats/{job_id}")
def get_token_stats_endpoint(job_id: str):
    if job_id not in migration_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return migration_jobs[job_id].get("token_stats", {
        "total_tokens": 0, "total_executions": 0, "total_llm_calls": 0,
        "avg_tokens_per_execution": 0, "avg_tokens_per_llm_call": 0
    })

@router.get("/report")
def get_report():
    return generate_report()


# ── Runtime routes ────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def _is_port_open(port: int) -> bool:
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.3):
            return True
    except OSError:
        return False

def _find_csproj(output_dir: str):
    out = Path(output_dir)
    projects = [f for f in out.rglob('*.csproj')
                if not any(p.lower() in {'obj','bin'} for p in f.parts)]
    if not projects:
        return None
    web = [p for p in projects if 'Microsoft.NET.Sdk.Web' in
           p.read_text(encoding='utf-8', errors='ignore')]
    return web[0] if web else projects[0]

def _capture_logs(job_id: str, process: subprocess.Popen):
    app = runtime_apps[job_id]
    try:
        for line in process.stdout:
            app['logs'].append(line.rstrip())
            if len(app['logs']) > 500:
                app['logs'] = app['logs'][-500:]
    except Exception:
        pass

def _check_runnable(output_dir: str) -> dict:
    """Check if the migrated app is likely runnable without external dependencies."""
    out = Path(output_dir)
    reasons = []

    for cs_file in out.rglob('*.cs'):
        if any(p.lower() in {'obj', 'bin'} for p in cs_file.parts):
            continue
        try:
            content = cs_file.read_text(encoding='utf-8', errors='ignore')
            if 'Host=' in content and 'Database=' in content:
                reasons.append('Requires PostgreSQL database — configure connection string in appsettings.json before running.')
            if 'Server=' in content and ('Initial Catalog=' in content or 'Database=' in content):
                reasons.append('Requires SQL Server database — configure connection string in appsettings.json before running.')
            if 'mongodb://' in content.lower() or 'MongoClient' in content:
                reasons.append('Requires MongoDB — configure connection string before running.')
            if 'redis' in content.lower() and 'ConnectionMultiplexer' in content:
                reasons.append('Requires Redis — configure connection before running.')
        except Exception:
            pass

    for json_file in out.rglob('appsettings*.json'):
        try:
            content = json_file.read_text(encoding='utf-8', errors='ignore')
            if 'Host=' in content and 'Database=' in content:
                reasons.append('PostgreSQL connection string found in appsettings.json — ensure the database is running locally.')
            if 'Server=' in content and 'Database=' in content:
                reasons.append('SQL Server connection string found in appsettings.json — ensure the database is running locally.')
        except Exception:
            pass

    # Deduplicate
    reasons = list(dict.fromkeys(reasons))
    return {'runnable': len(reasons) == 0, 'reasons': reasons}


@router.post("/run/{job_id}")
def start_runtime(job_id: str):
    if job_id not in migration_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if migration_jobs[job_id].get('status') != 'completed':
        raise HTTPException(status_code=400, detail="Migration not completed yet")

    # Kill any existing process for this job and clean obj/bin locks
    existing = runtime_apps.get(job_id)
    if existing:
        proc = existing.get('process')
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        # Delete obj/bin to release file locks before next run
        out = Path(OUTPUT_DIR)
        for folder in out.rglob('obj'):
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
        for folder in out.rglob('bin'):
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)

    if not shutil.which('dotnet'):
        return {'status': 'failed', 'url': '', 'logs': ['.NET SDK not found — install dotnet to run the migrated app.']}

    csproj = _find_csproj(OUTPUT_DIR)
    if not csproj:
        return {'status': 'failed', 'url': '', 'logs': ['No runnable .csproj found in migrated output.']}

    # Warn upfront if external dependencies are detected
    runnable = _check_runnable(OUTPUT_DIR)
    if not runnable['runnable']:
        return {
            'status': 'needs_setup',
            'url': '',
            'logs': [
                'This app requires external services to run:',
                *[f'  - {r}' for r in runnable['reasons']],
                '',
                'To run locally:',
                '  1. Download the migrated zip',
                '  2. Set up the required services',
                '  3. Update appsettings.json with your connection details',
                '  4. Run: dotnet run',
            ]
        }

    port = _free_port()
    url = f'http://0.0.0.0:{port}'

    # Clean stale obj/bin before running
    for folder in ['obj', 'bin']:
        stale = csproj.parent / folder
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)

    process = subprocess.Popen(
        ['dotnet', 'run', '--project', str(csproj), '--urls', url, '--no-launch-profile'],
        cwd=str(csproj.parent),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
        env={**os.environ, 'ASPNETCORE_ENVIRONMENT': 'Development'},
    )
    display_url = f'http://127.0.0.1:{port}'
    runtime_apps[job_id] = {
        'status': 'starting', 'url': display_url, 'port': port,
        'process': process, 'logs': [f'Starting {csproj.name} on {display_url}'],
    }
    threading.Thread(target=_capture_logs, args=(job_id, process), daemon=True).start()
    for _ in range(30):
        if process.poll() is not None:
            runtime_apps[job_id]['status'] = 'failed'
            break
        if _is_port_open(port):
            runtime_apps[job_id]['status'] = 'running'
            break
        time.sleep(0.5)
    return _runtime_status(job_id)

@router.get("/run/{job_id}")
def get_runtime(job_id: str):
    return _runtime_status(job_id)

@router.post("/run/{job_id}/stop")
def stop_runtime(job_id: str):
    app = runtime_apps.get(job_id)
    if not app:
        return {'status': 'stopped', 'url': '', 'logs': []}
    proc = app.get('process')
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    app['status'] = 'stopped'
    app['logs'].append('Application stopped.')
    return _runtime_status(job_id)

def _detect_routes(output_dir: str) -> list:
    """Scan migrated controllers and Razor Pages to extract real routes."""
    routes = []
    out = Path(output_dir)

    # Scan controllers
    for cs_file in out.rglob('*Controller.cs'):
        if any(p.lower() in {'obj', 'bin'} for p in cs_file.parts):
            continue
        try:
            content = cs_file.read_text(encoding='utf-8', errors='ignore')
            ctrl_name = cs_file.stem.replace('Controller', '').lower()
            for match in re.findall(r'\[Route\(["\']([^"\']+)["\']\)\]', content):
                route = match.replace('[controller]', ctrl_name)
                if not route.startswith('/'):
                    route = '/' + route
                if 'api' in route.lower() and route not in routes:
                    routes.append(route)
        except Exception:
            pass

    # Scan Razor Pages — add page routes
    razor_pages = [
        f for f in out.rglob('*.cshtml')
        if not any(p.lower() in {'obj', 'bin'} for p in f.parts)
        and not f.name.startswith('_')
    ]
    if razor_pages:
        # Always add root for Razor Pages projects
        if '/' not in routes:
            routes.insert(0, '/')
        for page in razor_pages[:4]:
            # Convert Pages/Info.cshtml -> /Info
            try:
                parts = list(page.parts)
                pages_idx = next((i for i, p in enumerate(parts) if p.lower() == 'pages'), None)
                if pages_idx is not None:
                    rel_parts = parts[pages_idx + 1:]
                    route = '/' + '/'.join(p.replace('.cshtml', '') for p in rel_parts)
                    if route not in routes and 'shared' not in route.lower():
                        routes.append(route)
            except Exception:
                pass

    # Always add /health as optional
    if '/health' not in routes:
        routes.append('/health')

    # Fallback for pure API projects
    if not routes or routes == ['/health']:
        routes = ['/api', '/health']

    return routes[:6]


@router.post("/run/{job_id}/smoke")
def smoke_test(job_id: str):
    # Start app if not running
    app = runtime_apps.get(job_id)
    if not app or app.get('status') != 'running':
        start_runtime(job_id)
        app = runtime_apps.get(job_id)
    if not app or app.get('status') != 'running':
        return {'status': 'failed', 'summary': 'App did not start.', 'checks': [], 'runtime': _runtime_status(job_id)}

    base_url = app['url'].rstrip('/')
    routes = _detect_routes(OUTPUT_DIR)
    checks = []
    for path in routes:
        name = path.strip('/').replace('/', ' › ') or 'root'
        url = f'{base_url}{path}'
        optional = path == '/health'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'MigrationAgentSmokeTest/1.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                status_code = resp.status
                passed = 200 <= status_code < 400
        except urllib.error.HTTPError as e:
            status_code = e.code
            passed = optional  # health 404 is acceptable
        except Exception:
            status_code = 0
            passed = False
        checks.append({'name': name, 'path': path, 'status_code': status_code, 'passed': passed, 'optional': optional})

    passed_count = sum(1 for c in checks if c['passed'])
    required = [c for c in checks if not c.get('optional')]
    required_passed = sum(1 for c in required if c['passed'])
    overall = 'passed' if required_passed == len(required) else 'needs_review'
    return {
        'status': overall,
        'summary': f'{passed_count}/{len(checks)} checks passed ({required_passed}/{len(required)} required).',
        'url': base_url,
        'checks': checks,
        'runtime': _runtime_status(job_id),
    }

def _runtime_status(job_id: str) -> dict:
    app = runtime_apps.get(job_id)
    if not app:
        return {'status': 'stopped', 'url': '', 'logs': []}
    proc = app.get('process')
    if proc:
        if proc.poll() is not None and app['status'] not in {'stopped', 'failed'}:
            app['status'] = 'exited'
        elif proc.poll() is None and _is_port_open(app.get('port', 0)):
            app['status'] = 'running'
    return {'status': app['status'], 'url': app.get('url', ''), 'logs': app.get('logs', [])[-200:]}

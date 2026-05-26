from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from typing import List, Optional
from pathlib import PurePosixPath
import aiofiles
import os
from pathlib import Path
import zipfile
import tempfile
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx
import re
import shutil
from middleware.auth import require_user
from database.models import User

router = APIRouter(prefix="/api/files", tags=["files"])

BASE_DIR = Path(__file__).parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"

@router.post("/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(require_user)
):
    if UPLOAD_DIR.exists():
        shutil.rmtree(UPLOAD_DIR)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    uploaded = []
    for file in files:
        content = await file.read()
        if file.filename.endswith('.zip'):
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
                    _safe_extract_zip(zip_ref, UPLOAD_DIR)
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            uploaded.append({"name": file.filename, "type": "zip", "size": len(content)})
        else:
            file_path = UPLOAD_DIR / _sanitize_filename(file.filename)
            async with aiofiles.open(file_path, 'wb') as f:
                await f.write(content)
            uploaded.append({"name": file.filename, "type": "file", "size": len(content)})
    return {"success": True, "files": uploaded, "count": len(uploaded)}

@router.get("/download")
async def download_migrated_project(
    current_user: User = Depends(require_user)
):
    migrated_dir = BASE_DIR / "outputs" / "migrated"
    if not migrated_dir.exists() or not any(migrated_dir.rglob("*")):
        raise HTTPException(status_code=404, detail="No migrated project available — run migration first")
    zip_path = BASE_DIR / "outputs" / "migrated_project.zip"
    skip = {"obj", "bin", ".vs", ".git"}
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path in migrated_dir.rglob('*'):
            if not file_path.is_file():
                continue
            # Skip obj/bin folders
            if any(part.lower() in skip for part in file_path.parts):
                continue
            zipf.write(file_path, file_path.relative_to(migrated_dir))
    return FileResponse(zip_path, media_type='application/zip', filename='migrated_project.zip')

@router.get("/list")
async def list_uploaded_files(
    current_user: User = Depends(require_user)
):
    files = []
    for file_path in UPLOAD_DIR.glob("*"):
        if file_path.is_file():
            files.append({"name": file_path.name, "size": file_path.stat().st_size})
    return {"files": files}

@router.delete("/clear")
async def clear_uploads(
    current_user: User = Depends(require_user)
):
    count = 0
    for file_path in UPLOAD_DIR.glob("*"):
        if file_path.is_file():
            file_path.unlink()
            count += 1
    return {"success": True, "deleted": count}

class GithubRequest(BaseModel):
    url: str
    token: Optional[str] = None

@router.post("/upload-github")
async def upload_from_github(
    request: GithubRequest,
    current_user: User = Depends(require_user)
):
    url = request.url.strip().rstrip("/")
    match = re.match(r"https?://github\.com/([^/]+)/([^/]+)", url)
    if not match:
        raise HTTPException(status_code=400, detail="Invalid GitHub URL. Use https://github.com/owner/repo")
    owner, repo = match.group(1), match.group(2)
    zip_url = None
    token = (request.token or "").strip() if getattr(request, 'token', None) else None
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        headers = {}
        if token:
            # Use GitHub token for authenticated requests (do not log the token)
            headers['Authorization'] = f'token {token}'
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
    if UPLOAD_DIR.exists():
        shutil.rmtree(UPLOAD_DIR)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            tmp.write(zip_bytes)
            tmp_path = tmp.name
        with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
            _safe_extract_zip(zip_ref, UPLOAD_DIR)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
    extracted = [f for f in UPLOAD_DIR.rglob("*") if f.is_file()]
    cs_files = [f for f in extracted if f.suffix in [".cs", ".csproj", ".sln"]]
    return {"success": True, "repo": f"{owner}/{repo}", "branch": branch, "total_files": len(extracted), "cs_files": len(cs_files)}


def _sanitize_filename(filename: str) -> str:
    safe_name = Path(filename).name
    if not safe_name or safe_name in {'.', '..'}:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if '/' in safe_name or '\\' in safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return safe_name


def _is_within_directory(directory: Path, target: Path) -> bool:
    try:
        return directory.resolve() == target.resolve() or directory.resolve() in target.resolve().parents
    except RuntimeError:
        return False


def _safe_extract_zip(zip_ref: zipfile.ZipFile, target_dir: Path):
    target_dir = target_dir.resolve()
    for member in zip_ref.infolist():
        member_name = member.filename
        if member_name.startswith(('/', '\\')) or '..' in member_name or '\\' in member_name:
            raise HTTPException(status_code=400, detail="Archive contains invalid file paths")
        dest_path = target_dir.joinpath(Path(*PurePosixPath(member_name).parts))
        if not _is_within_directory(target_dir, dest_path):
            raise HTTPException(status_code=400, detail="Archive contains invalid file paths")
        if member.is_dir():
            dest_path.mkdir(parents=True, exist_ok=True)
            continue
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with zip_ref.open(member) as source, open(dest_path, 'wb') as target:
            shutil.copyfileobj(source, target)

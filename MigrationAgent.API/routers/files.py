from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import List
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

router = APIRouter(prefix="/api/files", tags=["files"])

BASE_DIR = Path(__file__).parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"

@router.post("/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    if UPLOAD_DIR.exists():
        shutil.rmtree(UPLOAD_DIR)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    uploaded = []
    for file in files:
        content = await file.read()
        if file.filename.endswith('.zip'):
            with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
                zip_ref.extractall(UPLOAD_DIR)
            os.unlink(tmp_path)
            uploaded.append({"name": file.filename, "type": "zip", "size": len(content)})
        else:
            file_path = UPLOAD_DIR / file.filename
            async with aiofiles.open(file_path, 'wb') as f:
                await f.write(content)
            uploaded.append({"name": file.filename, "type": "file", "size": len(content)})
    return {"success": True, "files": uploaded, "count": len(uploaded)}

@router.get("/download")
async def download_migrated_project():
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
async def list_uploaded_files():
    files = []
    for file_path in UPLOAD_DIR.glob("*"):
        if file_path.is_file():
            files.append({"name": file_path.name, "size": file_path.stat().st_size})
    return {"files": files}

@router.delete("/clear")
async def clear_uploads():
    count = 0
    for file_path in UPLOAD_DIR.glob("*"):
        if file_path.is_file():
            file_path.unlink()
            count += 1
    return {"success": True, "deleted": count}

class GithubRequest(BaseModel):
    url: str

@router.post("/upload-github")
async def upload_from_github(request: GithubRequest):
    url = request.url.strip().rstrip("/")
    match = re.match(r"https?://github\.com/([^/]+)/([^/]+)", url)
    if not match:
        raise HTTPException(status_code=400, detail="Invalid GitHub URL. Use https://github.com/owner/repo")
    owner, repo = match.group(1), match.group(2)
    zip_url = None
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        for branch in ["main", "master"]:
            candidate = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
            resp = await client.head(candidate)
            if resp.status_code == 200:
                zip_url = candidate
                break
        if not zip_url:
            raise HTTPException(status_code=404, detail="Could not find main or master branch on GitHub")
        resp = await client.get(zip_url)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to download repository from GitHub")
        zip_bytes = resp.content
    if UPLOAD_DIR.exists():
        shutil.rmtree(UPLOAD_DIR)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        tmp.write(zip_bytes)
        tmp_path = tmp.name
    with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
        zip_ref.extractall(UPLOAD_DIR)
    os.unlink(tmp_path)
    extracted = [f for f in UPLOAD_DIR.rglob("*") if f.is_file()]
    cs_files = [f for f in extracted if f.suffix in [".cs", ".csproj", ".sln"]]
    return {"success": True, "repo": f"{owner}/{repo}", "branch": branch, "total_files": len(extracted), "cs_files": len(cs_files)}

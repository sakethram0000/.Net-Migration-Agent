# Load .env FIRST before importing any modules that need environment variables
import os
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from routers import files, ollama_router, migration
from contextlib import asynccontextmanager
import shutil

BASE_DIR = Path(__file__).parent
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure folders exist on startup
    for folder in [BASE_DIR / "uploads", BASE_DIR / "outputs"]:
        folder.mkdir(parents=True, exist_ok=True)
    yield

app = FastAPI(title="Migration Agent API", version="1.0.0", lifespan=lifespan)

allowed_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if not allowed_origins:
    allowed_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

app.include_router(files.router)
app.include_router(ollama_router.router)
app.include_router(migration.router)

@app.get("/health")
def health():
    return {"status": "healthy", "runtime": None}

# Serve React static assets if the build exists
if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

@app.get("/")
def index():
    index_file = FRONTEND_DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"status": "Migration Agent API is running — build the frontend with: npm run build"}

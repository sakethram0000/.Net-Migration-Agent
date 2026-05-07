from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
for package_dir in [root / ".python_packages", root / ".python_runtime"]:
    if package_dir.exists():
        sys.path.insert(0, str(package_dir))

import uvicorn


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8050"))
    host = "0.0.0.0" if os.getenv("RENDER") or os.getenv("PORT") else "127.0.0.1"
    uvicorn.run("backend.api:app", host=host, port=port)

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .tools import clean_source_files, upgrade_project_files


class RuntimeManager:
    def __init__(self) -> None:
        self.apps: dict[str, dict[str, Any]] = {}

    def start(self, job_id: str, output_dir: str) -> dict[str, Any]:
        existing = self.apps.get(job_id)
        if existing and existing.get("process") and existing["process"].poll() is None:
            return self.status(job_id)

        output = Path(output_dir)
        repair_logs = prepare_runnable_output(output)
        project = find_runnable_project(output)
        if not project:
            self.apps[job_id] = {"status": "failed", "logs": ["No runnable .csproj found in migrated output."], "url": ""}
            return self.status(job_id)

        port = free_port()
        url = f"http://127.0.0.1:{port}"
        logs: list[str] = repair_logs + [f"Starting {project.name} on {url}"]
        process = subprocess.Popen(
            ["dotnet", "run", "--project", str(project), "--urls", url, "--no-launch-profile"],
            cwd=str(project.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.apps[job_id] = {
            "status": "starting",
            "project": str(project),
            "url": url,
            "port": port,
            "process": process,
            "logs": logs,
            "started_at": time.time(),
        }
        threading.Thread(target=self._capture_logs, args=(job_id, process), daemon=True).start()
        self._wait_for_port(job_id, port)
        return self.status(job_id)

    def stop(self, job_id: str) -> dict[str, Any]:
        app = self.apps.get(job_id)
        if not app:
            return {"status": "stopped", "url": "", "logs": ["Runtime was not started."]}
        process = app.get("process")
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        app["status"] = "stopped"
        app.setdefault("logs", []).append("Application stopped.")
        return self.status(job_id)

    def status(self, job_id: str) -> dict[str, Any]:
        app = self.apps.get(job_id)
        if not app:
            return {"status": "stopped", "url": "", "logs": []}
        process = app.get("process")
        if process and process.poll() is None and app.get("url") and is_port_open(int(app.get("port"))):
            app["status"] = "running"
        elif process and process.poll() is None and app.get("status") == "running" and not is_port_open(int(app.get("port"))):
            app["status"] = "starting"
        elif process and process.poll() is not None and app.get("status") not in {"stopped", "failed"}:
            app["status"] = "exited"
            app.setdefault("logs", []).append(f"Application exited with code {process.returncode}.")
        return {
            "status": app.get("status", "unknown"),
            "url": app.get("url", ""),
            "project": app.get("project", ""),
            "logs": app.get("logs", [])[-300:],
        }

    def _capture_logs(self, job_id: str, process: subprocess.Popen) -> None:
        app = self.apps[job_id]
        try:
            assert process.stdout is not None
            for line in process.stdout:
                app.setdefault("logs", []).append(line.rstrip())
                if len(app["logs"]) > 500:
                    app["logs"] = app["logs"][-500:]
        except Exception as exc:
            app.setdefault("logs", []).append(f"Log capture failed: {exc}")

    def _wait_for_port(self, job_id: str, port: int) -> None:
        app = self.apps[job_id]
        for _ in range(30):
            process = app.get("process")
            if process and process.poll() is not None:
                app["status"] = "exited"
                app.setdefault("logs", []).append(f"Application exited before opening port {port}.")
                return
            if is_port_open(port):
                app["status"] = "running"
                app.setdefault("logs", []).append(f"Application is listening on {app.get('url')}.")
                return
            time.sleep(0.5)
        app["status"] = "starting"
        app.setdefault("logs", []).append(f"Application has not opened port {port} yet. Refresh logs to continue checking.")


def find_runnable_project(output: Path) -> Path | None:
    projects = list(output.rglob("*.csproj"))
    if not projects:
        return None
    web_projects = []
    for project in projects:
        text = project.read_text(encoding="utf-8", errors="ignore")
        if "Microsoft.NET.Sdk.Web" in text or "Microsoft.AspNetCore" in text:
            web_projects.append(project)
    return web_projects[0] if web_projects else projects[0]


def prepare_runnable_output(output: Path) -> list[str]:
    logs: list[str] = []
    try:
        changes = upgrade_project_files(output, "net8.0")
        changes.extend(clean_source_files(output))
        if changes:
            logs.append("Prepared migrated output for local run:")
            logs.extend(f"- {change}" for change in changes[:20])
            if len(changes) > 20:
                logs.append(f"- {len(changes) - 20} more cleanup changes")
    except Exception as exc:
        logs.append(f"Runtime preparation warning: {exc}")
    return logs


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def is_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False

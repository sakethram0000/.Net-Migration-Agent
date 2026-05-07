from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from . import llm
from .inventory import inventory, interesting_files
from .microsoft_agent_framework import MicrosoftAgentFrameworkAdapter
from .tools import architecture_suggestions, build_error_fixer, build_output, code_rewrite_previews, clean_source_files, copy_source_to_output, dependency_modernization, executive_report, generated_test_plan, migration_diff, readiness_scorecard, upgrade_project_files, write_report, zip_output


class MigrationOrchestrator:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.uploads = base_dir / "uploads"
        self.workspaces = base_dir / "workspaces"
        self.outputs = base_dir / "outputs"
        self.jobs: dict[str, dict[str, Any]] = {}
        self.framework = MicrosoftAgentFrameworkAdapter()
        self.uploads.mkdir(exist_ok=True)
        self.workspaces.mkdir(exist_ok=True)
        self.outputs.mkdir(exist_ok=True)

    def runtime(self) -> dict[str, Any]:
        return {"agent_framework": self.framework.status(), "llm": llm.runtime_status(), "planned_agents": self.framework.planned_agents()}

    def new_job(self, source_dir: Path, from_version: str, to_version: str, scopes: dict[str, bool] | None = None) -> str:
        job_id = str(uuid.uuid4())
        workspace = self.workspaces / job_id / "source"
        output = self.workspaces / job_id / "migrated"
        if workspace.exists():
            shutil.rmtree(workspace)
        shutil.copytree(source_dir, workspace, ignore=shutil.ignore_patterns(".git", "bin", "obj", "packages", "node_modules"))
        self.jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "progress": "Migration queued",
            "from_version": from_version,
            "to_version": to_version,
            "scopes": scopes or {},
            "workspace": str(workspace),
            "output": str(output),
            "created_at": time.time(),
            "steps": [],
        }
        return job_id

    def analyze_current_upload(self, from_version: str, to_version: str) -> dict[str, Any]:
        return inventory(self.uploads, from_version, to_version)

    def run(self, job_id: str) -> None:
        job = self.jobs[job_id]
        source = Path(job["workspace"])
        output = Path(job["output"])
        from_version = job["from_version"]
        to_version = job["to_version"]
        target_framework = target_framework_for(to_version)
        try:
            self._step(job, "ingest", "Preparing isolated migration workspace")
            copy_source_to_output(source, output)

            self._step(job, "inventory", "Scanning solution, projects, packages, and blockers")
            inv = inventory(source, from_version, to_version)
            job["inventory"] = inv

            self._step(job, "upgrade-projects", f"Upgrading project files to {target_framework}")
            changes = upgrade_project_files(output, target_framework)

            self._step(job, "rewrite-code", "Applying deterministic cleanup and optional LLM rewrites")
            changes.extend(clean_source_files(output))
            if llm.configured():
                changes.extend(self._llm_rewrite(output, from_version, to_version, inv))

            self._step(job, "validate", "Running dotnet restore/build validation")
            validation = build_output(output)
            job["validation"] = validation
            manual_fixes = []
            if not validation.get("success"):
                manual_fixes = llm.explain_build_errors(str(validation.get("errors") or validation.get("output") or validation.get("error") or ""), inv)

            self._step(job, "package", "Generating report and downloadable zip")
            readiness = readiness_scorecard(inv, validation)
            diff = migration_diff(source, output)
            build_fixer = build_error_fixer(validation, inv)
            report = {
                "job_id": job_id,
                "from_version": from_version,
                "to_version": to_version,
                "inventory": inv,
                "readiness": readiness,
                "diff": diff,
                "code_rewrite_previews": code_rewrite_previews(source, output),
                "build_fixer": build_fixer,
                "dependency_modernization": dependency_modernization(inv),
                "architecture_suggestions": architecture_suggestions(inv),
                "generated_tests": generated_test_plan(output),
                "changes": changes,
                "validation": validation,
                "manual_fixes": manual_fixes,
                "runtime": self.runtime(),
            }
            report["executive_report"] = executive_report(report)
            write_report(output, report)
            zip_path = self.outputs / f"{job_id}-migrated-project.zip"
            zip_output(output, zip_path)
            job["report"] = report
            job["download_path"] = str(zip_path)
            job["status"] = "completed" if validation.get("success") else "needs_review"
            job["stage"] = "completed" if validation.get("success") else "needs-review"
            job["progress"] = "Migration completed and build passed" if validation.get("success") else "Migration completed; build/manual fixes need review"
        except Exception as exc:
            job["status"] = "failed"
            job["stage"] = "failed"
            job["progress"] = f"Migration failed: {exc}"
            job["error"] = str(exc)

    def _step(self, job: dict[str, Any], stage: str, progress: str) -> None:
        job["status"] = "running"
        job["stage"] = stage
        job["progress"] = progress
        job["steps"].append({"stage": stage, "message": progress, "at": time.time()})

    def _llm_rewrite(self, output: Path, from_version: str, to_version: str, inv: dict[str, Any]) -> list[str]:
        changes = []
        for file in interesting_files(output):
            if file.suffix.lower() not in {".cs", ".csproj", ".cshtml", ".razor"}:
                continue
            content = file.read_text(encoding="utf-8", errors="ignore")
            migrated = llm.rewrite_code(str(file.relative_to(output)), content, from_version, to_version, inv)
            if migrated and migrated.strip() and migrated.strip() != content.strip():
                file.write_text(migrated, encoding="utf-8")
                changes.append(f"LLM rewrite applied to {file.relative_to(output)}")
        return changes


def target_framework_for(to_version: str) -> str:
    if "10" in to_version:
        return "net10.0"
    if "9" in to_version:
        return "net9.0"
    if "8" in to_version:
        return "net8.0"
    if "7" in to_version:
        return "net7.0"
    return "net8.0"

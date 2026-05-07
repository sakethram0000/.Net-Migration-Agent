from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


IGNORED_DIRS = {".git", ".vs", "bin", "obj", "packages", "node_modules", ".idea", ".vscode"}
CODE_EXTS = {".cs", ".csproj", ".sln", ".config", ".json", ".cshtml", ".razor"}


def interesting_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in CODE_EXTS:
            files.append(path)
    return sorted(files)


def inventory(root: Path, from_version: str, to_version: str) -> dict[str, Any]:
    files = interesting_files(root)
    csproj_files = [path for path in files if path.suffix.lower() == ".csproj"]
    sln_files = [path for path in files if path.suffix.lower() == ".sln"]
    cs_files = [path for path in files if path.suffix.lower() == ".cs"]
    projects = [inspect_csproj(path, root) for path in csproj_files]
    packages = sorted({pkg["name"] for project in projects for pkg in project["packages"]})
    frameworks = sorted({project["target_framework"] for project in projects if project["target_framework"]})
    patterns = detect_patterns(files)
    complexity = complexity_score(projects, cs_files, patterns, from_version, to_version)
    return {
        "from_version": from_version,
        "to_version": to_version,
        "solution_files": [relative(path, root) for path in sln_files],
        "project_count": len(projects),
        "source_file_count": len(cs_files),
        "total_file_count": len(files),
        "projects": projects,
        "frameworks": frameworks,
        "packages": packages,
        "patterns": patterns,
        "complexity": complexity,
        "recommended_path": recommended_path(from_version, to_version, complexity),
    }


def inspect_csproj(path: Path, root: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8", errors="ignore")
    packages = []
    project_refs = []
    target_framework = ""
    try:
        xml_root = ET.fromstring(content)
        for elem in xml_root.iter():
            tag = elem.tag.split("}", 1)[-1]
            if tag in {"TargetFramework", "TargetFrameworks"} and elem.text:
                target_framework = elem.text.strip()
            if tag == "PackageReference":
                packages.append({"name": elem.get("Include", ""), "version": elem.get("Version", "")})
            if tag == "ProjectReference":
                project_refs.append(elem.get("Include", ""))
    except Exception:
        target_match = re.search(r"<TargetFrameworks?>(.*?)</TargetFrameworks?>", content, re.I | re.S)
        target_framework = target_match.group(1).strip() if target_match else ""
        packages = [
            {"name": match.group(1), "version": match.group(2) or ""}
            for match in re.finditer(r'<PackageReference\s+Include="([^"]+)"(?:\s+Version="([^"]+)")?', content, re.I)
        ]
    return {
        "path": relative(path, root),
        "sdk_style": "<Project Sdk=" in content,
        "target_framework": target_framework,
        "packages": packages,
        "project_references": project_refs,
        "is_web": "Microsoft.NET.Sdk.Web" in content or any(pkg["name"].startswith("Microsoft.AspNetCore") for pkg in packages),
    }


def detect_patterns(files: list[Path]) -> list[dict[str, str]]:
    checks = [
        ("Startup.cs", "Startup class", "Convert to minimal hosting or modern Program.cs"),
        ("packages.config", "packages.config", "Migrate packages.config to PackageReference"),
        ("web.config", "web.config", "Review IIS/system.web settings for ASP.NET Core hosting"),
        ("Global.asax", "Global.asax", "Move application startup hooks to ASP.NET Core pipeline"),
    ]
    found: list[dict[str, str]] = []
    names = {path.name.lower(): path for path in files}
    for filename, title, action in checks:
        path = names.get(filename.lower())
        if path:
            found.append({"title": title, "path": str(path), "action": action, "severity": "High" if filename in {"packages.config", "Global.asax"} else "Medium"})
    for path in files:
        if path.suffix.lower() != ".cs":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")[:12000]
        if "System.Web" in text:
            found.append({"title": "System.Web usage", "path": str(path), "action": "Replace with ASP.NET Core abstractions", "severity": "High"})
        if "ConfigurationManager" in text:
            found.append({"title": "ConfigurationManager usage", "path": str(path), "action": "Move settings to IConfiguration/options pattern", "severity": "Medium"})
    return found[:30]


def complexity_score(projects: list[dict[str, Any]], cs_files: list[Path], patterns: list[dict[str, str]], from_version: str, to_version: str) -> dict[str, Any]:
    points = len(projects) * 8 + len(cs_files) // 4 + len(patterns) * 10
    if "Framework" in from_version:
        points += 25
    if "10" in to_version or "9" in to_version:
        points += 5
    level = "High" if points >= 70 else "Medium" if points >= 35 else "Low"
    return {"score": min(points, 100), "level": level}


def recommended_path(from_version: str, to_version: str, complexity: dict[str, Any]) -> str:
    if "Framework" in from_version and complexity["level"] == "High":
        return f"Use staged migration: compile on .NET Framework first, port projects to SDK style, then move to {to_version} with build-fix iterations."
    return f"Direct migration to {to_version} is reasonable with restore/build/test validation gates."


def relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")

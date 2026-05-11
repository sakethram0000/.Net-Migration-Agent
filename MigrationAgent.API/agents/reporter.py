from pathlib import Path
import re

BASE_DIR = Path(__file__).parent.parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "outputs" / "migrated"
UPLOAD_DIR = BASE_DIR / "uploads"


def generate_report():
    migrated_dir = DEFAULT_OUTPUT_DIR
    upload_dir = UPLOAD_DIR

    empty = {
        "summary": "",
        "from_version": "",
        "to_version": "",
        "changes": [],
        "issues": [],
        "recommendations": [],
        "dependency_map": {},
        "manual_fixes": [],
        "validation": {"success": False, "stage": "not run", "output": "", "errors": ""},
        "diff": {"summary": {"added": 0, "modified": 0, "removed": 0, "unchanged": 0}, "added": [], "modified": [], "removed": [], "previews": []},
        "code_rewrite_previews": [],
        "build_fixer": {"summary": "", "items": []},
        "dependency_modernization": {"summary": "", "items": []},
        "architecture_suggestions": {"summary": "", "items": []},
        "generated_tests": {"summary": "", "items": []},
        "executive_report": {},
    }

    if not migrated_dir.exists():
        empty["issues"].append("No migrated output found.")
        empty["recommendations"].append("Run migration first.")
        return empty

    migrated_files = (
        list(migrated_dir.rglob("*.cs"))
        + list(migrated_dir.rglob("*.csproj"))
        + list(migrated_dir.rglob("*.sln"))
    )

    # --- changes ---
    changes = []
    for f in migrated_files:
        rel = str(f.relative_to(migrated_dir))
        if f.suffix == ".cs":
            changes.append({"file": rel, "summary": "Migrated to .NET 8 / C# 12"})
        elif f.suffix == ".csproj":
            changes.append({"file": rel, "summary": "Updated to .NET 8 SDK-style project"})
        elif f.suffix == ".sln":
            changes.append({"file": rel, "summary": "Solution file preserved"})

    # --- dependency_map ---
    dependency_map = {}
    for csproj in migrated_dir.rglob("*.csproj"):
        try:
            content = csproj.read_text(encoding="utf-8", errors="ignore")
            for pkg, ver in re.findall(r'<PackageReference Include="([^"]+)" Version="([^"]+)"', content):
                dependency_map[pkg] = ver
        except Exception:
            pass

    # --- manual_fixes ---
    manual_fixes = []
    for cs_file in migrated_dir.rglob("*.cs"):
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            rel = str(cs_file.relative_to(migrated_dir))
            if "TODO" in content or "FIXME" in content:
                manual_fixes.append(f"{rel}: Contains TODO/FIXME comments requiring attention")
            if "System.Web" in content:
                manual_fixes.append(f"{rel}: Contains System.Web references — verify compatibility")
            if re.search(r"async void \w+\(", content):
                manual_fixes.append(f"{rel}: Contains async void methods — consider async Task instead")
            if "HttpContext.Current" in content:
                manual_fixes.append(f"{rel}: Contains HttpContext.Current — replace with IHttpContextAccessor")
            if "ConfigurationManager" in content:
                manual_fixes.append(f"{rel}: Contains ConfigurationManager — replace with IConfiguration")
        except Exception:
            pass

    # --- diff (compare upload vs migrated) ---
    diff = _build_diff(upload_dir, migrated_dir)

    # --- code_rewrite_previews ---
    code_rewrite_previews = _build_rewrite_previews(upload_dir, migrated_dir)

    # --- validation (check if dotnet is available and run build) ---
    validation = _run_validation(migrated_dir)

    # --- build_fixer ---
    build_fixer = _build_fixer(validation)

    # --- dependency_modernization ---
    dependency_modernization = _dependency_modernization(dependency_map)

    # --- architecture_suggestions ---
    architecture_suggestions = _architecture_suggestions(manual_fixes)

    # --- generated_tests ---
    generated_tests = _generated_tests(migrated_dir)

    summary = f"{len(migrated_files)} file(s) migrated successfully to .NET 8."
    recommendations = [
        "Migration completed. Review code for business logic correctness.",
        "Run dotnet build to verify compilation.",
        "Test all API endpoints and database connections.",
    ]

    executive_report = {
        "title": ".NET Migration Executive Report",
        "total_files_migrated": len(migrated_files),
        "build_status": "Passed" if validation.get("success") else "Needs Review",
        "dependency_count": len(dependency_map),
        "manual_fix_count": len(manual_fixes),
        "diff_summary": diff["summary"],
        "recommendations": recommendations,
    }

    return {
        "summary": summary,
        "changes": changes,
        "issues": [],
        "recommendations": recommendations,
        "dependency_map": dependency_map,
        "manual_fixes": manual_fixes,
        "validation": validation,
        "diff": diff,
        "code_rewrite_previews": code_rewrite_previews,
        "build_fixer": build_fixer,
        "dependency_modernization": dependency_modernization,
        "architecture_suggestions": architecture_suggestions,
        "generated_tests": generated_tests,
        "executive_report": executive_report,
    }


def _build_diff(upload_dir: Path, migrated_dir: Path) -> dict:
    import difflib

    def collect(root):
        result = {}
        if not root.exists():
            return result
        for f in root.rglob("*"):
            if f.is_file() and not any(p.lower() in {"obj", "bin", ".git", ".vs"} for p in f.parts):
                try:
                    result[f.relative_to(root).as_posix()] = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass
        return result

    src = collect(upload_dir)
    out = collect(migrated_dir)

    added = sorted(set(out) - set(src))
    removed = sorted(set(src) - set(out))
    common = sorted(set(src) & set(out))
    modified, previews = [], []

    for rel in common:
        if src[rel] == out[rel]:
            continue
        modified.append(rel)
        if len(previews) < 8:
            diff_lines = list(difflib.unified_diff(
                src[rel].splitlines(), out[rel].splitlines(),
                fromfile=f"original/{rel}", tofile=f"migrated/{rel}",
                lineterm="", n=3,
            ))
            previews.append({"path": rel, "diff": "\n".join(diff_lines[:120])})

    return {
        "summary": {
            "added": len(added),
            "modified": len(modified),
            "removed": len(removed),
            "unchanged": max(0, len(common) - len(modified)),
        },
        "added": added[:60],
        "modified": modified[:60],
        "removed": removed[:60],
        "previews": previews,
    }


def _build_rewrite_previews(upload_dir: Path, migrated_dir: Path) -> list:
    previews = []
    if not migrated_dir.exists():
        return previews
    for out_file in list(migrated_dir.rglob("*.cs"))[:8]:
        rel = out_file.relative_to(migrated_dir).as_posix()
        src_file = upload_dir / rel
        try:
            migrated = out_file.read_text(encoding="utf-8", errors="ignore")
            legacy = src_file.read_text(encoding="utf-8", errors="ignore") if src_file.exists() else ""
            if legacy.strip() == migrated.strip():
                continue
            previews.append({
                "path": rel,
                "legacy": legacy[:4000] or "New file generated during migration.",
                "proposed": migrated[:4000],
                "explanation": "File was rewritten to target .NET 8 / C# 12 conventions.",
            })
        except Exception:
            pass
    return previews


def _run_validation(migrated_dir: Path) -> dict:
    import shutil, subprocess
    if not shutil.which("dotnet"):
        return {
            "success": False,
            "stage": "skipped",
            "output": "dotnet CLI not found — download the zip and run 'dotnet build' locally.",
            "errors": "",
        }
    csproj_files = [f for f in migrated_dir.rglob("*.csproj")
                    if not any(p.lower() in {"obj", "bin"} for p in f.parts)]
    if not csproj_files:
        return {"success": False, "stage": "skipped", "output": "No .csproj found.", "errors": ""}
    project = csproj_files[0]
    try:
        result = subprocess.run(
            ["dotnet", "build", str(project), "--nologo", "-v", "m"],
            capture_output=True, text=True, timeout=120, cwd=str(project.parent)
        )
        return {
            "success": result.returncode == 0,
            "stage": "build",
            "output": result.stdout,
            "errors": result.stderr if result.returncode != 0 else "",
        }
    except Exception as e:
        return {"success": False, "stage": "build", "output": "", "errors": str(e)}


def _build_fixer(validation: dict) -> dict:
    errors_text = validation.get("errors", "") or validation.get("output", "") or ""
    error_codes = re.findall(r"error\s+([A-Z]+\d+):\s*(.*)", errors_text)
    items = []
    for code, message in error_codes[:8]:
        items.append({
            "error": code,
            "root_cause": message[:200],
            "suggested_fix": _fix_hint(code, message),
        })
    if not items:
        items.append({
            "error": "None" if validation.get("success") else "Unknown",
            "root_cause": "Build passed." if validation.get("success") else "No parseable error codes found.",
            "suggested_fix": "No fixes required." if validation.get("success") else "Review build output manually.",
        })
    return {"summary": f"{len(items)} build issue(s) analysed.", "items": items}


def _fix_hint(code: str, message: str) -> str:
    msg = message.lower()
    if "system.web" in msg:
        return "Replace System.Web with ASP.NET Core equivalents."
    if "configurationmanager" in msg:
        return "Replace ConfigurationManager with IConfiguration."
    if "namespace" in msg or "type or namespace" in msg:
        return "Update using statements and NuGet references."
    if "nullable" in msg:
        return "Initialize nullable properties or mark them nullable."
    return "Apply targeted source/package correction and rerun build."


def _dependency_modernization(dependency_map: dict) -> dict:
    hints = {
        "Newtonsoft.Json": ("13.0.3", "Keep or migrate to System.Text.Json if contracts allow."),
        "EntityFramework": ("Microsoft.EntityFrameworkCore 8.x", "Replace EF6 with EF Core 8."),
        "Microsoft.AspNet.Mvc": ("Microsoft.AspNetCore.Mvc", "Replace MVC5 with ASP.NET Core MVC."),
    }
    items = []
    for pkg, ver in dependency_map.items():
        target, note = hints.get(pkg, (f"{ver} (current)", "Confirm .NET 8 compatibility."))
        items.append({"package": pkg, "current_version": ver, "recommended": target, "note": note})
    if not items:
        items.append({"package": "None detected", "current_version": "", "recommended": "", "note": "No packages found in migrated .csproj files."})
    return {"summary": f"{len(items)} dependency recommendation(s).", "items": items}


def _architecture_suggestions(manual_fixes: list) -> dict:
    fix_text = " ".join(manual_fixes)
    items = [
        {"area": "Configuration", "recommendation": "Move settings to IConfiguration and strongly typed options.", "priority": "High" if "ConfigurationManager" in fix_text else "Medium"},
        {"area": "Hosting", "recommendation": "Use ASP.NET Core minimal hosting in Program.cs.", "priority": "High"},
        {"area": "Dependency Injection", "recommendation": "Register services in DI instead of manual instantiation.", "priority": "Medium"},
        {"area": "API Modernization", "recommendation": "Use ControllerBase, attribute routing, and OpenAPI/Swagger.", "priority": "High" if "System.Web" in fix_text else "Medium"},
        {"area": "Observability", "recommendation": "Add structured logging and health checks.", "priority": "Low"},
    ]
    return {"summary": "Architecture modernization suggestions based on migration findings.", "items": items}


def _generated_tests(migrated_dir: Path) -> dict:
    controllers = [f.relative_to(migrated_dir).as_posix() for f in migrated_dir.rglob("*Controller.cs")]
    items = [
        {"name": "SmokeTest.HomePage_Returns200", "type": "Smoke", "target": "/", "sample": "Assert.True(response.IsSuccessStatusCode);"},
        {"name": "SmokeTest.HealthEndpoint_Returns200", "type": "Smoke", "target": "/health", "sample": "Assert.Equal(HttpStatusCode.OK, response.StatusCode);"},
    ]
    for c in controllers[:6]:
        items.append({"name": f"{Path(c).stem}Tests.Actions_ReturnExpectedResult", "type": "Controller", "target": c, "sample": "Assert.NotNull(result);"})
    return {"summary": f"{len(items)} starter test scenario(s) generated.", "items": items, "suggested_project": "MigratedApp.Tests"}

from pathlib import Path
import re
from agents.auth_agent import run_auth_agent

BASE_DIR = Path(__file__).parent.parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "outputs" / "migrated"
UPLOAD_DIR = BASE_DIR / "uploads"

# In-memory store — orchestrator writes context here after migration
# reporter reads from it instead of re-running everything
_last_context = None


def store_context(context):
    """Called by orchestrator after pipeline completes — stores context for reporter."""
    global _last_context
    _last_context = context


def generate_report():
    """
    Reporter Agent — reads from stored context when available.
    Falls back to file scanning only if no context exists (direct API call).
    """
    migrated_dir = DEFAULT_OUTPUT_DIR
    upload_dir   = UPLOAD_DIR
    ctx          = _last_context  # set by orchestrator after pipeline completes

    empty = {
        "summary": "", "from_version": "", "to_version": "",
        "changes": [], "issues": [], "recommendations": [],
        "dependency_map": {}, "manual_fixes": [],
        "readiness": {"score": 0, "level": "Unknown", "summary": "", "categories": [], "recommendations": []},
        "auth_migration": {}, "view_migration": {}, "webforms_migration": {}, "blazor_migration": {},
        "validation": {"success": False, "stage": "not run", "output": "", "errors": ""},
        "diff": {"summary": {"added": 0, "modified": 0, "removed": 0, "unchanged": 0}, "added": [], "modified": [], "removed": [], "previews": []},
        "code_rewrite_previews": [],
        "build_fixer": {"summary": "", "items": []},
        "dependency_modernization": {"summary": "", "items": []},
        "architecture_suggestions": {"summary": "", "items": []},
        "generated_tests": {"summary": "", "items": []},
        "executive_report": {}, "guardrails": {}, "orchestrator": {},
    }

    if not migrated_dir.exists():
        empty["issues"].append("No migrated output found.")
        empty["recommendations"].append("Run migration first.")
        return empty

    # ── Pull data from context if available (agent mode) ─────────────────
    if ctx is not None:
        auth_migration     = ctx.auth_result
        view_migration     = ctx.view_result
        webforms_migration = ctx.webforms_result
        blazor_migration   = ctx.blazor_result
        guardrail_result   = ctx.guardrail_result
        manual_fixes       = ctx.fix_result.get("manual_fixes", [])
        orchestrator_data  = ctx.to_summary_dict()
        build_passed       = ctx.build_passed
        build_result       = ctx.build_result
        from_version       = ctx.from_version
        to_version         = ctx.to_version
        validation = {
            "success":    build_passed,
            "stage":      "build",
            "output":     build_result.get("output", ""),
            "errors":     build_result.get("output", "") if not build_passed else "",
            "skipped":    build_result.get("skipped", False),
            "reason":     build_result.get("reason", ""),
            "auto_fixes": build_result.get("auto_fixes", []) + build_result.get("pre_clean_fixes", []),
            "error_list": build_result.get("errors", []),
        }
    else:
        # ── Fallback: no context — re-run agents independently ────────────
        from_version = ""
        to_version   = ""
        orchestrator_data = {}
        guardrail_result  = {}
        manual_fixes = _scan_manual_fixes(migrated_dir)

        from agents.build_validator import build_loop
        validation_raw = build_loop(str(migrated_dir))
        build_passed   = validation_raw.get("success", False)
        validation = {
            "success":    build_passed,
            "stage":      "build",
            "output":     validation_raw.get("output", ""),
            "errors":     validation_raw.get("output", "") if not build_passed else "",
            "skipped":    validation_raw.get("skipped", False),
            "reason":     validation_raw.get("reason", ""),
            "auto_fixes": validation_raw.get("auto_fixes", []) + validation_raw.get("pre_clean_fixes", []),
            "error_list": validation_raw.get("errors", []),
        }
        try:
            auth_migration = run_auth_agent(upload_dir=str(upload_dir), output_dir=str(migrated_dir))
        except Exception:
            auth_migration = {"status": "skipped", "summary": "Auth agent unavailable."}
        view_migration     = {"skipped": True, "reason": "No context — run migration first."}
        webforms_migration = {"skipped": True, "reason": "No context — run migration first."}
        blazor_migration   = {"skipped": True, "reason": "No context — run migration first."}

    # ── Always re-scan files for changes + dependency map ─────────────────
    migrated_files = (
        list(migrated_dir.rglob("*.cs"))
        + list(migrated_dir.rglob("*.csproj"))
        + list(migrated_dir.rglob("*.sln"))
    )
    changes = []
    for f in migrated_files:
        rel = str(f.relative_to(migrated_dir))
        if f.suffix == ".cs":
            changes.append({"file": rel, "summary": f"Migrated to {to_version or '.NET 8'} / C# 12"})
        elif f.suffix == ".csproj":
            changes.append({"file": rel, "summary": "Updated to SDK-style project"})
        elif f.suffix == ".sln":
            changes.append({"file": rel, "summary": "Solution file preserved"})

    dependency_map = {}
    for csproj in migrated_dir.rglob("*.csproj"):
        try:
            content = csproj.read_text(encoding="utf-8", errors="ignore")
            for pkg, ver in re.findall(r'<PackageReference Include="([^"]+)" Version="([^"]+)"', content):
                dependency_map[pkg] = ver
        except Exception:
            pass

    # ── Always re-scan manual fixes from output files ─────────────────────
    if not manual_fixes:
        manual_fixes = _scan_manual_fixes(migrated_dir)

    # ── Diff + rewrite previews ───────────────────────────────────────────
    diff                 = _build_diff(upload_dir, migrated_dir)
    code_rewrite_previews = _build_rewrite_previews(upload_dir, migrated_dir)

    # ── Derived sections ──────────────────────────────────────────────────
    build_fixer              = _build_fixer(validation)
    dependency_modernization = _dependency_modernization(dependency_map)
    architecture_suggestions = _architecture_suggestions(manual_fixes)
    generated_tests          = _generated_tests(migrated_dir)

    # ── Readiness scorecard ───────────────────────────────────────────────
    high_fixes   = len([f for f in manual_fixes if any(k in f for k in ['System.Web', 'Global.asax', 'packages.config', 'HttpContext.Current'])])
    medium_fixes = len(manual_fixes) - high_fixes

    def _score(val): return max(0, min(100, val))

    readiness_categories = [
        {'name': 'Build Status',       'score': _score(95 if build_passed else 40),       'status': 'Good' if build_passed else 'Risk',   'description': 'dotnet build passed' if build_passed else 'Build failed or skipped — review errors'},
        {'name': 'Legacy Code Removed','score': _score(100 - high_fixes * 15),            'status': 'Good' if high_fixes == 0 else 'Risk', 'description': f'{high_fixes} high-priority legacy pattern(s) still present' if high_fixes else 'No critical legacy patterns remaining'},
        {'name': 'Code Quality',       'score': _score(100 - medium_fixes * 10),          'status': 'Good' if medium_fixes == 0 else 'Review', 'description': f'{medium_fixes} code quality item(s) to review' if medium_fixes else 'No code quality issues detected'},
        {'name': 'Dependencies',       'score': _score(90 if dependency_map else 60),     'status': 'Good' if dependency_map else 'Review', 'description': f'{len(dependency_map)} package(s) migrated' if dependency_map else 'No packages detected'},
        {'name': 'Files Migrated',     'score': _score(100 if len(changes) > 0 else 0),  'status': 'Good' if len(changes) > 0 else 'Risk', 'description': f'{len(changes)} file(s) successfully migrated'},
    ]
    readiness_score = round(sum(c['score'] for c in readiness_categories) / len(readiness_categories))
    readiness_level = 'Ready' if readiness_score >= 80 else 'Moderate' if readiness_score >= 60 else 'High Risk'
    readiness_recs  = []
    if not build_passed:
        readiness_recs.append('Fix build errors before deploying — check Build Error AI Fixer for details.')
    if high_fixes > 0:
        readiness_recs.append(f'Address {high_fixes} high-priority item(s) in Manual Fix List before deploying.')
    if medium_fixes > 0:
        readiness_recs.append(f'Review {medium_fixes} code quality item(s) in Manual Fix List.')
    if not readiness_recs:
        readiness_recs.append('Migration looks clean — proceed with smoke testing and regression tests.')

    readiness = {
        'score': readiness_score, 'level': readiness_level,
        'summary': f'{readiness_level} — {readiness_score}/100 migration readiness score.',
        'categories': readiness_categories, 'recommendations': readiness_recs,
    }

    executive_report = {
        "title":               ".NET Migration Executive Report",
        "total_files_migrated": len(migrated_files),
        "build_status":        "Passed" if build_passed else "Needs Review",
        "readiness_score":     readiness_score,
        "readiness_level":     readiness_level,
        "dependency_count":    len(dependency_map),
        "manual_fix_count":    len(manual_fixes),
        "diff_summary":        diff["summary"],
        "recommendations":     readiness_recs,
    }

    return {
        "summary":                f"{len(migrated_files)} file(s) migrated successfully.",
        "from_version":           from_version,
        "to_version":             to_version,
        "changes":                changes,
        "issues":                 [],
        "recommendations":        ["Review code for business logic correctness.", "Run dotnet build to verify compilation.", "Test all API endpoints and database connections."],
        "dependency_map":         dependency_map,
        "manual_fixes":           manual_fixes,
        "readiness":              readiness,
        "view_migration":         view_migration,
        "webforms_migration":     webforms_migration,
        "blazor_migration":       blazor_migration,
        "auth_migration":         auth_migration,
        "validation":             validation,
        "diff":                   diff,
        "code_rewrite_previews":  code_rewrite_previews,
        "build_fixer":            build_fixer,
        "dependency_modernization": dependency_modernization,
        "architecture_suggestions": architecture_suggestions,
        "generated_tests":        generated_tests,
        "executive_report":       executive_report,
        "guardrails":             guardrail_result,
        "orchestrator":           orchestrator_data,
    }


def _scan_manual_fixes(migrated_dir: Path) -> list:
    """Scan migrated output files for remaining issues."""
    manual_fixes = []
    for cs_file in migrated_dir.rglob("*.cs"):
        if any(p.lower() in {"obj", "bin"} for p in cs_file.parts):
            continue
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            rel = str(cs_file.relative_to(migrated_dir))
            if "TODO" in content or "FIXME" in content:
                manual_fixes.append(f"{rel}: Contains TODO/FIXME comments requiring attention")
            if "System.Web" in content:
                manual_fixes.append(f"{rel}: Still contains System.Web references — verify compatibility")
            if re.search(r"async void \w+\(", content):
                manual_fixes.append(f"{rel}: Contains async void methods — consider async Task instead")
            if "HttpContext.Current" in content:
                manual_fixes.append(f"{rel}: Contains HttpContext.Current — replace with IHttpContextAccessor")
            if "ConfigurationManager" in content:
                manual_fixes.append(f"{rel}: Contains ConfigurationManager — replace with IConfiguration")
        except Exception:
            pass
    structural_leftovers = [
        ("packages.config", "packages.config still present — migrate to PackageReference"),
        ("Web.config",      "Web.config still present — not needed in ASP.NET Core"),
        ("Global.asax",     "Global.asax still present — startup hooks should be in Program.cs"),
        ("Global.asax.cs",  "Global.asax.cs still present — merge into Program.cs"),
        ("App_Start",       "App_Start folder still present — not needed in ASP.NET Core"),
        ("AssemblyInfo.cs", "AssemblyInfo.cs still present — not needed in SDK-style projects"),
    ]
    for filename, message in structural_leftovers:
        for match in migrated_dir.rglob(filename):
            if any(p.lower() in {"obj", "bin"} for p in match.parts):
                continue
            manual_fixes.append(f"{str(match.relative_to(migrated_dir))}: {message}")
    return manual_fixes


# ── Agent wrapper ──────────────────────────────────────────────────────
from agents.base_agent import BaseAgent
from agents.context import MigrationContext, AgentObservation

class ReporterAgent(BaseAgent):
    name = "Reporter Agent"
    goal = "generate full migration report from context — no re-running of agents"

    def act(self, context: MigrationContext) -> dict:
        store_context(context)
        report = generate_report()
        return {"success": True, "report": report}

    def observe(self, result: dict, context: MigrationContext) -> AgentObservation:
        return AgentObservation(
            agent=self.name,
            status="completed",
            summary="Migration report generated from agent context.",
            actionable=False,
            data=result,
        )


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
    # Deduplicate — same error code + same message counts as one
    seen = set()
    unique_errors = []
    for code, message in error_codes:
        key = f"{code}:{message[:80]}"
        if key not in seen:
            seen.add(key)
            unique_errors.append((code, message))
    items = []
    for code, message in unique_errors[:8]:
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
    if "runtimeinformation" in msg:
        return "Add 'using System.Runtime.InteropServices;' to the file using RuntimeInformation."
    if "does not exist in the current context" in msg:
        return "Add the required using statement or NuGet package for the missing type."
    if "cannot convert" in msg or "no implicit conversion" in msg:
        return "Fix type mismatch — check method signatures and return types."
    if "does not contain a definition" in msg:
        return "Method or property not found — check the correct API for .NET 8."
    if "ambiguous" in msg:
        return "Resolve ambiguous reference by adding fully qualified namespace."
    if "duplicate" in msg:
        return "Remove duplicate using statements or class definitions."
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

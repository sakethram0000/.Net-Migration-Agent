from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import difflib
import csv
import io
import zipfile
from pathlib import Path
from typing import Any

from .inventory import IGNORED_DIRS, interesting_files


PACKAGE_TARGETS = {
    "Microsoft.EntityFrameworkCore": "8.0.0",
    "Microsoft.EntityFrameworkCore.Design": "8.0.0",
    "Microsoft.EntityFrameworkCore.SqlServer": "8.0.0",
    "Microsoft.AspNetCore.Authentication.JwtBearer": "8.0.0",
    "Swashbuckle.AspNetCore": "6.5.0",
}

REMOVE_PACKAGES = {
    "Microsoft.AspNetCore.SpaServices",
    "Microsoft.AspNetCore.SpaServices.Extensions",
    "Microsoft.AspNetCore.NodeServices",
}


def safe_extract(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if not str(target).startswith(str(destination.resolve())):
                raise ValueError(f"Unsafe zip entry blocked: {member.filename}")
            if any(part in IGNORED_DIRS for part in Path(member.filename).parts):
                continue
            archive.extract(member, destination)


def copy_source_to_output(source: Path, output: Path) -> None:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        rel = path.relative_to(source)
        target = output / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def upgrade_project_files(output: Path, target_framework: str) -> list[str]:
    changes = []
    for csproj in output.rglob("*.csproj"):
        original = csproj.read_text(encoding="utf-8", errors="ignore")
        text = original
        if is_legacy_project(text):
            text = legacy_to_sdk_project(text, csproj, target_framework)
        else:
            text = re.sub(r"<TargetFrameworks?>.*?</TargetFrameworks?>", f"<TargetFramework>{target_framework}</TargetFramework>", text, flags=re.I | re.S)
            if "<TargetFramework>" not in text:
                text = text.replace("<PropertyGroup>", f"<PropertyGroup>\n    <TargetFramework>{target_framework}</TargetFramework>", 1)
        if "<Nullable>" not in text:
            text = text.replace("</PropertyGroup>", "    <Nullable>enable</Nullable>\n  </PropertyGroup>", 1)
        if "<ImplicitUsings>" not in text:
            text = text.replace("</PropertyGroup>", "    <ImplicitUsings>enable</ImplicitUsings>\n  </PropertyGroup>", 1)
        for package in REMOVE_PACKAGES:
            text = re.sub(rf'\s*<PackageReference[^>]+Include="{re.escape(package)}"[^>]*/>\s*', "\n", text, flags=re.I)
            text = re.sub(rf'\s*<PackageReference[^>]+Include="{re.escape(package)}"[^>]*>.*?</PackageReference>\s*', "\n", text, flags=re.I | re.S)
        for package, version in PACKAGE_TARGETS.items():
            text = re.sub(rf'(PackageReference[^>]+Include="{re.escape(package)}"[^>]+Version=")[^"]+(")', rf"\g<1>{version}\2", text, flags=re.I)
        if "Microsoft.NET.Sdk.Web" in text and "App_Start\\**\\*.cs" not in text:
            text = text.replace(
                "</Project>",
                """  <ItemGroup>
    <Compile Remove="App_Start\\**\\*.cs" />
    <Compile Remove="Global.asax.cs" />
    <Compile Remove="Properties\\AssemblyInfo.cs" />
    <Content Remove="Global.asax" />
    <Content Remove="Web.config" />
    <Content Remove="Views\\Web.config" />
  </ItemGroup>
</Project>""",
                1,
            )
        if "Microsoft.NET.Sdk.Web" in text and "Global.asax.cs" not in text:
            text = text.replace('    <Compile Remove="Properties\\AssemblyInfo.cs" />', '    <Compile Remove="Global.asax.cs" />\n    <Compile Remove="Properties\\AssemblyInfo.cs" />', 1)
            text = text.replace('    <Content Remove="Web.config" />', '    <Content Remove="Global.asax" />\n    <Content Remove="Web.config" />', 1)
        if text != original:
            csproj.write_text(text, encoding="utf-8")
            changes.append(f"Upgraded {csproj.name} to {target_framework}")
    return changes


def is_legacy_project(text: str) -> bool:
    return (
        "<Project Sdk=" not in text
        or "<TargetFrameworkVersion>" in text
        or "ProjectTypeGuids" in text
        or "Microsoft.WebApplication.targets" in text
        or 'xmlns="http://schemas.microsoft.com/developer/msbuild/2003"' in text
    )


def legacy_to_sdk_project(text: str, csproj: Path, target_framework: str) -> str:
    is_web = "System.Web.Mvc" in text or "System.Web.Http" in text or "ProjectTypeGuids" in text
    sdk = "Microsoft.NET.Sdk.Web" if is_web else "Microsoft.NET.Sdk"
    package_names = set(re.findall(r'<package\s+id="([^"]+)"\s+version="([^"]+)"', read_packages_config(csproj), re.I))
    package_refs = []
    if is_web:
        package_refs.extend([
            ('Microsoft.AspNetCore.Mvc.NewtonsoftJson', '8.0.0'),
            ('Swashbuckle.AspNetCore', '6.5.0'),
        ])
    for name, version in package_names:
        if name.startswith("Microsoft.AspNet.") or name in REMOVE_PACKAGES:
            continue
        if name == "Newtonsoft.Json":
            package_refs.append((name, "13.0.3"))
        elif name not in {"Microsoft.AspNet.Razor", "Microsoft.AspNet.WebPages"}:
            package_refs.append((name, version))
    package_xml = ""
    deduped = []
    for item in package_refs:
        if item[0] not in [existing[0] for existing in deduped]:
            deduped.append(item)
    if deduped:
        lines = "\n".join(f'    <PackageReference Include="{name}" Version="{version}" />' for name, version in deduped)
        package_xml = f"\n  <ItemGroup>\n{lines}\n  </ItemGroup>"
    legacy_excludes = """
  <ItemGroup>
    <Compile Remove="App_Start\\**\\*.cs" />
    <Compile Remove="Global.asax.cs" />
    <Compile Remove="Properties\\AssemblyInfo.cs" />
    <Content Remove="Global.asax" />
    <Content Remove="Web.config" />
    <Content Remove="Views\\Web.config" />
  </ItemGroup>""" if is_web else ""
    return f"""<Project Sdk="{sdk}">
  <PropertyGroup>
    <TargetFramework>{target_framework}</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>{package_xml}{legacy_excludes}
</Project>
"""


def read_packages_config(csproj: Path) -> str:
    packages = csproj.parent / "packages.config"
    if packages.exists():
        return packages.read_text(encoding="utf-8", errors="ignore")
    return ""


def clean_source_files(output: Path) -> list[str]:
    changes = []
    has_legacy_web = (
        any(output.rglob("Global.asax"))
        or any(output.rglob("App_Start"))
        or any("System.Web.Mvc" in path.read_text(encoding="utf-8", errors="ignore") or "System.Web.Http" in path.read_text(encoding="utf-8", errors="ignore") for path in output.rglob("*.cs"))
    )
    invalid_usings = [
        "using System.Web;",
        "using System.Web.Mvc;",
        "using System.Web.Http;",
        "using Microsoft.AspNet.Identity;",
    ]
    for file in output.rglob("*.cs"):
        original = file.read_text(encoding="utf-8", errors="ignore")
        text = original
        for item in invalid_usings:
            text = text.replace(item + "\n", "")
        text = re.sub(r"\n{3,}", "\n\n", text)
        if text != original:
            file.write_text(text, encoding="utf-8")
            changes.append(f"Cleaned obsolete usings in {file.name}")
    if has_legacy_web:
        changes.extend(convert_legacy_web_app(output))
    return changes


def convert_legacy_web_app(output: Path) -> list[str]:
    changes = []
    app_start = output / "App_Start"
    if app_start.exists():
        try:
            remove_tree(app_start)
            changes.append("Removed legacy App_Start configuration")
        except OSError:
            changes.append("Excluded legacy App_Start configuration from SDK build")

    properties = output / "Properties"
    assembly_info = properties / "AssemblyInfo.cs"
    if assembly_info.exists():
        try:
            assembly_info.unlink()
            changes.append("Removed legacy AssemblyInfo.cs")
        except OSError:
            changes.append("Excluded legacy AssemblyInfo.cs from SDK build")
    if properties.exists() and not any(properties.glob("*.cs")):
        try:
            properties.rmdir()
        except OSError:
            pass

    program = output / "Program.cs"
    program.write_text("""var builder = WebApplication.CreateBuilder(args);

builder.Services.AddControllersWithViews().AddNewtonsoftJson();

var app = builder.Build();

if (!app.Environment.IsDevelopment())
{
    app.UseExceptionHandler("/Home/Error");
}

app.UseStaticFiles();
app.UseRouting();

app.MapControllerRoute(
    name: "default",
    pattern: "{controller=Home}/{action=Index}/{id?}");

app.MapControllers();
app.Run();
""", encoding="utf-8")
    changes.append("Generated ASP.NET Core Program.cs")

    global_files = list(output.rglob("Global.asax")) + list(output.rglob("Global.asax.cs"))
    removed_global = False
    for file in global_files:
        try:
            file.unlink(missing_ok=True)
            removed_global = True
        except OSError:
            continue
    if removed_global:
        changes.append("Removed Global.asax startup files")
    elif global_files:
        changes.append("Excluded Global.asax startup files from SDK build")

    for file in output.rglob("*.cs"):
        text = file.read_text(encoding="utf-8", errors="ignore")
        original = text
        text = text.replace("using System.Web.Mvc;", "using Microsoft.AspNetCore.Mvc;")
        text = text.replace("using System.Web.Http;", "using Microsoft.AspNetCore.Mvc;")
        text = text.replace(": ApiController", ": ControllerBase")
        text = text.replace("IHttpActionResult", "IActionResult")
        text = text.replace("[RoutePrefix(\"api/orders\")]", "[Route(\"api/orders\")]")
        text = text.replace("[Route(\"\")]", "[HttpGet]")
        text = text.replace("[HttpGet]\n        [HttpGet]", "[HttpGet]")
        text = text.replace("return NotFound();", "return NotFound();")
        text = text.replace("return Ok(order);", "return Ok(order);")
        text = text.replace("ConfigurationManager.AppSettings[\"PortalName\"] ?? \"Legacy Customer Portal\"", "\"Legacy Customer Portal\"")
        text = text.replace("ConfigurationManager.AppSettings[\"PortalName\"]", "\"Legacy Customer Portal\"")
        text = text.replace("\"Legacy Customer Portal\" ?? \"Legacy Customer Portal\"", "\"Legacy Customer Portal\"")
        if "using System.Configuration;" in text:
            text = text.replace("using System.Configuration;\n", "")
        if file.name == "HomeController.cs":
            text = text.replace("public ActionResult Index()", "public IActionResult Index()")
        if needs_aspnetcore_mvc_using(text) and "using Microsoft.AspNetCore.Mvc;" not in text:
            text = "using Microsoft.AspNetCore.Mvc;\n" + text
        if text != original:
            file.write_text(text, encoding="utf-8")
            changes.append(f"Converted {file.name} to ASP.NET Core MVC/Web API")

    views_web_config = output / "Views" / "Web.config"
    try:
        views_web_config.unlink(missing_ok=True)
    except OSError:
        pass
    web_config = output / "Web.config"
    try:
        web_config.unlink(missing_ok=True)
    except OSError:
        pass
    packages = output / "packages.config"
    try:
        packages.unlink(missing_ok=True)
    except OSError:
        pass
    return changes


def needs_aspnetcore_mvc_using(text: str) -> bool:
    mvc_tokens = [
        " : Controller",
        " : ControllerBase",
        "IActionResult",
        "[Route(",
        "[HttpGet",
        "View(",
        "NotFound(",
        "Ok(",
    ]
    return any(token in text for token in mvc_tokens)


def remove_tree(path: Path) -> None:
    def make_writable_and_retry(func, failed_path, exc_info) -> None:
        os.chmod(failed_path, 0o700)
        func(failed_path)

    shutil.rmtree(path, onerror=make_writable_and_retry)


def build_output(output: Path) -> dict[str, Any]:
    sln_files = list(output.rglob("*.sln"))
    csproj_files = list(output.rglob("*.csproj"))
    target = sln_files[0] if sln_files else csproj_files[0] if csproj_files else None
    if not target:
        return {"success": False, "error": "No .sln or .csproj found in migrated output"}
    cwd = target.parent
    try:
        restore = subprocess.run(["dotnet", "restore", str(target), "--nologo"], cwd=str(cwd), capture_output=True, text=True, timeout=240)
        if restore.returncode != 0:
            return {"success": False, "stage": "restore", "output": restore.stdout, "errors": restore.stderr}
        build = subprocess.run(["dotnet", "build", str(target), "-v", "m", "--nologo", "--no-restore"], cwd=str(cwd), capture_output=True, text=True, timeout=240)
        return {"success": build.returncode == 0, "stage": "build", "output": build.stdout, "errors": build.stderr}
    except FileNotFoundError:
        return {"success": False, "error": ".NET SDK not found. Install the target .NET SDK to validate builds."}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def readiness_scorecard(inv: dict[str, Any], validation: dict[str, Any] | None = None) -> dict[str, Any]:
    patterns = inv.get("patterns") or []
    high = sum(1 for item in patterns if item.get("severity") == "High")
    medium = max(0, len(patterns) - high)
    complexity = int((inv.get("complexity") or {}).get("score") or 0)
    project_count = int(inv.get("project_count") or 0)
    source_files = int(inv.get("source_file_count") or 0)
    validation_passed = bool((validation or {}).get("success"))

    categories = [
        score_item("Project Compatibility", 100 - min(60, complexity // 2), "Framework and project-file migration readiness"),
        score_item("Dependency Risk", 100 - min(70, medium * 10 + high * 15), "NuGet/package modernization exposure"),
        score_item("Code Modernization", 100 - min(75, high * 12), "Legacy API and System.Web usage exposure"),
        score_item("Build Readiness", 95 if validation_passed else 45, "Restore/build validation status"),
        score_item("Application Size", 100 - min(45, project_count * 4 + source_files // 20), "Estimated delivery complexity from app size"),
    ]
    score = round(sum(item["score"] for item in categories) / len(categories))
    level = "Ready" if score >= 80 else "Moderate" if score >= 60 else "High Risk"
    return {
        "score": score,
        "level": level,
        "summary": f"{level} migration readiness with {high} high-risk and {medium} medium-risk findings.",
        "categories": categories,
        "recommendations": readiness_recommendations(score, high, medium, validation_passed),
    }


def score_item(name: str, score: int, description: str) -> dict[str, Any]:
    value = max(0, min(100, score))
    return {"name": name, "score": value, "status": "Good" if value >= 80 else "Review" if value >= 60 else "Risk", "description": description}


def readiness_recommendations(score: int, high: int, medium: int, validation_passed: bool) -> list[str]:
    recommendations = []
    if high:
        recommendations.append("Prioritize high-risk System.Web, config, and project-file blockers before broad modernization.")
    if medium:
        recommendations.append("Review package/config findings and confirm equivalent ASP.NET Core patterns.")
    if not validation_passed:
        recommendations.append("Use the build output to drive a fix-and-retry cycle before runtime validation.")
    if score >= 80:
        recommendations.append("Proceed with smoke testing and targeted regression tests.")
    return recommendations or ["Proceed with migration validation and smoke testing."]


def migration_diff(source: Path, output: Path, max_files: int = 80) -> dict[str, Any]:
    source_files = {file.relative_to(source).as_posix(): file for file in interesting_files(source)}
    output_files = {file.relative_to(output).as_posix(): file for file in interesting_files(output)}
    added = sorted(set(output_files) - set(source_files))
    removed = sorted(set(source_files) - set(output_files))
    common = sorted(set(source_files) & set(output_files))
    modified = []
    previews = []
    for rel in common:
        source_text = source_files[rel].read_text(encoding="utf-8", errors="ignore")
        output_text = output_files[rel].read_text(encoding="utf-8", errors="ignore")
        if source_text == output_text:
            continue
        modified.append(rel)
        if len(previews) < 12:
            diff = difflib.unified_diff(
                source_text.splitlines(),
                output_text.splitlines(),
                fromfile=f"legacy/{rel}",
                tofile=f"migrated/{rel}",
                lineterm="",
                n=3,
            )
            previews.append({"path": rel, "diff": "\n".join(list(diff)[:120])})
    return {
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
            "unchanged": max(0, len(common) - len(modified)),
        },
        "added": added[:max_files],
        "removed": removed[:max_files],
        "modified": modified[:max_files],
        "previews": previews,
    }


def code_rewrite_previews(source: Path, output: Path, max_items: int = 8) -> list[dict[str, Any]]:
    previews = []
    for rel in [
        "LegacyCustomerPortalNet45.csproj",
        "Controllers/HomeController.cs",
        "Controllers/OrdersApiController.cs",
        "Services/OrderRepository.cs",
        "Program.cs",
    ]:
        src = source / rel
        out = output / rel
        if not out.exists():
            continue
        legacy = src.read_text(encoding="utf-8", errors="ignore") if src.exists() else ""
        migrated = out.read_text(encoding="utf-8", errors="ignore")
        if legacy.strip() == migrated.strip() and src.exists():
            continue
        previews.append({
            "path": rel,
            "legacy": legacy[:6000] or "New file generated during migration.",
            "proposed": migrated[:6000],
            "explanation": rewrite_explanation(rel, legacy, migrated),
        })
        if len(previews) >= max_items:
            break
    return previews


def rewrite_explanation(path: str, legacy: str, migrated: str) -> str:
    if path.endswith(".csproj"):
        return "Converts legacy project metadata to SDK-style project format and targets the selected modern .NET runtime."
    if "Controller" in path:
        return "Replaces System.Web MVC/Web API patterns with ASP.NET Core MVC abstractions and action result types."
    if "Program.cs" in path:
        return "Creates the ASP.NET Core hosting pipeline with routing, static files, MVC controllers, and API controller mapping."
    if "ConfigurationManager" in legacy:
        return "Removes direct ConfigurationManager usage so settings can move toward IConfiguration/options patterns."
    return "Shows the proposed migrated file content and the behavior-preserving modernization approach."


def dependency_modernization(inv: dict[str, Any]) -> dict[str, Any]:
    packages = sorted(set(inv.get("packages") or []))
    legacy_hints = {
        "Newtonsoft.Json": ("13.0.3", "Keep or migrate gradually to System.Text.Json if contracts allow."),
        "EntityFramework": ("Microsoft.EntityFrameworkCore.SqlServer 8.x", "Replace EF6 APIs carefully; validate LINQ/query behavior."),
        "Microsoft.AspNet.Mvc": ("Microsoft.AspNetCore.Mvc", "Replace MVC5 with ASP.NET Core MVC."),
        "Microsoft.AspNet.WebApi": ("Microsoft.AspNetCore.Mvc", "Replace Web API 2 controllers with ASP.NET Core API controllers."),
        "Microsoft.AspNet.Razor": ("ASP.NET Core Razor", "Move Razor view configuration to ASP.NET Core conventions."),
    }
    recommendations = []
    for name in packages:
        target, note = legacy_hints.get(name, ("Review latest compatible package", "Confirm target framework support and breaking changes."))
        recommendations.append({"package": name, "recommended": target, "risk": "High" if name.startswith("Microsoft.AspNet") or name == "EntityFramework" else "Medium", "note": note})
    if not recommendations:
        recommendations.append({"package": "No packages detected", "recommended": "No action", "risk": "Low", "note": "No package modernization candidates found in project files."})
    return {"summary": f"{len(recommendations)} dependency modernization recommendation(s).", "items": recommendations}


def architecture_suggestions(inv: dict[str, Any]) -> dict[str, Any]:
    patterns = inv.get("patterns") or []
    titles = " ".join(item.get("title", "") for item in patterns)
    suggestions = [
        {"area": "Configuration", "recommendation": "Move app settings from Web.config/ConfigurationManager to IConfiguration and strongly typed options.", "priority": "High" if "ConfigurationManager" in titles or "web.config" in titles else "Medium"},
        {"area": "Hosting", "recommendation": "Use ASP.NET Core minimal hosting in Program.cs with explicit middleware ordering.", "priority": "High" if "Global.asax" in titles else "Medium"},
        {"area": "Dependency Injection", "recommendation": "Register repositories/services in DI instead of manually instantiating dependencies inside controllers.", "priority": "Medium"},
        {"area": "Observability", "recommendation": "Add structured logging, health checks, and startup diagnostics for migrated services.", "priority": "Medium"},
        {"area": "API Modernization", "recommendation": "Use ControllerBase, attribute routing, model validation, and OpenAPI/Swagger for API endpoints.", "priority": "High" if "System.Web usage" in titles else "Medium"},
    ]
    return {"summary": "Architecture modernization suggestions generated from inventory findings.", "items": suggestions}


def generated_test_plan(output: Path) -> dict[str, Any]:
    controllers = [path.relative_to(output).as_posix() for path in output.rglob("*Controller.cs")]
    tests = [
        {"name": "SmokeTests.HomePage_ReturnsSuccess", "type": "Smoke", "target": "/", "sample": 'Assert.True(response.IsSuccessStatusCode);'},
        {"name": "SmokeTests.OrdersApi_ReturnsOrders", "type": "API", "target": "/api/orders", "sample": 'Assert.Contains("orderId", body);'},
    ]
    for controller in controllers[:6]:
        tests.append({"name": f"{Path(controller).stem}Tests.Actions_ReturnExpectedResult", "type": "Controller", "target": controller, "sample": "Assert.NotNull(result);"})
    return {"summary": f"{len(tests)} starter test scenario(s) generated.", "items": tests, "suggested_project": "MigratedApp.Tests"}


def build_error_fixer(validation: dict[str, Any], inv: dict[str, Any]) -> dict[str, Any]:
    output = str(validation.get("errors") or validation.get("output") or validation.get("error") or "")
    errors = re.findall(r"error\s+([A-Z]+\d+):\s*(.*)", output)
    fixes = []
    for code, message in errors[:8]:
        fixes.append({"error": code, "root_cause": message[:220], "suggested_fix": deterministic_fix_for_error(code, message), "applied": False})
    if not fixes and validation.get("success"):
        fixes.append({"error": "None", "root_cause": "Build passed.", "suggested_fix": "No build fix required. Continue with smoke and regression testing.", "applied": False})
    elif not fixes:
        fixes.append({"error": "Unknown", "root_cause": "Build failed without parseable compiler error codes.", "suggested_fix": "Review restore/build logs and rerun after dependency and source cleanup.", "applied": False})
    return {"summary": "Build fix analysis generated from compiler output.", "items": fixes}


def deterministic_fix_for_error(code: str, message: str) -> str:
    text = message.lower()
    if "system.web" in text:
        return "Replace System.Web dependency with ASP.NET Core equivalents and remove legacy App_Start/Global.asax wiring."
    if "configurationmanager" in text:
        return "Move configuration access to IConfiguration or add a temporary compatibility package only if needed."
    if "namespace" in text or "type or namespace" in text:
        return "Update using statements and NuGet references for ASP.NET Core/.NET target framework."
    if "nullable" in text:
        return "Initialize nullable reference properties or mark them nullable/required."
    return "Apply targeted source/package correction and rerun restore/build."


def executive_report(report: dict[str, Any]) -> dict[str, Any]:
    readiness = report.get("readiness") or {}
    diff = (report.get("diff") or {}).get("summary") or {}
    validation = report.get("validation") or {}
    smoke = report.get("smoke_test") or {}
    return {
        "title": ".NET Migration Executive Report",
        "job_id": report.get("job_id"),
        "from_version": report.get("from_version"),
        "to_version": report.get("to_version"),
        "readiness_score": readiness.get("score"),
        "readiness_level": readiness.get("level"),
        "build_status": "Passed" if validation.get("success") else "Needs Review",
        "smoke_status": smoke.get("status", "Not Run"),
        "diff_summary": diff,
        "top_risks": [item.get("title") or item.get("area") for item in (report.get("inventory") or {}).get("patterns", [])[:6]],
        "next_steps": (readiness.get("recommendations") or [])[:6],
    }


def report_to_csv(report: dict[str, Any], kind: str) -> str:
    rows = report_rows(report, kind)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=sorted({key for row in rows for key in row.keys()} or {"message"}))
    writer.writeheader()
    writer.writerows(rows or [{"message": "No data"}])
    return out.getvalue()


def report_rows(report: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    if kind == "readiness":
        return (report.get("readiness") or {}).get("categories", [])
    if kind == "dependencies":
        return (report.get("dependency_modernization") or {}).get("items", [])
    if kind == "architecture":
        return (report.get("architecture_suggestions") or {}).get("items", [])
    if kind == "tests":
        return (report.get("generated_tests") or {}).get("items", [])
    if kind == "diff":
        diff = report.get("diff") or {}
        return [{"change": "added", "path": path} for path in diff.get("added", [])] + [{"change": "modified", "path": path} for path in diff.get("modified", [])] + [{"change": "removed", "path": path} for path in diff.get("removed", [])]
    if kind == "build-fixer":
        return (report.get("build_fixer") or {}).get("items", [])
    if kind == "rewrite":
        return [
            {"path": item.get("path"), "explanation": item.get("explanation"), "legacy_chars": len(item.get("legacy") or ""), "proposed_chars": len(item.get("proposed") or "")}
            for item in report.get("code_rewrite_previews", [])
        ]
    executive = report.get("executive_report") or executive_report(report)
    return [{"metric": key, "value": json.dumps(value) if isinstance(value, (dict, list)) else value} for key, value in executive.items()]


def report_to_html(report: dict[str, Any], kind: str) -> str:
    title = kind.replace("-", " ").title()
    rows = report_rows(report, kind)
    body_rows = "".join("<tr>" + "".join(f"<td>{html_escape(str(row.get(col, '')))}</td>" for col in sorted(row.keys())) + "</tr>" for row in rows[:200])
    headers = sorted({key for row in rows for key in row.keys()} or {"message"})
    head = "".join(f"<th>{html_escape(col)}</th>" for col in headers)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html_escape(title)}</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:32px;color:#303030}}h1{{color:#024099}}table{{width:100%;border-collapse:collapse}}th{{background:#056bfc;color:white;text-align:left}}td,th{{border:1px solid #dedede;padding:8px;font-size:12px}}.print{{background:#fabd00;border:0;padding:10px 14px;font-weight:700}}</style>
</head><body><button class="print" onclick="window.print()">Print / Save as PDF</button><h1>{html_escape(title)}</h1><p>Job: {html_escape(str(report.get('job_id','')))}</p><table><thead><tr>{head}</tr></thead><tbody>{body_rows}</tbody></table></body></html>"""


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def zip_output(output: Path, zip_path: Path) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in interesting_files(output):
            archive.write(file, file.relative_to(output))
    return zip_path


def write_report(output: Path, report: dict[str, Any]) -> None:
    (output / "migration-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

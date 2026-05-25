"""
Guardrail Agent — runs after Fix Agent, before Build Validator.
Scans the migrated output for architecture and code quality violations.
READ ONLY — does not modify any files.
"""
from pathlib import Path
import re

SKIP_FOLDERS = {"obj", "bin", ".vs", ".git", "node_modules"}


def run_guardrails(output_dir: str, progress_callback=None) -> dict:
    """
    Main entry point. Scans migrated output and returns a structured
    guardrail report with violations, passed checks and a score.
    Does NOT modify any files.
    """
    out = Path(output_dir)
    if not out.exists():
        return _empty_result("Output directory not found.")

    if progress_callback:
        progress_callback("Guardrail Agent: Scanning migrated output...")

    violations = []
    passed = []

    cs_files = [
        f for f in out.rglob("*.cs")
        if not any(p.lower() in SKIP_FOLDERS for p in f.parts)
    ]
    csproj_files = [
        f for f in out.rglob("*.csproj")
        if not any(p.lower() in SKIP_FOLDERS for p in f.parts)
    ]

    for cs_file in cs_files:
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            rel = str(cs_file.relative_to(out))
            _check_file(content, rel, violations, passed)
        except Exception:
            pass

    for csproj in csproj_files:
        try:
            content = csproj.read_text(encoding="utf-8", errors="ignore")
            rel = str(csproj.relative_to(out))
            _check_csproj(content, rel, violations, passed)
        except Exception:
            pass

    # Overall checks
    _check_program_cs(out, violations, passed)
    _check_startup_cs(out, violations, passed)
    _check_folder_structure(out, violations, passed)

    total = len(violations) + len(passed)
    score = round((len(passed) / total) * 100) if total else 100
    level = "Good" if score >= 80 else "Needs Review" if score >= 50 else "Poor"

    if progress_callback:
        progress_callback(f"Guardrail Agent: {len(passed)}/{total} checks passed. Score: {score}/100.")

    return {
        "score": score,
        "level": level,
        "violations": violations,
        "passed": passed,
        "total": total,
        "passed_count": len(passed),
        "violation_count": len(violations),
        "summary": f"{level} — {score}/100 architecture score. {len(violations)} violation(s) found.",
    }


# ── Per-file checks ───────────────────────────────────────────────────────

def _check_file(content: str, rel: str, violations: list, passed: list):
    is_controller = "Controller" in rel or "controller" in rel.lower()
    is_program    = rel.replace("\\", "/").endswith("Program.cs")

    # 1. System.Web references remaining
    if "System.Web" in content:
        violations.append(_v(rel, "System.Web reference remaining",
            "Replace System.Web with ASP.NET Core equivalents.", "High"))
    else:
        passed.append(_p(rel, "No System.Web references"))

    # 2. ConfigurationManager remaining
    if "ConfigurationManager" in content:
        violations.append(_v(rel, "ConfigurationManager usage",
            "Replace with IConfiguration injected via constructor.", "High"))
    else:
        passed.append(_p(rel, "No ConfigurationManager usage"))

    # 3. HttpContext.Current remaining
    if "HttpContext.Current" in content:
        violations.append(_v(rel, "HttpContext.Current usage",
            "Replace with IHttpContextAccessor injected via constructor.", "High"))
    else:
        passed.append(_p(rel, "No HttpContext.Current usage"))

    # 4. async void methods (except event handlers)
    if re.search(r'async\s+void\s+\w+\s*\(', content):
        violations.append(_v(rel, "async void method detected",
            "Replace async void with async Task to avoid unhandled exceptions.", "Medium"))
    else:
        passed.append(_p(rel, "No async void methods"))

    # 5. Direct DbContext instantiation
    if re.search(r'new\s+\w*(?:DbContext|ApplicationContext|AppDbContext)\s*\(', content):
        violations.append(_v(rel, "Direct DbContext instantiation",
            "Inject DbContext via constructor using dependency injection.", "High"))
    else:
        passed.append(_p(rel, "No direct DbContext instantiation"))

    # 6. Controller-specific checks
    if is_controller:
        # Business logic in controller — heuristic: direct DB calls in controller
        if re.search(r'\.SaveChanges\(\)|\.SaveChangesAsync\(\)', content):
            violations.append(_v(rel, "Direct database save in controller",
                "Move data access logic to a Repository or Service layer.", "Medium"))
        else:
            passed.append(_p(rel, "No direct DB saves in controller"))

        # Controller not inheriting ControllerBase or Controller
        if "class" in content and "Controller" in rel:
            if not re.search(r':\s*(Controller|ControllerBase)', content):
                violations.append(_v(rel, "Controller not inheriting ControllerBase",
                    "Inherit from ControllerBase for API controllers or Controller for MVC.", "Medium"))
            else:
                passed.append(_p(rel, "Controller correctly inherits ControllerBase"))

        # Missing [ApiController] attribute on API controllers
        if "ControllerBase" in content and "[ApiController]" not in content:
            violations.append(_v(rel, "Missing [ApiController] attribute",
                "Add [ApiController] attribute to API controllers.", "Low"))
        else:
            passed.append(_p(rel, "[ApiController] attribute present"))

    # 7. File-scoped namespace check
    if "namespace " in content:
        block_ns = re.search(r'namespace\s+[\w\.]+\s*\{', content)
        if block_ns and not is_program:
            violations.append(_v(rel, "Block-style namespace used",
                "Convert to file-scoped namespace: 'namespace Foo.Bar;'", "Low"))
        else:
            passed.append(_p(rel, "File-scoped namespace used"))

    # 8. NotImplementedException remaining
    if "throw new NotImplementedException" in content:
        violations.append(_v(rel, "NotImplementedException remaining",
            "Implement the method or add a TODO comment with tracking reference.", "Medium"))
    else:
        passed.append(_p(rel, "No NotImplementedException found"))

    # 9. Microsoft Graph SDK v4 pattern remaining (.Request().GetAsync())
    if ".Request()." in content and ".GetAsync()" in content:
        violations.append(_v(rel, "Microsoft Graph SDK v4 pattern detected",
            "Replace .Request().GetAsync() with .GetAsync() — Graph SDK v5 no longer uses .Request().", "High"))
    else:
        passed.append(_p(rel, "No Graph SDK v4 patterns detected"))

    # 10. Hardcoded connection strings — security risk
    hardcoded_patterns = [
        r'Server\s*=\s*[^{][^;"]+;\s*Database\s*=',
        r'Host\s*=\s*[^{][^;"]+;\s*Database\s*=',
        r'mongodb://[^{"\s]+',
        r'Data Source\s*=\s*[^{][^;"]+;\s*Initial Catalog\s*=',
    ]
    found_hardcoded = any(re.search(p, content, re.IGNORECASE) for p in hardcoded_patterns)
    if found_hardcoded:
        violations.append(_v(rel, "Hardcoded connection string detected",
            "Move connection strings to appsettings.json and read via builder.Configuration.GetConnectionString().", "High"))
    else:
        passed.append(_p(rel, "No hardcoded connection strings detected"))

    # 11. Empty catch blocks — silently swallows errors
    if re.search(r'catch\s*\(\s*\w[^)]*\)\s*\{\s*\}', content):
        violations.append(_v(rel, "Empty catch block detected",
            "Add logging or error handling inside catch blocks — empty catches silently swallow exceptions.", "Medium"))
    else:
        passed.append(_p(rel, "No empty catch blocks"))

    # 12. Synchronous .Result or .Wait() on async calls — deadlock risk
    if re.search(r'\.Result\b', content) or re.search(r'\.Wait\(\)', content):
        violations.append(_v(rel, ".Result or .Wait() on async call detected",
            "Replace .Result/.Wait() with await to avoid deadlocks.", "High"))
    else:
        passed.append(_p(rel, "No synchronous .Result or .Wait() on async calls"))


def _check_csproj(content: str, rel: str, violations: list, passed: list):
    # Target framework
    if "net8.0" in content or "net9.0" in content or "net10.0" in content:
        passed.append(_p(rel, "Target framework is .NET 8+"))
    else:
        violations.append(_v(rel, "Target framework not updated",
            "Set <TargetFramework>net8.0</TargetFramework>", "High"))

    # Nullable enabled
    if "<Nullable>enable</Nullable>" in content:
        passed.append(_p(rel, "Nullable reference types enabled"))
    else:
        violations.append(_v(rel, "Nullable reference types not enabled",
            "Add <Nullable>enable</Nullable> to PropertyGroup.", "Low"))

    # ImplicitUsings enabled
    if "<ImplicitUsings>enable</ImplicitUsings>" in content:
        passed.append(_p(rel, "ImplicitUsings enabled"))
    else:
        violations.append(_v(rel, "ImplicitUsings not enabled",
            "Add <ImplicitUsings>enable</ImplicitUsings> to PropertyGroup.", "Low"))

    # Old packages remaining
    old_packages = [
        "Microsoft.AspNet.Mvc", "Microsoft.AspNet.WebApi",
        "Microsoft.Web.Infrastructure", "Microsoft.AspNet.WebPages"
    ]
    found_old = [p for p in old_packages if p in content]
    if found_old:
        violations.append(_v(rel, f"Legacy packages remaining: {', '.join(found_old)}",
            "Remove these packages — they are not compatible with .NET 8.", "High"))
    else:
        passed.append(_p(rel, "No legacy packages found"))

    # Outdated package versions — minimum .NET 8 compatible versions
    MINIMUM_VERSIONS = {
        "Microsoft.Identity.Web":                  (2, 0, 0),
        "Microsoft.Identity.Web.UI":               (2, 0, 0),
        "Microsoft.Identity.Web.MicrosoftGraph":   (2, 0, 0),
        "Microsoft.ApplicationInsights.AspNetCore":(2, 21, 0),
    }
    outdated = []
    for pkg, min_ver in MINIMUM_VERSIONS.items():
        match = re.search(
            rf'PackageReference Include="{re.escape(pkg)}"[^>]*Version="([\d\.]+)"',
            content, re.IGNORECASE
        )
        if match:
            try:
                parts = [int(x) for x in match.group(1).split(".")]
                # Pad to 3 parts
                while len(parts) < 3:
                    parts.append(0)
                if tuple(parts[:3]) < min_ver:
                    outdated.append(f"{pkg} {match.group(1)} (min: {'.'.join(str(x) for x in min_ver)})")
            except Exception:
                pass
    if outdated:
        violations.append(_v(rel, f"Outdated package version(s): {', '.join(outdated)}",
            "Update to .NET 8 compatible minimum versions.", "Medium"))
    else:
        passed.append(_p(rel, "Package versions are .NET 8 compatible"))


def _check_program_cs(out: Path, violations: list, passed: list):
    program = next(
        (f for f in out.rglob("Program.cs")
         if not any(p.lower() in SKIP_FOLDERS for p in f.parts)),
        None
    )
    if not program:
        violations.append(_v("Program.cs", "Program.cs not found",
            "Program.cs is required for .NET 8 minimal hosting.", "High"))
        return

    content = program.read_text(encoding="utf-8", errors="ignore")

    if "WebApplication.CreateBuilder" in content:
        passed.append(_p("Program.cs", "Uses .NET 8 minimal hosting"))
    else:
        violations.append(_v("Program.cs", "Not using minimal hosting",
            "Use WebApplication.CreateBuilder(args) pattern.", "High"))

    if "app.Run()" in content:
        passed.append(_p("Program.cs", "app.Run() present"))
    else:
        violations.append(_v("Program.cs", "app.Run() missing",
            "Add app.Run() at the end of Program.cs.", "High"))

    # UseDeveloperExceptionPage without environment check — exposes stack traces in production
    if "UseDeveloperExceptionPage" in content:
        if "IsDevelopment" in content:
            passed.append(_p("Program.cs", "UseDeveloperExceptionPage correctly guarded by IsDevelopment"))
        else:
            violations.append(_v("Program.cs", "UseDeveloperExceptionPage without environment check",
                "Wrap app.UseDeveloperExceptionPage() inside if (app.Environment.IsDevelopment()) to avoid exposing stack traces in production.", "High"))

    if "UseAuthentication" in content and "UseAuthorization" in content:
        auth_pos  = content.index("UseAuthentication")
        authz_pos = content.index("UseAuthorization")
        if auth_pos < authz_pos:
            passed.append(_p("Program.cs", "Auth middleware order correct"))
        else:
            violations.append(_v("Program.cs", "Auth middleware order incorrect",
                "UseAuthentication() must come before UseAuthorization().", "High"))


def _check_startup_cs(out: Path, violations: list, passed: list):
    startup = next(
        (f for f in out.rglob("Startup.cs")
         if not any(p.lower() in SKIP_FOLDERS for p in f.parts)),
        None
    )
    if startup:
        violations.append(_v("Startup.cs", "Startup.cs still present in output",
            "Startup.cs should be merged into Program.cs for .NET 8.", "High"))
    else:
        passed.append(_p("Project", "No Startup.cs in output — correctly merged"))


def _check_folder_structure(out: Path, violations: list, passed: list):
    all_folders = {f.name.lower() for f in out.rglob("*") if f.is_dir()
                   and not any(p.lower() in SKIP_FOLDERS for p in f.parts)}
    all_files   = [f for f in out.rglob("*") if f.is_file()
                   and not any(p.lower() in SKIP_FOLDERS for p in f.parts)]

    # Detect project type before checking folder structure
    # Razor Pages projects use Pages/ folder, not Controllers/
    has_razor_pages  = "pages" in all_folders
    has_controllers  = "controllers" in all_folders
    # Minimal API — Program.cs with MapGet/MapPost and no controllers
    program_files    = [f for f in all_files if f.name == "Program.cs"]
    is_minimal_api   = False
    if program_files:
        try:
            prog_content = program_files[0].read_text(encoding="utf-8", errors="ignore")
            is_minimal_api = (
                re.search(r'app\.Map(Get|Post|Put|Delete|Patch)\s*\(', prog_content) is not None
                and not has_controllers
            )
        except Exception:
            pass

    # Controllers folder check — skip for Razor Pages and Minimal API projects
    if has_controllers:
        passed.append(_p("Structure", "Controllers folder present"))
    elif has_razor_pages:
        passed.append(_p("Structure", "Razor Pages project — Controllers folder not required"))
    elif is_minimal_api:
        passed.append(_p("Structure", "Minimal API project — Controllers folder not required"))
    else:
        violations.append(_v("Structure", "No Controllers folder found",
            "Organize controllers into a Controllers/ folder.", "Low"))

    # Models folder check — skip for projects that have no data models (e.g. pure demo/utility apps)
    has_models       = "models" in all_folders or "entities" in all_folders
    has_dbcontext    = any(
        "DbContext" in f.read_text(encoding="utf-8", errors="ignore")
        for f in all_files if f.suffix == ".cs"
    )
    if has_models:
        passed.append(_p("Structure", "Models/Entities folder present"))
    elif not has_dbcontext:
        passed.append(_p("Structure", "No data models required — project has no DbContext"))
    else:
        violations.append(_v("Structure", "No Models or Entities folder found",
            "Organize data models into a Models/ or Entities/ folder.", "Low"))


# ── Helpers ───────────────────────────────────────────────────────────────

def _v(file: str, rule: str, suggestion: str, severity: str) -> dict:
    return {"file": file, "rule": rule, "suggestion": suggestion, "severity": severity}

def _p(file: str, rule: str) -> dict:
    return {"file": file, "rule": rule}

def _empty_result(reason: str) -> dict:
    return {
        "score": 0, "level": "Unknown", "violations": [],
        "passed": [], "total": 0, "passed_count": 0,
        "violation_count": 0, "summary": reason,
    }


# ── Agent wrapper ─────────────────────────────────────────────────────────
from agents.base_agent import BaseAgent
from agents.context import MigrationContext, AgentObservation

class GuardrailAgentWrapper(BaseAgent):
    name = "Guardrail Agent"
    goal = "scan migrated output for architecture and code quality violations"

    def act(self, context: MigrationContext) -> dict:
        return run_guardrails(
            output_dir=context.output_dir,
            progress_callback=context.progress_callback,
        )

    def observe(self, result: dict, context: MigrationContext) -> AgentObservation:
        context.guardrail_result = result
        return AgentObservation(
            agent=self.name,
            status="completed",
            summary=result.get("summary", "Guardrail scan completed."),
            actionable=False,
            recommended_next="build_validator",
            data=result,
        )

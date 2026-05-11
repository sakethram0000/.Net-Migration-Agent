from pathlib import Path
import subprocess
import shutil
import re
from typing import Callable, Optional

BASE_DIR = Path(__file__).parent.parent
DEFAULT_OUTPUT_DIR = str(BASE_DIR / "outputs" / "migrated")

# EF Core packages that must stay version-synced
EF_CORE_PACKAGES = [
    "Microsoft.EntityFrameworkCore",
    "Microsoft.EntityFrameworkCore.Design",
    "Microsoft.EntityFrameworkCore.SqlServer",
    "Microsoft.EntityFrameworkCore.InMemory",
    "Microsoft.EntityFrameworkCore.Sqlite",
    "Microsoft.AspNetCore.Identity.EntityFrameworkCore",
]


def _run(cmd: list, cwd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)


def _version_less_than(v1: str, v2: str) -> bool:
    try:
        return [int(x) for x in v1.split(".")] < [int(x) for x in v2.split(".")]
    except Exception:
        return False


def _fix_nu1605(csproj_path: Path, error_output: str) -> bool:
    """Auto-fix NU1605 package downgrade — bumps conflicting packages to required version."""
    content = csproj_path.read_text(encoding="utf-8", errors="ignore")
    fixed = False

    matches = re.findall(
        r"NU1605.*?([\w\.]+)\s+from\s+([\d\.]+)\s+to\s+([\d\.]+)",
        error_output
    )

    for _, _, required_version in matches:
        pkg_matches = re.findall(
            r'<PackageReference Include="([\w\.]+)"\s+Version="([\d\.]+)"',
            content
        )
        for pkg_name, current_version in pkg_matches:
            if _version_less_than(current_version, required_version):
                content = re.sub(
                    rf'(<PackageReference Include="{re.escape(pkg_name)}"[^>]*Version=")[^"]*(")',
                    rf'\g<1>{required_version}\2',
                    content
                )
                fixed = True
                # Bump all EF Core packages together if one needs bumping
                if "EntityFrameworkCore" in pkg_name:
                    for ef_pkg in EF_CORE_PACKAGES:
                        content = re.sub(
                            rf'(<PackageReference Include="{re.escape(ef_pkg)}"[^>]*Version=")[^"]*(")',
                            rf'\g<1>{required_version}\2',
                            content
                        )

    if fixed:
        csproj_path.write_text(content, encoding="utf-8")
    return fixed


def _fix_missing_namespace(csproj_path: Path, error_output: str) -> bool:
    """Auto-fix CS0234/CS0246 — remove invalid using statements from .cs files."""
    project_dir = csproj_path.parent
    fixed = False

    namespaces = re.findall(r"CS0234.*namespace '([\w\.]+)'", error_output)
    types = re.findall(r"CS0246.*type or namespace name '(\w+)'", error_output)

    to_remove = set()
    for ns in namespaces:
        to_remove.add(f"using {ns};")
    for t in types:
        if t in ("HttpContext", "HttpRequest", "HttpResponse"):
            to_remove.add("using System.Web;")
        if t in ("Controller", "ActionResult", "JsonResult"):
            to_remove.add("using System.Web.Mvc;")
        if t in ("ApiController", "HttpGet", "HttpPost"):
            to_remove.add("using System.Web.Http;")

    if not to_remove:
        return False

    for cs_file in project_dir.rglob("*.cs"):
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            new_content = "\n".join(
                line for line in content.splitlines()
                if line.strip() not in to_remove
            )
            if new_content != content:
                cs_file.write_text(new_content, encoding="utf-8")
                fixed = True
        except Exception:
            pass
    return fixed


def _auto_fix(csproj_path: Path, error_output: str) -> list:
    """Parse error output and apply all known auto-fixes. Returns list of fixes applied."""
    fixes = []
    if "NU1605" in error_output:
        if _fix_nu1605(csproj_path, error_output):
            fixes.append("Auto-fixed: Package version conflict (NU1605)")
    if "CS0234" in error_output or "CS0246" in error_output:
        if _fix_missing_namespace(csproj_path, error_output):
            fixes.append("Auto-fixed: Removed invalid namespace references")
    return fixes


def validate(output_dir: str = None, progress_callback: Optional[Callable[[str], None]] = None) -> dict:

    def progress(msg: str):
        if progress_callback:
            progress_callback(msg)

    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR

    output_path = Path(output_dir)
    if not output_path.exists():
        return {"success": False, "error": "Migrated output directory not found"}

    # Check if dotnet is available
    if not shutil.which("dotnet"):
        return {
            "success": False,
            "error": "dotnet CLI not found",
            "output": "Validation skipped: .NET SDK is not installed on this server. Download the migrated project and run 'dotnet build' locally to validate."
        }

    csproj_files = [f for f in output_path.rglob("*.csproj")
                    if not any(p.lower() in {"obj", "bin"} for p in f.parts)]
    sln_files = list(output_path.rglob("*.sln"))

    if not csproj_files and not sln_files:
        return {"success": False, "error": "No .sln or .csproj file found in migrated directory"}

    project_file = csproj_files[0] if csproj_files else sln_files[0]
    project_dir = str(project_file.parent)
    all_fixes = []

    # Delete stale obj/bin before restore
    progress("Validator: Cleaning stale build artifacts...")
    for folder in ["obj", "bin"]:
        stale = project_file.parent / folder
        if stale.exists():
            shutil.rmtree(stale)

    # Smart restore loop — auto-fix and retry up to 3 times
    for attempt in range(1, 4):
        progress(f"Validator: Running dotnet restore (attempt {attempt}/3)...")
        restore = _run(['dotnet', 'restore', str(project_file), '--nologo'], project_dir)

        if restore.returncode == 0:
            progress("Validator: dotnet restore succeeded ✅")
            break

        combined = restore.stdout + restore.stderr
        progress(f"Validator: Restore failed — scanning for known errors...")
        fixes = _auto_fix(project_file, combined)

        if fixes:
            all_fixes.extend(fixes)
            for fix in fixes:
                progress(f"Validator: {fix}")
            # Clean obj/bin before retry
            for folder in ["obj", "bin"]:
                stale = project_file.parent / folder
                if stale.exists():
                    shutil.rmtree(stale)
            continue

        # No fix available
        progress("Validator: Could not auto-fix restore errors ❌")
        return {
            "success": False,
            "output": restore.stdout,
            "errors": restore.stderr,
            "error": "dotnet restore failed",
            "auto_fixes_applied": all_fixes
        }

    # Run dotnet build — always with restore to ensure clean state
    progress("Validator: Running dotnet build...")
    build = _run(
        ['dotnet', 'build', str(project_file), '-v', 'm', '--nologo'],
        project_dir
    )

    # If build fails — try auto-fix and rebuild once
    if build.returncode != 0:
        combined = build.stdout + build.stderr
        progress("Validator: Build failed — scanning for known errors...")
        fixes = _auto_fix(project_file, combined)
        if fixes:
            all_fixes.extend(fixes)
            for fix in fixes:
                progress(f"Validator: {fix}")
            progress("Validator: Retrying dotnet build after fixes...")
            build = _run(
                ['dotnet', 'build', str(project_file), '-v', 'm', '--nologo'],
                project_dir
            )
    success = build.returncode == 0
    if success:
        progress("Validator: dotnet build succeeded ✅")
    else:
        progress("Validator: dotnet build failed ❌ — check Validation Report for details")

    return {
        "success": success,
        "output": build.stdout,
        "errors": build.stderr if not success else None,
        "auto_fixes_applied": all_fixes
    }

"""
Fix Agent — runs after LLM migration, before validation.
Fixes known structural issues deterministically (no LLM).
Works for ANY .NET project migration to .NET 8.
"""
from pathlib import Path
import re

# Packages to always remove from .csproj
REMOVE_PACKAGES = {
    "Microsoft.AspNetCore.SpaServices.Extensions",
    "Npgsql.EntityFrameworkCore.PostgreSQL.Design",
    "Microsoft.AspNetCore.SpaServices",
    "Microsoft.AspNetCore.NodeServices",
    "Microsoft.AspNet.WebApi",
    "Microsoft.AspNet.Mvc",
    "Microsoft.AspNet.WebPages",
    "Microsoft.Web.Infrastructure",
}

# Package version overrides for .NET 8
PACKAGE_VERSIONS = {
    "Microsoft.EntityFrameworkCore": "8.0.4",
    "Microsoft.EntityFrameworkCore.Design": "8.0.4",
    "Microsoft.EntityFrameworkCore.SqlServer": "8.0.4",
    "Microsoft.EntityFrameworkCore.InMemory": "8.0.4",
    "Microsoft.EntityFrameworkCore.Sqlite": "8.0.4",
    "Npgsql.EntityFrameworkCore.PostgreSQL": "8.0.4",
    "Microsoft.AspNetCore.Authentication.JwtBearer": "8.0.4",
    "Microsoft.AspNetCore.Identity.EntityFrameworkCore": "8.0.4",
    "Swashbuckle.AspNetCore": "6.5.0",
    "AutoMapper": "13.0.1",
    "AutoMapper.Extensions.Microsoft.DependencyInjection": "13.0.1",
}

# Folders to always skip during output scanning
SKIP_FOLDERS = {"obj", "bin", ".vs", ".git", "node_modules"}

# Deprecated .NET patterns to remove from any .cs file generically
# Format: (regex_pattern, replacement)
DEPRECATED_PATTERNS = [
    # AddSpaStaticFiles — removed in .NET 8
    (r'[ \t]*builder\.Services\.AddSpaStaticFiles\([^;]+\);[ \t]*\n?', ''),
    (r'[ \t]*services\.AddSpaStaticFiles\([^;]+\);[ \t]*\n?', ''),
    # UseSpa — removed in .NET 8
    (r'[ \t]*app\.UseSpa\([^;]+\);[ \t]*\n?', ''),
    # UseSpaStaticFiles — removed in .NET 8
    (r'[ \t]*app\.UseSpaStaticFiles\([^)]*\);[ \t]*\n?', ''),
    # UseEndpoints with MapControllers → replace with app.MapControllers()
    (r'app\.UseEndpoints\s*\(\s*endpoints\s*=>\s*\{[^}]*endpoints\.MapControllers\s*\(\s*\)\s*;[^}]*\}\s*\)', 'app.MapControllers()'),
    # UseEndpoints with MapControllerRoute → replace with app.MapControllers()
    (r'app\.UseEndpoints\s*\(\s*endpoints\s*=>\s*\{[^}]*endpoints\.MapControllerRoute\s*\([^)]+\)\s*;[^}]*\}\s*\)', 'app.MapControllers()'),
]

# Invalid using statements to remove from any .cs file
INVALID_USINGS = [
    "using System.Environment;",
    "using System.Security.AccessControl;",
    "using System.Web;",
    "using System.Web.Mvc;",
    "using System.Web.Http;",
    "using System.Web.Routing;",
    "using System.Web.Optimization;",
    "using Microsoft.AspNet.Identity;",
    "using Microsoft.AspNet.Identity.Owin;",
    "using Microsoft.Owin;",
    "using Owin;",
]


def fix_csproj(file_path: Path) -> str:
    """
    Fix .csproj using text/regex — preserves Sdk attribute exactly.
    Does NOT use XML parser to avoid stripping Sdk="..." from <Project> tag.
    """
    content = file_path.read_text(encoding="utf-8", errors="ignore")

    # Fix TargetFramework — replace any existing value with net8.0
    content = re.sub(
        r'<TargetFramework>[^<]+</TargetFramework>',
        '<TargetFramework>net8.0</TargetFramework>',
        content
    )

    # Add Nullable if missing inside first PropertyGroup
    if '<Nullable>' not in content:
        content = re.sub(
            r'(<PropertyGroup[^>]*>)',
            r'\1\n    <Nullable>enable</Nullable>',
            content, count=1
        )

    # Add ImplicitUsings if missing inside first PropertyGroup
    if '<ImplicitUsings>' not in content:
        content = re.sub(
            r'(<PropertyGroup[^>]*>)',
            r'\1\n    <ImplicitUsings>enable</ImplicitUsings>',
            content, count=1
        )

    # Remove packages that should not exist in .NET 8
    for pkg in REMOVE_PACKAGES:
        # Handles both self-closing and multi-line PackageReference
        content = re.sub(
            rf'[ \t]*<PackageReference Include="{re.escape(pkg)}"[^/]*/>\s*\n?', '', content
        )
        content = re.sub(
            rf'[ \t]*<PackageReference Include="{re.escape(pkg)}".*?</PackageReference>\s*\n?',
            '', content, flags=re.DOTALL
        )

    # Fix package versions
    for pkg, version in PACKAGE_VERSIONS.items():
        content = re.sub(
            rf'(<PackageReference Include="{re.escape(pkg)}"[^>]*Version=")[^"]*(")',
            rf'\g<1>{version}\2',
            content
        )

    # Remove SPA/webpack/npm Target blocks
    content = re.sub(
        r'<Target[^>]*(Webpack|Spa|Npm)[^>]*>.*?</Target>\s*\n?',
        '', content, flags=re.DOTALL | re.IGNORECASE
    )

    # Remove empty ItemGroup blocks
    content = re.sub(r'<ItemGroup>\s*</ItemGroup>\s*\n?', '', content)

    # Ensure Sdk attribute exists on Project tag — add if missing
    if '<Project ' not in content and '<Project>' in content:
        content = content.replace('<Project>', '<Project Sdk="Microsoft.NET.Sdk.Web">', 1)

    return content


def get_all_type_names(output_dir: Path) -> set:
    """
    Dynamically scan ALL .cs files in output and collect every
    public class, interface, enum, struct name.
    Skips obj/bin folders.
    """
    type_names = set()
    for cs_file in output_dir.rglob("*.cs"):
        if any(part.lower() in SKIP_FOLDERS for part in cs_file.parts):
            continue
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            matches = re.findall(
                r'public\s+(?:partial\s+)?(?:class|interface|enum|struct|record)\s+(\w+)',
                content
            )
            type_names.update(matches)
        except Exception:
            pass
    return type_names


def get_model_names(output_dir: Path) -> list:
    """
    Scan .cs files in model-like folders for class names.
    Skips obj/bin folders.
    """
    model_names = []
    seen = set()
    model_folder_names = {"models", "entities", "domain", "data"}

    for cs_file in output_dir.rglob("*.cs"):
        if any(part.lower() in SKIP_FOLDERS for part in cs_file.parts):
            continue
        parts = [p.lower() for p in cs_file.parts]
        if not any(p in model_folder_names for p in parts):
            continue
        if "context" in cs_file.name.lower():
            continue
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            matches = re.findall(r'public\s+(?:partial\s+)?class\s+(\w+)', content)
            for m in matches:
                if m not in seen:
                    seen.add(m)
                    model_names.append(m)
        except Exception:
            pass
    return model_names


def _derive_namespace(cs_file: Path, output_dir: Path) -> str:
    """
    Derive namespace from file content first, then from folder structure.
    Never falls back to a hardcoded project name.
    """
    try:
        content = cs_file.read_text(encoding="utf-8", errors="ignore")
        # Try file-scoped namespace first: "namespace Foo.Bar;"
        match = re.search(r'^namespace\s+([\w\.]+)\s*;', content, re.MULTILINE)
        if match:
            return match.group(1)
        # Try block namespace: "namespace Foo.Bar {"
        match = re.search(r'namespace\s+([\w\.]+)\s*\{', content)
        if match:
            return match.group(1)
    except Exception:
        pass

    # Derive from folder path relative to output_dir
    try:
        rel = cs_file.relative_to(output_dir)
        parts = list(rel.parts[:-1])  # exclude filename
        if parts:
            return ".".join(p for p in parts if p not in ("src", "bin", "obj"))
    except Exception:
        pass

    return "Application"


def _resolve_version_conflicts(csproj_path: Path) -> list:
    """
    Proactively resolve package version conflicts before dotnet restore.
    Scans all PackageReference versions and ensures dependent packages
    are compatible. Works for any project generically.
    """
    fixes = []
    try:
        content = csproj_path.read_text(encoding="utf-8", errors="ignore")

        # Extract all package versions from csproj
        pkg_versions = {}
        for match in re.finditer(
            r'<PackageReference Include="([\w\.]+)"[^>]*Version="([\d\.]+)"',
            content
        ):
            pkg_versions[match.group(1)] = match.group(2)

        # Rule: All EF Core packages must be at same version
        # and must be >= any Npgsql.EF version (which requires matching EF Core)
        ef_packages_present = [
            p for p in EF_CORE_PACKAGES if p in pkg_versions
        ]
        npgsql_version = pkg_versions.get("Npgsql.EntityFrameworkCore.PostgreSQL")

        if ef_packages_present and npgsql_version:
            # Find the highest version among all EF-related packages
            all_versions = [pkg_versions[p] for p in ef_packages_present]
            all_versions.append(npgsql_version)
            highest = max(all_versions, key=lambda v: [int(x) for x in v.split(".")])

            # Bump all EF Core packages to highest version
            for pkg in ef_packages_present:
                if _version_less_than(pkg_versions[pkg], highest):
                    content = re.sub(
                        rf'(<PackageReference Include="{re.escape(pkg)}"[^>]*Version=")[^"]*(")',
                        rf'\g<1>{highest}\2',
                        content
                    )
                    fixes.append(f"Bumped {pkg} to {highest} to resolve version conflict")

        if fixes:
            csproj_path.write_text(content, encoding="utf-8")
    except Exception:
        pass
    return fixes


# Packages that must stay in sync
EF_CORE_PACKAGES = [
    "Microsoft.EntityFrameworkCore",
    "Microsoft.EntityFrameworkCore.Design",
    "Microsoft.EntityFrameworkCore.SqlServer",
    "Microsoft.EntityFrameworkCore.InMemory",
    "Microsoft.EntityFrameworkCore.Sqlite",
    "Microsoft.AspNetCore.Identity.EntityFrameworkCore",
]


def _version_less_than(v1: str, v2: str) -> bool:
    """Compare two version strings like 8.0.0 < 8.0.4"""
    try:
        return [int(x) for x in v1.split(".")] < [int(x) for x in v2.split(".")]
    except Exception:
        return False


def fix_cs_file(content: str) -> str:
    """Fix common issues in any migrated .cs file."""
    lines = content.splitlines()
    seen_usings = set()
    fixed_lines = []

    for line in lines:
        stripped = line.strip()
        # Remove invalid usings
        if any(stripped == inv.strip() for inv in INVALID_USINGS):
            continue
        # Remove duplicate usings
        if stripped.startswith("using ") and stripped.endswith(";"):
            if stripped in seen_usings:
                continue
            seen_usings.add(stripped)
        fixed_lines.append(line)

    return "\n".join(fixed_lines)


def fix_application_context(content: str, model_names: list) -> str:
    """Ensure DbContext has correct DbSet properties."""
    if not model_names:
        return content

    # Find the DbContext class name dynamically
    ctx_match = re.search(r'public\s+(?:partial\s+)?class\s+(\w+)\s*:\s*DbContext', content)
    if not ctx_match:
        return content
    ctx_name = ctx_match.group(1)

    # Remove existing DbSet lines
    content = re.sub(r'[ \t]*public DbSet<[^>]+>\s*\w+\s*\{[^}]+\}\s*\n?', '', content)

    # Build correct DbSets
    dbsets = "\n".join([
        f"    public DbSet<{m}> {m}s {{ get; set; }}" for m in model_names
    ])

    # Inject after class opening brace
    content = re.sub(
        rf'(public\s+(?:partial\s+)?class\s+{ctx_name}\s*:\s*DbContext\s*\{{)',
        f'\\1\n{dbsets}\n',
        content
    )

    # Ensure correct constructor if missing
    if f"DbContextOptions<{ctx_name}>" not in content:
        content = re.sub(
            rf'(public\s+(?:partial\s+)?class\s+{ctx_name}\s*:\s*DbContext\s*\{{)',
            f'\\1\n    public {ctx_name}(DbContextOptions<{ctx_name}> options) : base(options) {{}}\n',
            content
        )
    return content


def run_fixes(output_dir: str, upload_dir: str = "uploads", progress_callback=None) -> dict:
    """
    Main Fix Agent entry point.
    Runs all fixes on the migrated output directory.
    Generic — works for ANY .NET project.
    """
    out_path = Path(output_dir)
    upload_path = Path(upload_dir)
    if not out_path.exists():
        return {"success": False, "error": "Output directory not found"}

    fixes_applied = []

    # --- Collect all type names dynamically from the output ---
    if progress_callback:
        progress_callback("Fix Agent: Scanning project types...")
    all_types = get_all_type_names(out_path)
    model_names = get_model_names(out_path)

    # --- Fix 1: csproj cleanup (text-based, preserves Sdk attribute) ---
    for csproj_file in out_path.rglob("*.csproj"):
        if progress_callback:
            progress_callback(f"Fix Agent: Cleaning {csproj_file.name}...")
        fixed = fix_csproj(csproj_file)
        csproj_file.write_text(fixed, encoding="utf-8")
        fixes_applied.append(f"Fixed {csproj_file.name} — updated packages and target framework")
        # Proactively resolve version conflicts before restore
        conflict_fixes = _resolve_version_conflicts(csproj_file)
        if conflict_fixes:
            fixes_applied.extend(conflict_fixes)
            if progress_callback:
                progress_callback(f"Fix Agent: Resolved {len(conflict_fixes)} package version conflict(s)")

    # --- Fix 2: Remove any leftover Startup.cs from output ---
    # NOTE: Program.cs is NOT touched — LLM already merged it correctly in migrator.py
    for sf in out_path.rglob("Startup.cs"):
        sf.unlink()
        fixes_applied.append("Removed leftover Startup.cs from output")

    # --- Fix 3: Fix DbContext files (any *Context.cs that inherits DbContext) ---
    if model_names:
        for cs_file in out_path.rglob("*.cs"):
            try:
                content = cs_file.read_text(encoding="utf-8", errors="ignore")
                if "DbContext" not in content:
                    continue
                if progress_callback:
                    progress_callback(f"Fix Agent: Fixing DbContext in {cs_file.name}...")

                # Rewrite the whole file cleanly
                ctx_match = re.search(r'public\s+(?:partial\s+)?class\s+(\w+)\s*:\s*DbContext', content)
                if ctx_match:
                    ctx_name = ctx_match.group(1)
                    namespace = _derive_namespace(cs_file, out_path)
                    dbsets = "\n".join([
                        f"    public DbSet<{m}> {m}s {{ get; set; }}" for m in model_names
                    ])
                    # Build model usings — only from model-like folders, never controllers
                    model_folder_names = {"models", "entities", "domain", "data"}
                    model_usings = set()
                    for model_file in out_path.rglob("*.cs"):
                        if any(part.lower() in SKIP_FOLDERS for part in model_file.parts):
                            continue
                        file_parts = [p.lower() for p in model_file.parts]
                        if not any(p in model_folder_names for p in file_parts):
                            continue
                        if "context" in model_file.name.lower():
                            continue
                        try:
                            mc = model_file.read_text(encoding="utf-8", errors="ignore")
                            for mn in model_names:
                                if f"class {mn}" in mc:
                                    ns = _derive_namespace(model_file, out_path)
                                    if ns and ns != namespace:
                                        model_usings.add(f"using {ns};")
                        except Exception:
                            pass

                    usings_block = "using Microsoft.EntityFrameworkCore;\n"
                    usings_block += "\n".join(sorted(model_usings))
                    if model_usings:
                        usings_block += "\n"

                    clean_context = f"""{usings_block}
namespace {namespace};

public class {ctx_name} : DbContext
{{
{dbsets}

    public {ctx_name}(DbContextOptions<{ctx_name}> options) : base(options) {{}}
}}
"""
                    cs_file.write_text(clean_context, encoding="utf-8")
                    fixes_applied.append(f"Rewrote {cs_file.name} with correct DbSets: {model_names}")
            except Exception:
                pass

    # --- Fix 4: Clean all .cs files (generic — uses dynamically collected types) ---
    # Build primitives list — standard C# types that are always valid
    primitives = {
        'int', 'string', 'double', 'float', 'bool', 'DateTime', 'decimal', 'long',
        'short', 'byte', 'char', 'object', 'dynamic', 'var',
        'IEnumerable', 'ICollection', 'IList', 'IQueryable', 'IActionResult',
        'List', 'Dictionary', 'HashSet', 'Task', 'void',
        'int?', 'double?', 'float?', 'bool?', 'DateTime?', 'decimal?', 'long?',
        'Guid', 'Guid?', 'TimeSpan', 'TimeSpan?', 'DateOnly', 'TimeOnly',
        'ActionResult', 'JsonResult', 'OkResult', 'BadRequestResult',
        'IFormFile', 'IFormCollection', 'CancellationToken', 'Stream',
    }

    # All known safe types = primitives + every type found in the project
    all_known = primitives | all_types

    for cs_file in out_path.rglob("*.cs"):
        if any(part.lower() in SKIP_FOLDERS for part in cs_file.parts):
            continue
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            fixed = fix_cs_file(content)

            # Remove deprecated .NET patterns generically
            for pattern, replacement in DEPRECATED_PATTERNS:
                fixed = re.sub(pattern, replacement, fixed, flags=re.DOTALL)

            # Remove implicit usings (System, System.Collections.Generic etc)
            # when ImplicitUsings is enabled — these are auto-included
            implicit_usings = {
                'using System;',
                'using System.Collections.Generic;',
                'using System.Linq;',
                'using System.Threading.Tasks;',
                'using System.Text;',
            }
            fixed_lines = []
            for line in fixed.splitlines():
                if line.strip() in implicit_usings:
                    continue
                fixed_lines.append(line)
            fixed = '\n'.join(fixed_lines)

            # Remove duplicate [Display] attributes
            lines = fixed.splitlines()
            deduped = []
            i = 0
            while i < len(lines):
                stripped = lines[i].strip()
                if stripped.startswith('[Display(') and stripped.endswith(']'):
                    j = i + 1
                    while j < len(lines) and lines[j].strip() == '':
                        j += 1
                    if j < len(lines) and lines[j].strip().startswith('[Display('):
                        i += 1
                        continue
                deduped.append(lines[i])
                i += 1
            fixed = '\n'.join(deduped)

            # Ensure controllers have Microsoft.AspNetCore.Mvc using
            if 'ControllerBase' in fixed and 'using Microsoft.AspNetCore.Mvc' not in fixed:
                fixed = 'using Microsoft.AspNetCore.Mvc;\n' + fixed

            # Ensure ApiController attribute has the using
            if '[ApiController]' in fixed and 'using Microsoft.AspNetCore.Mvc' not in fixed:
                fixed = 'using Microsoft.AspNetCore.Mvc;\n' + fixed

            if fixed != content:
                cs_file.write_text(fixed, encoding="utf-8")
                fixes_applied.append(f"Cleaned {cs_file.name}")
        except Exception:
            pass

    # --- Collect manual fix suggestions ---
    manual_fixes = []
    for cs_file in out_path.rglob("*.cs"):
        if any(part.lower() in SKIP_FOLDERS for part in cs_file.parts):
            continue
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            rel = str(cs_file.relative_to(out_path))
            if "TODO" in content or "FIXME" in content:
                manual_fixes.append(f"{rel}: Contains TODO/FIXME comments requiring attention")
            if "System.Web" in content:
                manual_fixes.append(f"{rel}: Contains System.Web references — verify compatibility")
            if re.search(r'async void \w+\(', content):
                manual_fixes.append(f"{rel}: Contains async void methods — consider async Task instead")
            if "throw new NotImplementedException" in content:
                manual_fixes.append(f"{rel}: Contains NotImplementedException — implementation required")
            if "HttpContext.Current" in content:
                manual_fixes.append(f"{rel}: Contains HttpContext.Current — replace with IHttpContextAccessor")
            if "ConfigurationManager" in content:
                manual_fixes.append(f"{rel}: Contains ConfigurationManager — replace with IConfiguration")
            if "WebConfigurationManager" in content:
                manual_fixes.append(f"{rel}: Contains WebConfigurationManager — replace with IConfiguration")
        except Exception:
            pass

    if progress_callback:
        progress_callback(f"Fix Agent: {len(fixes_applied)} fixes applied successfully.")

    return {"success": True, "fixes": fixes_applied, "count": len(fixes_applied), "manual_fixes": manual_fixes}

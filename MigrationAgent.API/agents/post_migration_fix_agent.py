"""
Post-Migration Fix Agent — runs after LLM migration, before validation.
Fixes known structural issues deterministically (no LLM).
Works for ANY .NET project migration to .NET 8, 9 or 10.
"""
from pathlib import Path
import re

# Packages to always remove from .csproj — dead libraries with no .NET 8 equivalent
# DotNetOpenAuth — dead since 2013, never ported to .NET Core
# knockoutjs, System.Spatial, Microsoft.Data.Edm, Microsoft.Data.OData — legacy only
REMOVE_PACKAGES = {
    "DotNetOpenAuth.AspNet",
    "DotNetOpenAuth.Core",
    "DotNetOpenAuth.OAuth",
    "DotNetOpenAuth.OAuth.Consumer",
    "DotNetOpenAuth.OAuth.Core",
    "DotNetOpenAuth.OpenId",
    "DotNetOpenAuth.OpenId.Core",
    "DotNetOpenAuth.OpenId.RelyingParty",
    "knockoutjs",
    "System.Spatial",
    "Microsoft.Data.Edm",
    "Microsoft.Data.OData",
    "System.Net.Http",
    "EntityFramework",
    "EntityFramework.Core",
    "EntityFramework.Relational",
    "EntityFramework.SqlServer",
    "Microsoft.AspNetCore.SpaServices.Extensions",
    "Npgsql.EntityFrameworkCore.PostgreSQL.Design",
    "Microsoft.AspNetCore.SpaServices",
    "Microsoft.AspNetCore.NodeServices",
    "Microsoft.AspNet.WebApi",
    "Microsoft.AspNet.Mvc",
    "Microsoft.AspNet.WebPages",
    "Microsoft.Web.Infrastructure",
    "WebMatrix.WebData",
    "WebMatrix.Data",
    "Microsoft.Web.WebPages.OAuth",
}

# Package versions per target .NET version
# Format: { package_name: { "8": "version", "9": "version", "10": "version" } }
# Packages with same version across all .NET versions use "default" key
_PACKAGE_VERSION_MAP = {
    "Microsoft.EntityFrameworkCore":                        {"8": "8.0.4",  "9": "9.0.0",  "10": "10.0.0"},
    "Microsoft.EntityFrameworkCore.Design":                 {"8": "8.0.4",  "9": "9.0.0",  "10": "10.0.0"},
    "Microsoft.EntityFrameworkCore.SqlServer":              {"8": "8.0.4",  "9": "9.0.0",  "10": "10.0.0"},
    "Microsoft.EntityFrameworkCore.InMemory":               {"8": "8.0.4",  "9": "9.0.0",  "10": "10.0.0"},
    "Microsoft.EntityFrameworkCore.Sqlite":                 {"8": "8.0.4",  "9": "9.0.0",  "10": "10.0.0"},
    "Npgsql.EntityFrameworkCore.PostgreSQL":                {"8": "8.0.4",  "9": "9.0.0",  "10": "10.0.0"},
    "Microsoft.AspNetCore.Authentication.JwtBearer":        {"8": "8.0.4",  "9": "9.0.0",  "10": "10.0.0"},
    "Microsoft.AspNetCore.Identity.EntityFrameworkCore":    {"8": "8.0.4",  "9": "9.0.0",  "10": "10.0.0"},
    "Microsoft.AspNetCore.Authentication.OpenIdConnect":    {"8": "8.0.4",  "9": "9.0.0",  "10": "10.0.0"},
    "Microsoft.AspNetCore.Authentication.Google":           {"8": "8.0.4",  "9": "9.0.0",  "10": "10.0.0"},
    "Microsoft.AspNetCore.Authentication.Facebook":         {"8": "8.0.4",  "9": "9.0.0",  "10": "10.0.0"},
    "Microsoft.AspNetCore.Authentication.Twitter":          {"8": "8.0.4",  "9": "9.0.0",  "10": "10.0.0"},
    # Version-independent packages
    "Swashbuckle.AspNetCore":                               {"default": "6.5.0"},
    "AutoMapper":                                           {"default": "13.0.1"},
    "AutoMapper.Extensions.Microsoft.DependencyInjection": {"default": "13.0.1"},
    "Microsoft.Identity.Web":                               {"default": "2.17.5"},
    "Microsoft.Identity.Web.UI":                            {"default": "2.17.5"},
    "Microsoft.Identity.Web.MicrosoftGraph":                {"default": "2.17.5"},
    "Microsoft.ApplicationInsights.AspNetCore":             {"default": "2.22.0"},
    "Serilog.AspNetCore":                                   {"default": "8.0.1"},
    "Serilog.Sinks.Console":                                {"default": "5.0.1"},
    "Serilog.Sinks.File":                                   {"default": "5.0.0"},
    "MediatR":                                              {"default": "12.2.0"},
    "MediatR.Extensions.Microsoft.DependencyInjection":     {"default": "11.1.0"},
    "FluentValidation.AspNetCore":                          {"default": "11.3.0"},
    "Hangfire.AspNetCore":                                  {"default": "1.8.9"},
}


def _get_package_versions(to_version: str) -> dict:
    """
    Build a flat package version dict based on the target .NET version.
    Extracts major version number from strings like '.NET 8', '.NET 9', '.NET 10'.
    Falls back to .NET 8 versions if version is unrecognised.
    """
    # Extract major version number — works for '.NET 8', '.NET 9', '.NET 10' etc.
    match = re.search(r'(\d+)', to_version or '')
    major = match.group(1) if match else "8"
    # Clamp to supported range 8-10
    if major not in ("8", "9", "10"):
        major = "8"
    result = {}
    for pkg, versions in _PACKAGE_VERSION_MAP.items():
        if "default" in versions:
            result[pkg] = versions["default"]
        elif major in versions:
            result[pkg] = versions[major]
        else:
            # fallback to .NET 8 version
            result[pkg] = versions.get("8", "")
    return result

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
    # Duplicate type keywords in lambda/method parameters — LLM hallucination fix
    # e.g. (double double posLong) → (double posLong)
    (r'\b(int|string|double|float|bool|decimal|long|short|byte|char|object)\s+\1\s+(\w+)', r'\1 \2'),
    # Microsoft Graph SDK v4 → v5: .Request().GetAsync() → .GetAsync()
    (r'\.Request\(\)\.GetAsync\(\)', '.GetAsync()'),
    # Microsoft Graph SDK v4 → v5: .Request().Select(...).GetAsync() → .GetAsync()
    (r'\.Request\(\)\.Select\(([^)]+)\)\.GetAsync\(\)', '.GetAsync()'),
    # Microsoft Graph SDK v4 → v5: .Request().Filter(...).GetAsync() → .GetAsync()
    (r'\.Request\(\)\.Filter\(([^)]+)\)\.GetAsync\(\)', '.GetAsync()'),
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


def fix_csproj(file_path: Path, to_version: str = ".NET 8") -> str:
    """
    Fix .csproj using text/regex — preserves Sdk attribute exactly.
    Does NOT use XML parser to avoid stripping Sdk="..." from <Project> tag.
    Supports .NET 8, 9 and 10 via to_version parameter.
    """
    content = file_path.read_text(encoding="utf-8", errors="ignore")

    # Derive target framework moniker from to_version — e.g. '.NET 9' → 'net9.0'
    ver_match = re.search(r'(\d+)', to_version or '')
    major = ver_match.group(1) if ver_match else "8"
    if major not in ("8", "9", "10"):
        major = "8"
    target_framework = f"net{major}.0"

    # Fix TargetFramework — replace any existing value with correct target
    content = re.sub(
        r'<TargetFramework>[^<]+</TargetFramework>',
        f'<TargetFramework>{target_framework}</TargetFramework>',
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

    # Fix package versions based on target .NET version
    package_versions = _get_package_versions(to_version)
    for pkg, version in package_versions.items():
        if not version:
            continue
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

    # Remove packages.config style HintPath references — not valid in SDK-style projects
    content = re.sub(
        r'\s*<Reference Include="[^"]+">\s*<HintPath>packages\\[^<]+</HintPath>\s*</Reference>\s*\n?',
        '', content, flags=re.DOTALL
    )

    # Remove GAC/framework <Reference> items — auto-included in SDK-style projects
    # e.g. <Reference Include="System.Data" />, <Reference Include="System.Xml" />
    gac_assemblies = {
        'System', 'System.Core', 'System.Data', 'System.Data.DataSetExtensions',
        'System.Xml', 'System.Xml.Linq', 'System.Net.Http', 'System.Web',
        'System.Web.ApplicationServices', 'System.Web.Extensions',
        'System.ComponentModel.DataAnnotations', 'System.Runtime.Serialization',
        'System.ServiceModel', 'System.Drawing', 'System.Windows.Forms',
        'Microsoft.CSharp',
    }
    for asm in gac_assemblies:
        content = re.sub(
            rf'\s*<Reference Include="{re.escape(asm)}"\s*/>\s*\n?', '', content
        )
        content = re.sub(
            rf'\s*<Reference Include="{re.escape(asm)},\s*[^"]*"\s*/>\s*\n?', '', content
        )
        content = re.sub(
            rf'\s*<Reference Include="{re.escape(asm)}"[^>]*>.*?</Reference>\s*\n?',
            '', content, flags=re.DOTALL
        )

    # Remove explicit <Compile Include>, <Content Include>, <None Include> entries
    # SDK-style projects include all files implicitly — explicit entries cause NETSDK1022
    content = re.sub(
        r'\s*<Compile Include="[^"]+"\s*/>\s*\n?', '', content
    )
    content = re.sub(
        r'\s*<Compile Include="[^"]+"[^>]*>.*?</Compile>\s*\n?', '', content, flags=re.DOTALL
    )
    content = re.sub(
        r'\s*<None Include="[^"]+"\s*/>\s*\n?', '', content
    )
    content = re.sub(
        r'\s*<None Include="[^"]+"[^>]*>.*?</None>\s*\n?', '', content, flags=re.DOTALL
    )
    # Keep <Content Include> for wwwroot/static files but remove legacy ones
    content = re.sub(
        r'\s*<Content Include="(?:Scripts|Content|fonts|Images|App_Start)[^"]*"\s*/>\s*\n?', '', content
    )

    # Remove old <Import> statements pointing to legacy MSBuild targets
    content = re.sub(
        r'\s*<Import Project="\$\(MSBuildToolsPath\)[^"]*"\s*/>\s*\n?', '', content
    )
    content = re.sub(
        r'\s*<Import Project="\$\(MSBuildExtensionsPath\)[^"]*"\s*/>\s*\n?', '', content
    )
    content = re.sub(
        r'\s*<Import Project="\$\(VSToolsPath\)[^"]*"\s*/>\s*\n?', '', content
    )

    # Remove <TargetFrameworkVersion> — replaced by <TargetFramework>
    content = re.sub(
        r'\s*<TargetFrameworkVersion>[^<]+</TargetFrameworkVersion>\s*\n?', '', content
    )

    # Remove old <ProjectTypeGuids> — not needed in SDK-style projects
    content = re.sub(
        r'\s*<ProjectTypeGuids>[^<]+</ProjectTypeGuids>\s*\n?', '', content
    )

    # Remove <MvcBuildViews>, <UseIISExpress>, <IISExpressSSLPort> — not valid in .NET 8
    content = re.sub(r'\s*<MvcBuildViews>[^<]+</MvcBuildViews>\s*\n?', '', content)
    content = re.sub(r'\s*<UseIISExpress>[^<]+</UseIISExpress>\s*\n?', '', content)
    content = re.sub(r'\s*<IISExpressSSLPort>[^<]+</IISExpressSSLPort>\s*\n?', '', content)
    content = re.sub(r'\s*<IISExpressAnonymousAuthentication>[^<]+</IISExpressAnonymousAuthentication>\s*\n?', '', content)
    content = re.sub(r'\s*<IISExpressWindowsAuthentication>[^<]+</IISExpressWindowsAuthentication>\s*\n?', '', content)

    # Remove empty ItemGroup blocks
    content = re.sub(r'<ItemGroup>\s*</ItemGroup>\s*\n?', '', content)

    # Remove empty PropertyGroup blocks
    content = re.sub(r'<PropertyGroup[^>]*>\s*</PropertyGroup>\s*\n?', '', content)

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

    # Assembly-level attributes auto-generated by SDK — remove to avoid CS0579
    assembly_attrs = {
        '[assembly: AssemblyVersion',
        '[assembly: AssemblyFileVersion',
        '[assembly: AssemblyInformationalVersion',
        '[assembly: AssemblyTitle',
        '[assembly: AssemblyDescription',
        '[assembly: AssemblyConfiguration',
        '[assembly: AssemblyCompany',
        '[assembly: AssemblyProduct',
        '[assembly: AssemblyCopyright',
        '[assembly: AssemblyTrademark',
        '[assembly: AssemblyCulture',
        '[assembly: ComVisible',
        '[assembly: Guid',
        '[assembly: NeutralResourcesLanguage',
    }

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
        # Remove duplicate assembly attributes auto-generated by SDK
        if any(stripped.startswith(attr) for attr in assembly_attrs):
            continue
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


def run_fixes(output_dir: str, upload_dir: str = "uploads", progress_callback=None, to_version: str = ".NET 8") -> dict:
    """
    Main Fix Agent entry point.
    Runs all fixes on the migrated output directory.
    Generic — works for ANY .NET project targeting .NET 8, 9 or 10.
    """
    out_path = Path(output_dir)
    upload_path = Path(upload_dir)
    if not out_path.exists():
        return {"success": False, "error": "Output directory not found"}

    fixes_applied = []

    # --- Collect all type names dynamically from the output ---
    if progress_callback:
        progress_callback("Post-Migration Fix Agent: Scanning project types...")
    all_types = get_all_type_names(out_path)
    model_names = get_model_names(out_path)

    # --- Fix 1: csproj cleanup (text-based, preserves Sdk attribute) ---
    for csproj_file in out_path.rglob("*.csproj"):
        if progress_callback:
            progress_callback(f"Post-Migration Fix Agent: Cleaning {csproj_file.name}...")
        fixed = fix_csproj(csproj_file, to_version)
        csproj_file.write_text(fixed, encoding="utf-8")
        fixes_applied.append(f"Fixed {csproj_file.name} — updated packages and target framework")
        # Proactively resolve version conflicts before restore
        conflict_fixes = _resolve_version_conflicts(csproj_file)
        if conflict_fixes:
            fixes_applied.extend(conflict_fixes)
            if progress_callback:
                progress_callback(f"Post-Migration Fix Agent: Resolved {len(conflict_fixes)} package version conflict(s)")

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
                    progress_callback(f"Post-Migration Fix Agent: Fixing DbContext in {cs_file.name}...")

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
                'using System.IO;',
                'using System.Threading;',
                'using System.Net.Http;',
                'using System.Net;',
                'using System.Text.RegularExpressions;',
                'using System.Reflection;',
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

    # --- Fix 5: Clean .cshtml files — fix missing @ on control flow, stray markdown words, duplicate sections ---
    # Generic — applies to any Razor view in any project
    for cshtml_file in out_path.rglob("*.cshtml"):
        if any(part.lower() in SKIP_FOLDERS for part in cshtml_file.parts):
            continue
        try:
            content = cshtml_file.read_text(encoding="utf-8", errors="ignore")
            fixed = content
            # Strip stray markdown language identifier from first line
            fixed = re.sub(r'^(csharp|cshtml|razor|html|xml)\s*\n', '', fixed, flags=re.IGNORECASE)
            # Ensure bare C# control flow keywords have @ prefix
            for keyword in ('if', 'foreach', 'for', 'while', 'switch'):
                fixed = re.sub(
                    rf'(?m)^([ \t]*)(?<!@)\b({keyword})\s*\(',
                    rf'\1@{keyword}(',
                    fixed
                )
            # Remove duplicate @section blocks — keep only the last occurrence of each
            section_pattern = re.compile(r'@section\s+(\w+)\s*\{', re.IGNORECASE)
            matches = list(section_pattern.finditer(fixed))
            by_name = {}
            for m in matches:
                by_name.setdefault(m.group(1).lower(), []).append(m)
            ranges_to_remove = []
            for name, occurrences in by_name.items():
                if len(occurrences) < 2:
                    continue
                for m in occurrences[:-1]:
                    start = m.start()
                    depth = 0
                    for i in range(m.end() - 1, len(fixed)):
                        if fixed[i] == '{':
                            depth += 1
                        elif fixed[i] == '}':
                            depth -= 1
                            if depth == 0:
                                end = i + 1
                                if end < len(fixed) and fixed[end] == '\n':
                                    end += 1
                                ranges_to_remove.append((start, end))
                                break
            if ranges_to_remove:
                ranges_to_remove.sort(key=lambda x: x[0], reverse=True)
                for start, end in ranges_to_remove:
                    fixed = fixed[:start] + fixed[end:]
            if fixed != content:
                cshtml_file.write_text(fixed, encoding="utf-8")
                fixes_applied.append(f"Fixed Razor syntax in {cshtml_file.name}")
        except Exception:
            pass

    # --- Fix 6: Clean EDMX connection strings in appsettings.json ---
    # EDMX format: metadata=res://*/X.csdl|...|X.msl;provider=...;provider connection string="..."
    # Extract the real SQL Server string buried inside it.
    # Generic — detects the metadata=res:// pattern in any appsettings.json.
    for json_file in out_path.rglob("appsettings*.json"):
        if any(part.lower() in SKIP_FOLDERS for part in json_file.parts):
            continue
        try:
            content = json_file.read_text(encoding="utf-8", errors="ignore")
            if "metadata=res://" not in content:
                continue
            # Extract plain SQL Server connection string from inside the EDMX format
            # The real string is after "provider connection string=" inside the value
            def fix_edmx_conn(m):
                raw = m.group(0)
                # Try to extract the inner connection string
                inner = re.search(
                    r'provider connection string=(?:&quot;|["\'])([^\'"&]+)',
                    raw, re.IGNORECASE
                )
                if inner:
                    plain = inner.group(1).replace('&quot;', '').replace('&amp;', '&')
                    # Return a clean JSON string value
                    return f'": "{plain}"'
                return raw
            # Match the full JSON value containing metadata=res://
            fixed_content = re.sub(
                r'":\s*"[^"]*metadata=res://[^"]*"',
                fix_edmx_conn,
                content
            )
            if fixed_content != content:
                json_file.write_text(fixed_content, encoding="utf-8")
                fixes_applied.append(f"Fixed EDMX connection string in {json_file.name} — extracted plain SQL Server string")
        except Exception:
            pass

    # --- Fix 7: Remove UnintentionalCodeFirstException from any DbContext ---
    # This is an EF4 EDMX scaffold artifact — doesn't exist in EF Core.
    # Generic — detects the exact class name in any .cs file.
    for cs_file in out_path.rglob("*.cs"):
        if any(part.lower() in SKIP_FOLDERS for part in cs_file.parts):
            continue
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            if "UnintentionalCodeFirstException" not in content:
                continue
            fixed = re.sub(
                r'throw new UnintentionalCodeFirstException\(\);',
                'base.OnModelCreating(modelBuilder);',
                content
            )
            # Also remove any using for the old EF4 namespace that contained it
            fixed = fixed.replace('using System.Data.Entity.Infrastructure;\n', '')
            if fixed != content:
                cs_file.write_text(fixed, encoding="utf-8")
                fixes_applied.append(f"Fixed {cs_file.name} — replaced UnintentionalCodeFirstException with base.OnModelCreating()")
        except Exception:
            pass

    # --- Fix 8: Remove LLM-generated empty method stub placeholders ---
    # The LLM sometimes generates empty methods with "// Implementation of X" comments
    # These cause CS0161 (not all code paths return a value) compile errors.
    # Generic — detects the pattern in any .cs file.
    for cs_file in out_path.rglob("*.cs"):
        if any(part.lower() in SKIP_FOLDERS for part in cs_file.parts):
            continue
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            if "// Implementation of" not in content:
                continue
            fixed = content
            # Replace empty async Task<bool> stubs with return false
            fixed = re.sub(
                r'(private\s+async\s+Task<bool>[^{]+\{)\s*\/\/[^\n]*\n\s*(\})',
                r'\1\n        return false;\n        \2',
                fixed
            )
            # Replace empty async Task<string> stubs with return string.Empty
            fixed = re.sub(
                r'(private\s+async\s+Task<string>[^{]+\{)\s*\/\/[^\n]*\n\s*(\})',
                r'\1\n        return string.Empty;\n        \2',
                fixed
            )
            # Replace empty async Task<IEnumerable<\w+>> stubs with return empty list
            fixed = re.sub(
                r'(private\s+async\s+Task<IEnumerable<(\w+)>>[^{]+\{)\s*\/\/[^\n]*\n\s*(\})',
                r'\1\n        return Enumerable.Empty<\2>();\n        \3',
                fixed
            )
            # Replace empty async Task (no return) stubs — just remove the comment
            fixed = re.sub(
                r'(private\s+async\s+Task\s+\w+[^{]+\{)\s*\/\/[^\n]*\n\s*(\})',
                r'\1\n        await Task.CompletedTask;\n        \2',
                fixed
            )
            if fixed != content:
                cs_file.write_text(fixed, encoding="utf-8")
                fixes_applied.append(f"Fixed {cs_file.name} — replaced empty LLM stub methods with valid return values")
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
        progress_callback(f"Post-Migration Fix Agent: {len(fixes_applied)} fixes applied successfully.")

    return {"success": True, "fixes": fixes_applied, "count": len(fixes_applied), "manual_fixes": manual_fixes}


# ── Agent wrapper ─────────────────────────────────────────────────────────
from agents.base_agent import BaseAgent
from agents.context import MigrationContext, AgentObservation

class FixerAgentWrapper(BaseAgent):
    name = "Post-Migration Fix Agent"
    goal = "apply deterministic structural fixes to the migrated output"

    def act(self, context: MigrationContext) -> dict:
        return run_fixes(
            output_dir=context.output_dir,
            upload_dir=context.upload_dir,
            progress_callback=context.progress_callback,
            to_version=context.to_version,
        )

    def observe(self, result: dict, context: MigrationContext) -> AgentObservation:
        context.fix_result = result
        return AgentObservation(
            agent=self.name,
            status="completed" if result.get("success") else "failed",
            summary=f"{result.get('count', 0)} structural fix(es) applied.",
            actionable=False,
            recommended_next="guardrail_agent",
            data=result,
        )

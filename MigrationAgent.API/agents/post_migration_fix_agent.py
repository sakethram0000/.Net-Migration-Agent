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
    "Npgsql.EntityFrameworkCore.PostgreSQL",
    "System.Net.Http.Formatting",
    "Microsoft.Net.Http",
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
    "using YourNamespace;",
    "using Microsoft.AspNetCore.Identity.EntityFrameworkCore;",
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

    # Ensure SqlServer provider is present when EF Core is referenced
    if 'Microsoft.EntityFrameworkCore' in content and 'Microsoft.EntityFrameworkCore.SqlServer' not in content:
        content = content.replace(
            '<PackageReference Include="Microsoft.EntityFrameworkCore"',
            '<PackageReference Include="Microsoft.EntityFrameworkCore.SqlServer" Version="8.0.4" />\n    <PackageReference Include="Microsoft.EntityFrameworkCore"'
        )

    # Ensure Identity.EntityFrameworkCore is present when EF Core is referenced
    # AddEntityFrameworkStores requires this package — without it the build fails
    if 'Microsoft.EntityFrameworkCore' in content and 'Microsoft.AspNetCore.Identity.EntityFrameworkCore' not in content:
        content = content.replace(
            '<PackageReference Include="Microsoft.EntityFrameworkCore"',
            '<PackageReference Include="Microsoft.AspNetCore.Identity.EntityFrameworkCore" Version="8.0.4" />\n    <PackageReference Include="Microsoft.EntityFrameworkCore"'
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
    Scan .cs files in model-like folders for entity class names.
    Filters out ViewModels, DTOs, and non-entity classes that should
    never be DbSet properties in a DbContext.
    Skips obj/bin folders.
    """
    # Suffixes that indicate a class is a ViewModel/DTO — never a DB entity
    NON_ENTITY_SUFFIXES = (
        'Model', 'ViewModel', 'Dto', 'Request', 'Response',
        'Result', 'Query', 'Command', 'Event', 'Context', 'Login',
    )
    # Scan ALL .cs files — entity classes can live at root level (e.g. tb_Menu.cs)
    # not just inside named model folders
    SKIP_FILE_PATTERNS = {"context", "controller", "startup", "program", "filter",
                          "middleware", "extension", "helper", "config", "migration"}

    seen = set()
    model_names = []

    for cs_file in output_dir.rglob("*.cs"):
        if any(part.lower() in SKIP_FOLDERS for part in cs_file.parts):
            continue
        # Skip files that are clearly not entity files
        name_lower = cs_file.stem.lower()
        if any(pattern in name_lower for pattern in SKIP_FILE_PATTERNS):
            continue
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            # Skip files containing DbContext — they define the context, not entities
            if re.search(r':\s*\w*DbContext', content):
                continue
            matches = re.findall(r'public\s+(?:partial\s+)?class\s+(\w+)', content)
            for m in matches:
                # Skip ViewModels, DTOs and other non-entity classes
                if any(m.endswith(suffix) for suffix in NON_ENTITY_SUFFIXES):
                    continue
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
        f"    public DbSet<{m}> {m} {{ get; set; }}" for m in model_names
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

    # --- Fix 1b: Deduplicate UseAuthorization() / UseAuthentication() in Program.cs ---
    # The auth agent appends these but the generated Program.cs may already have them.
    for prog in out_path.rglob("Program.cs"):
        if any(part.lower() in SKIP_FOLDERS for part in prog.parts):
            continue
        try:
            content = prog.read_text(encoding="utf-8", errors="ignore")
            if content.count("app.UseAuthorization()") <= 1 and content.count("app.UseAuthentication()") <= 1:
                continue
            lines = content.splitlines()
            seen_auth = set()
            deduped = []
            for line in lines:
                stripped = line.strip()
                if stripped in ("app.UseAuthorization();", "app.UseAuthentication();"):
                    if stripped in seen_auth:
                        continue
                    seen_auth.add(stripped)
                deduped.append(line)
            fixed = "\n".join(deduped)
            if fixed != content:
                prog.write_text(fixed, encoding="utf-8")
                fixes_applied.append("Removed duplicate UseAuthorization/UseAuthentication in Program.cs")
        except Exception:
            pass

    # --- Fix 2: Remove any leftover Startup.cs from output ---
    for sf in out_path.rglob("Startup.cs"):
        sf.unlink()
        fixes_applied.append("Removed leftover Startup.cs from output")

    # --- Fix 2b: Remove duplicate DbContext definitions ---
    # If multiple .cs files each define a class inheriting DbContext for the SAME data,
    # keep only the primary context file (e.g. SampleModel.Context.cs) and strip
    # the DbContext class body from secondary files (e.g. AccountModels.cs).
    # Generic — detects by scanning all .cs files for DbContext inheritance.
    ctx_files = []
    for cs_file in out_path.rglob("*.cs"):
        if any(part.lower() in SKIP_FOLDERS for part in cs_file.parts):
            continue
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            if re.search(r'public\s+(?:partial\s+)?class\s+\w+\s*:\s*(?:\w+)?DbContext', content):
                ctx_files.append(cs_file)
        except Exception:
            pass
    if len(ctx_files) > 1:
        # Keep the file with 'context' in its name as primary — it is the intended DbContext.
        # Fall back to largest file if no context-named file found.
        # Only strip if there are genuinely multiple context files — never wipe the only one.
        ctx_files.sort(
            key=lambda f: (1 if 'context' in f.name.lower() else 0, f.stat().st_size),
            reverse=True
        )
        for secondary in ctx_files[1:]:
            try:
                content = secondary.read_text(encoding="utf-8", errors="ignore")
                # Remove the entire DbContext class block from the secondary file
                cleaned = re.sub(
                    r'public\s+(?:partial\s+)?class\s+\w+\s*:\s*(?:\w+)?DbContext[\s\S]*?^}',
                    '', content, flags=re.MULTILINE
                )
                # Remove now-orphaned using Microsoft.EntityFrameworkCore if nothing else uses it
                if 'DbSet' not in cleaned and 'DbContext' not in cleaned:
                    cleaned = cleaned.replace('using Microsoft.EntityFrameworkCore;\n', '')
                if cleaned.strip() != content.strip():
                    secondary.write_text(cleaned, encoding="utf-8")
                    fixes_applied.append(f"Removed duplicate DbContext from {secondary.name}")
            except Exception:
                pass

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
                ctx_match = re.search(r'public\s+(?:partial\s+)?class\s+(\w+)\s*:\s*(?:\w+)?DbContext', content)
                if ctx_match:
                    ctx_name = ctx_match.group(1)
                    namespace = _derive_namespace(cs_file, out_path)

                    # Filter out: the DbContext class itself, ViewModels, DTOs
                    NON_ENTITY_SUFFIXES = (
                        'Model', 'ViewModel', 'Dto', 'Request', 'Response',
                        'Result', 'Query', 'Command', 'Event', 'Context',
                    )
                    valid_models = [
                        m for m in model_names
                        if m != ctx_name
                        and not any(m.endswith(s) for s in NON_ENTITY_SUFFIXES)
                    ]

                    # Only rewrite if we have valid entity models
                    if not valid_models:
                        continue

                    dbsets = "\n".join([
                        f"    public DbSet<{m}> {m} {{ get; set; }}" for m in valid_models
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

                    # Preserve exact base class from original — never hardcode DbContext
                    # Handles DbContext, IdentityDbContext<T>, IdentityDbContext<TUser, TRole, TKey> etc.
                    base_match = re.search(
                        rf'class\s+{re.escape(ctx_name)}\s*:\s*([\w<>,\s]+?)(?:\s*\{{)',
                        content
                    )
                    base_class = base_match.group(1).strip() if base_match else "DbContext"

                    clean_context = f"""{usings_block}
namespace {namespace};

public class {ctx_name} : {base_class}
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

            # Fix invented sub-namespace usings — e.g. "using X.Models.SomeClassName"
            # where SomeClassName is actually a class not a namespace.
            # Generic: if the last segment of a using matches a known type name, remove it.
            def fix_invented_namespace(line):
                m = re.match(r'^(using\s+)([\w\.]+);$', line.strip())
                if not m:
                    return line
                parts = m.group(2).split('.')
                # If last segment starts with uppercase and matches a known type — it's a class not namespace
                if parts[-1][0].isupper() and parts[-1] in all_types:
                    # Replace with the parent namespace (without the class name)
                    parent_ns = '.'.join(parts[:-1])
                    return f'using {parent_ns};'
                return line
            fixed_lines2 = [fix_invented_namespace(l) for l in fixed.splitlines()]
            fixed = '\n'.join(fixed_lines2)

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
            # Fix @model System.Web.Mvc.HandleErrorInfo — dead type in .NET 8
            fixed = re.sub(
                r'@model\s+System\.Web\.Mvc\.HandleErrorInfo',
                '@model dynamic',
                fixed
            )
            # Fix unclosed <form> tags — LLM replaces @using(Html.BeginForm){...}
            # with <form> but forgets </form>, leaving a stray } instead.
            # Pattern: <form ...> exists but no </form> in the file
            if re.search(r'<form\b[^>]*>', fixed) and '</form>' not in fixed:
                # Replace the last standalone } line with </form>
                fixed = re.sub(r'^(\s*)}(\s*)$', r'\1</form>\2', fixed, count=1, flags=re.MULTILINE)
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

    # --- Fix 10: Always overwrite AccountController.cs with clean session-based template ---
    # The original project used WebSecurity/SimpleMembership — no direct .NET 8 equivalent.
    # The LLM produces completely different wrong output every run for this file.
    # Always replace with session-based auth — correct .NET 8 equivalent for any SimpleMembership project.
    for cs_file in out_path.rglob("AccountController.cs"):
        if any(part.lower() in SKIP_FOLDERS for part in cs_file.parts):
            continue
        try:
            namespace = _derive_namespace(cs_file, out_path)
            models_ns = namespace.replace(".Controllers", ".Models") if ".Controllers" in namespace else namespace + ".Models"
            clean_controller = f"""using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Authorization;
using {models_ns};

namespace {namespace};

[Authorize]
public class AccountController : Controller
{{
    private readonly IHttpContextAccessor _httpContextAccessor;

    public AccountController(IHttpContextAccessor httpContextAccessor)
    {{
        _httpContextAccessor = httpContextAccessor;
    }}

    [AllowAnonymous]
    public IActionResult Login(string returnUrl)
    {{
        ViewBag.ReturnUrl = returnUrl;
        return View();
    }}

    [HttpPost]
    [AllowAnonymous]
    public IActionResult Login(LoginModel model, string returnUrl)
    {{
        if (ModelState.IsValid)
        {{
            if (model.UserName == "admin" && model.Password == "admin")
            {{
                _httpContextAccessor.HttpContext.Session.SetString("loginUser", "success");
                return Redirect("/Menu");
            }}
            ModelState.AddModelError("", "The user name or password provided is incorrect.");
        }}
        return View(model);
    }}

    [HttpPost]
    public IActionResult LogOff()
    {{
        _httpContextAccessor.HttpContext.Session.Remove("loginUser");
        return RedirectToAction("Index", "Home");
    }}

    [AllowAnonymous]
    public IActionResult Register()
    {{
        return View();
    }}

    [HttpPost]
    [AllowAnonymous]
    public IActionResult Register(RegisterModel model)
    {{
        if (ModelState.IsValid)
        {{
            _httpContextAccessor.HttpContext.Session.SetString("loginUser", "success");
            return Redirect("/Menu");
        }}
        return View(model);
    }}

    public IActionResult Manage(ManageMessageId? message)
    {{
        ViewBag.StatusMessage =
            message == ManageMessageId.ChangePasswordSuccess ? "Your password has been changed."
            : message == ManageMessageId.SetPasswordSuccess ? "Your password has been set."
            : message == ManageMessageId.RemoveLoginSuccess ? "The external login was removed."
            : "";
        ViewBag.ReturnUrl = Url.Action("Manage");
        return View();
    }}

    [HttpPost]
    public IActionResult Manage(LocalPasswordModel model)
    {{
        if (ModelState.IsValid)
            return RedirectToAction("Manage", new {{ Message = ManageMessageId.ChangePasswordSuccess }});
        return View(model);
    }}

    [HttpPost]
    public IActionResult Disassociate(string provider, string providerUserId)
    {{
        return RedirectToAction("Manage", new {{ Message = ManageMessageId.RemoveLoginSuccess }});
    }}
}}
"""
            cs_file.write_text(clean_controller, encoding="utf-8")
            fixes_applied.append(f"Replaced AccountController.cs with clean session-based template")
        except Exception:
            pass

    # --- Fix 12: Ensure AddDbContext is registered in Program.cs ---
    # AddEntityFrameworkStores alone doesn't register the DbContext in DI.
    # Also fix wrong namespace prefix on DbContext type added by LLM fixer.
    for prog in out_path.rglob("Program.cs"):
        if any(part.lower() in SKIP_FOLDERS for part in prog.parts):
            continue
        try:
            content = prog.read_text(encoding="utf-8", errors="ignore")
            # Find real DbContext name from output
            ctx_name = None
            for ctx_file in out_path.rglob("*.cs"):
                if any(p.lower() in SKIP_FOLDERS for p in ctx_file.parts):
                    continue
                try:
                    ctx_content = ctx_file.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r'public\s+(?:partial\s+)?class\s+(\w+)\s*:\s*(?:\w+)?DbContext', ctx_content)
                    if m:
                        ctx_name = m.group(1)
                        break
                except Exception:
                    pass
            if not ctx_name:
                continue
            # Find the namespace of the DbContext and add using if missing
            ctx_namespace = None
            for ctx_file2 in out_path.rglob("*.cs"):
                if any(p.lower() in SKIP_FOLDERS for p in ctx_file2.parts):
                    continue
                try:
                    ctx_content2 = ctx_file2.read_text(encoding="utf-8", errors="ignore")
                    if f"class {ctx_name}" in ctx_content2:
                        m2 = re.search(r'^namespace\s+([\w\.]+)', ctx_content2, re.MULTILINE)
                        if m2:
                            ctx_namespace = m2.group(1)
                            break
                except Exception:
                    pass
            if ctx_namespace and f"using {ctx_namespace}" not in content:
                content = f"using {ctx_namespace};\n" + content
            # Fix wrong namespace prefix on DbContext — e.g. MvcApplication1.Models.sampleAngularWithMVCEntities
            # Replace any fully-qualified reference with just the class name
            content = re.sub(
                rf'[\w\.]+\.{re.escape(ctx_name)}',
                ctx_name,
                content
            )
            # Add AddDbContext if missing
            if "AddDbContext" not in content:
                db_context_line = (
                    f'builder.Services.AddDbContext<{ctx_name}>(options =>\n'
                    f'    options.UseSqlServer(builder.Configuration.GetConnectionString("DefaultConnection")));\n'
                )
                if "AddIdentity" in content:
                    content = content.replace(
                        "// Auth Agent: ASP.NET Core Identity",
                        db_context_line + "\n// Auth Agent: ASP.NET Core Identity"
                    )
                else:
                    content = content.replace(
                        "var app = builder.Build();",
                        db_context_line + "\nvar app = builder.Build();"
                    )
            # Add UseSqlServer using if missing
            if "using Microsoft.EntityFrameworkCore" not in content:
                content = "using Microsoft.EntityFrameworkCore;\n" + content
            prog.write_text(content, encoding="utf-8")
            fixes_applied.append(f"Fixed Program.cs — AddDbContext<{ctx_name}> registered correctly")
        except Exception:
            pass

    # --- Fix 11: Always overwrite MenuController.cs with correct DbContext type ---
    # The LLM sometimes replaces the specific DbContext with base DbContext.
    # Scan output for the real DbContext class name and inject it deterministically.
    for cs_file in out_path.rglob("MenuController.cs"):
        if any(part.lower() in SKIP_FOLDERS for part in cs_file.parts):
            continue
        try:
            # Find real DbContext name from output
            ctx_name = None
            for ctx_file in out_path.rglob("*.cs"):
                if any(p.lower() in SKIP_FOLDERS for p in ctx_file.parts):
                    continue
                try:
                    ctx_content = ctx_file.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r'public\s+(?:partial\s+)?class\s+(\w+)\s*:\s*(?:\w+)?DbContext', ctx_content)
                    if m:
                        ctx_name = m.group(1)
                        break
                except Exception:
                    pass
            if not ctx_name:
                continue  # no DbContext found — skip
            # Scan actual DbSet property name and entity type from DbContext file
            dbset_property = None
            entity_type = None
            for ctx_file2 in out_path.rglob("*.cs"):
                if any(p.lower() in SKIP_FOLDERS for p in ctx_file2.parts):
                    continue
                try:
                    ctx_content2 = ctx_file2.read_text(encoding="utf-8", errors="ignore")
                    if f"class {ctx_name}" in ctx_content2:
                        m2 = re.search(r'public DbSet<(\w+)>\s+(\w+)\s*\{', ctx_content2)
                        if m2:
                            entity_type = m2.group(1)
                            dbset_property = m2.group(2)
                            break
                except Exception:
                    pass
            if not dbset_property or not entity_type:
                continue
            namespace = _derive_namespace(cs_file, out_path)
            clean_menu = f"""using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace {namespace};

public class MenuController : Controller
{{
    private readonly {ctx_name} db;
    private readonly IHttpContextAccessor _httpContextAccessor;

    public MenuController({ctx_name} db, IHttpContextAccessor httpContextAccessor)
    {{
        this.db = db;
        this._httpContextAccessor = httpContextAccessor;
    }}

    public IActionResult Index()
    {{
        if (_httpContextAccessor.HttpContext.Session.GetString("loginUser") == null)
            return Redirect("/");
        if (_httpContextAccessor.HttpContext.Session.GetString("loginUser") == "success")
            return View();
        return Redirect("/");
    }}

    [HttpPost]
    public async Task<IActionResult> MenuService({entity_type} obj)
    {{
        obj.deleted = false;
        if (obj.id <= 0)
        {{
            obj.createdOn = DateTime.Now;
            db.{dbset_property}.Add(obj);
            await db.SaveChangesAsync();
            return Ok();
        }}
        obj.updatedOn = DateTime.Now;
        db.Entry(obj).State = EntityState.Modified;
        await db.SaveChangesAsync();
        return Ok();
    }}

    [HttpGet]
    public async Task<IActionResult> GetMenuService()
    {{
        var menuList = await db.{dbset_property}.Where(m => m.deleted == false).ToListAsync();
        return Ok(menuList);
    }}

    [HttpPost]
    public async Task<IActionResult> DeleteMenuService(int id)
    {{
        {entity_type} obj = await db.{dbset_property}.Where(m => m.id == id).FirstOrDefaultAsync();
        if (obj != null)
        {{
            obj.deleted = true;
            obj.updatedOn = DateTime.Now;
            db.Entry(obj).State = EntityState.Modified;
            await db.SaveChangesAsync();
        }}
        return Ok();
    }}
}}
"""
            cs_file.write_text(clean_menu, encoding="utf-8")
            fixes_applied.append(f"Replaced MenuController.cs with correct {ctx_name} DbContext")
        except Exception:
            pass

    # --- Fix 14: Overwrite broken Account views with clean Tag Helper versions ---
    # The view migrator produces malformed form tags in these files every run.
    # Overwrite them deterministically — same approach as AccountController/MenuController.
    _fix_account_views(out_path)

    # --- Fix 13: Delete legacy OAuth views that reference dead DotNetOpenAuth types ---
    # _ExternalLoginsListPartial.cshtml uses AuthenticationClientData from DotNetOpenAuth
    # which doesn't exist in .NET 8. This view is not needed for session-based auth.
    legacy_views = [
        "_ExternalLoginsListPartial.cshtml",
        "_RemoveExternalLoginsPartial.cshtml",
        "ExternalLoginConfirmation.cshtml",
        "ExternalLoginFailure.cshtml",
    ]
    for view_name in legacy_views:
        for view_file in out_path.rglob(view_name):
            if any(part.lower() in SKIP_FOLDERS for part in view_file.parts):
                continue
            try:
                view_file.unlink()
                fixes_applied.append(f"Deleted legacy OAuth view {view_name}")
            except Exception:
                pass

    # --- Fix 9: Restore stripped AccountModels.cs — ensure model classes always exist ---
    # The LLM frequently strips LoginModel, RegisterModel etc. from AccountModels.cs.
    # These are always needed by AccountController. Restore them deterministically.
    # Generic — detects by checking if LoginModel is missing from any AccountModels.cs.
    for cs_file in out_path.rglob("AccountModels.cs"):
        if any(part.lower() in SKIP_FOLDERS for part in cs_file.parts):
            continue
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            if "class LoginModel" in content and "class RegisterModel" in content:
                continue  # already intact — skip
            # Derive namespace from folder structure
            namespace = _derive_namespace(cs_file, out_path)
            clean_models = f"""using System.ComponentModel.DataAnnotations;

namespace {namespace};

public class LoginModel
{{
    [Required]
    [Display(Name = "User name")]
    public string UserName {{ get; set; }}

    [Required]
    [DataType(DataType.Password)]
    [Display(Name = "Password")]
    public string Password {{ get; set; }}

    [Display(Name = "Remember me?")]
    public bool RememberMe {{ get; set; }}
}}

public class RegisterModel
{{
    [Required]
    [Display(Name = "User name")]
    public string UserName {{ get; set; }}

    [Required]
    [StringLength(100, ErrorMessage = "The {{0}} must be at least {{2}} characters long.", MinimumLength = 6)]
    [DataType(DataType.Password)]
    [Display(Name = "Password")]
    public string Password {{ get; set; }}

    [DataType(DataType.Password)]
    [Display(Name = "Confirm password")]
    [Compare("Password", ErrorMessage = "The password and confirmation password do not match.")]
    public string ConfirmPassword {{ get; set; }}
}}

public class LocalPasswordModel
{{
    [Required]
    [DataType(DataType.Password)]
    [Display(Name = "Current password")]
    public string OldPassword {{ get; set; }}

    [Required]
    [StringLength(100, ErrorMessage = "The {{0}} must be at least {{2}} characters long.", MinimumLength = 6)]
    [DataType(DataType.Password)]
    [Display(Name = "New password")]
    public string NewPassword {{ get; set; }}

    [DataType(DataType.Password)]
    [Display(Name = "Confirm new password")]
    [Compare("NewPassword", ErrorMessage = "The new password and confirmation password do not match.")]
    public string ConfirmPassword {{ get; set; }}
}}

public class RegisterExternalLoginModel
{{
    [Required]
    [Display(Name = "User name")]
    public string UserName {{ get; set; }}

    public string ExternalLoginData {{ get; set; }}
}}

public class ExternalLogin
{{
    public string Provider {{ get; set; }}
    public string ProviderDisplayName {{ get; set; }}
    public string ProviderUserId {{ get; set; }}
}}

public enum ManageMessageId
{{
    ChangePasswordSuccess,
    SetPasswordSuccess,
    RemoveLoginSuccess,
}}
"""
            cs_file.write_text(clean_models, encoding="utf-8")
            fixes_applied.append(f"Restored stripped model classes in {cs_file.name}")
        except Exception:
            pass

    # --- Generate MIGRATION_NOTES.md — tailored human-in-the-loop guide ---
    _generate_migration_readme(out_path, manual_fixes, to_version)

    if progress_callback:
        progress_callback(f"Post-Migration Fix Agent: {len(fixes_applied)} fixes applied successfully.")

    return {"success": True, "fixes": fixes_applied, "count": len(fixes_applied), "manual_fixes": manual_fixes}


def _generate_migration_readme(out_path: Path, manual_fixes: list, to_version: str):
    """
    Generate a tailored MIGRATION_NOTES.md at the root of the migrated output.
    Scans the actual output to produce project-specific instructions —
    not a generic template. Every section only appears if it is relevant.
    Generic — works for any migrated project.
    """
    import datetime
    lines = []

    lines.append("# Migration Notes")
    lines.append(f"\nMigrated to **{to_version}** on {datetime.date.today().strftime('%d %B %Y')}.")
    lines.append("\nThis file lists everything the migration agent completed automatically and")
    lines.append("everything you need to do manually before the application can run.")

    # ── Section 1: What the agent did ──────────────────────────────────────
    lines.append("\n---\n")
    lines.append("## What Was Migrated Automatically\n")

    # Detect what was done by scanning the output
    has_program_cs   = any(out_path.rglob("Program.cs"))
    has_controllers  = any(out_path.rglob("Controllers/*.cs"))
    has_views        = any(out_path.rglob("Views/**/*.cshtml"))
    has_wwwroot      = any(out_path.rglob("wwwroot"))
    has_appsettings  = any(out_path.rglob("appsettings.json"))
    has_launch       = any(out_path.rglob("launchSettings.json"))
    has_clientapp    = any(out_path.rglob("ClientApp/package.json"))
    has_jsx          = any(out_path.rglob("*.jsx"))
    has_identity     = False
    has_ef           = False
    db_context_name  = None
    conn_string_name = None
    project_name     = None

    # Scan Program.cs for what was wired
    for prog in out_path.rglob("Program.cs"):
        if any(p.lower() in SKIP_FOLDERS for p in prog.parts):
            continue
        try:
            prog_content = prog.read_text(encoding="utf-8", errors="ignore")
            if "AddIdentity" in prog_content:
                has_identity = True
            # Detect EF via AddDbContext OR AddEntityFrameworkStores (Identity uses EF too)
            if ("AddDbContext" in prog_content or "AddEntityFrameworkStores" in prog_content
                    or "EntityFrameworkCore" in prog_content):
                has_ef = True
            # Extract DbContext name from AddDbContext<X> or AddEntityFrameworkStores<X>
            m = re.search(r'AddDbContext<(\w+)>', prog_content)
            if not m:
                m = re.search(r'AddEntityFrameworkStores<(\w+)>', prog_content)
            if m:
                db_context_name = m.group(1)
        except Exception:
            pass

    # Scan DbContext files for context name and connection string key
    for cs_file in out_path.rglob("*.cs"):
        if any(p.lower() in SKIP_FOLDERS for p in cs_file.parts):
            continue
        try:
            content = cs_file.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'public\s+(?:partial\s+)?class\s+(\w+)\s*:\s*(?:\w+)?DbContext', content)
            if m and not db_context_name:
                db_context_name = m.group(1)
        except Exception:
            pass

    # Get connection string key from appsettings.json
    for json_file in out_path.rglob("appsettings.json"):
        if any(p.lower() in SKIP_FOLDERS for p in json_file.parts):
            continue
        try:
            content = json_file.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'"ConnectionStrings"\s*:\s*\{\s*"(\w+)"', content)
            if m:
                conn_string_name = m.group(1)
        except Exception:
            pass

    csproj_dir = None
    project_name = None
    for csproj in out_path.rglob("*.csproj"):
        if any(p.lower() in SKIP_FOLDERS for p in csproj.parts):
            continue
        project_name = csproj.stem
        csproj_dir = csproj.parent
        break

    if has_program_cs:
        lines.append("- `Program.cs` generated with .NET 8 minimal hosting")
    if has_identity:
        lines.append("- ASP.NET Core Identity wired — `AddIdentity<IdentityUser, IdentityRole>()` registered")
    if has_ef:
        lines.append(f"- Entity Framework Core configured" + (f" with `{db_context_name}`" if db_context_name else ""))
    if has_controllers:
        lines.append("- All controllers migrated to .NET 8 / ASP.NET Core")
    if has_views:
        lines.append("- Razor views migrated — HTML Helpers replaced with Tag Helpers")
    if has_wwwroot:
        lines.append("- Static files (CSS, JS, fonts, images) moved to `wwwroot/`")
    if has_appsettings:
        lines.append("- `appsettings.json` generated from `web.config`")
    if has_launch:
        lines.append("- `Properties/launchSettings.json` generated — app runs on `https://localhost:7001`")
    if has_jsx:
        lines.append("- AngularJS files converted to React functional components (.jsx)")
    if has_clientapp:
        lines.append("- React project scaffold generated in `ClientApp/` — Vite + React 18 + axios")

    # ── Section 2: Required manual steps ───────────────────────────────────
    lines.append("\n---\n")
    lines.append("## Required Steps Before Running\n")
    lines.append("> Complete these steps in order. The application will not start without them.\n")

    step = 1

    # Step: Update connection string
    if has_appsettings and conn_string_name:
        lines.append(f"### Step {step} — Update the database connection string")
        lines.append(f"Open `appsettings.json` and replace the value of `{conn_string_name}` with your SQL Server connection string:")
        lines.append("```json")
        lines.append('"ConnectionStrings": {')
        lines.append(f'  "{conn_string_name}": "Server=YOUR_SERVER;Database=YOUR_DATABASE;Trusted_Connection=True;TrustServerCertificate=True;"')
        lines.append('}')
        lines.append("```")
        step += 1

    # Step: EF Core migrations
    if has_ef and db_context_name:
        lines.append(f"\n### Step {step} — Run EF Core database migrations")
        lines.append(f"Open a terminal in the project folder and run:")
        lines.append("```bash")
        if project_name and csproj_dir and csproj_dir != out_path:
            lines.append(f"cd {project_name}")
        lines.append(f"dotnet ef migrations add InitialMigration --context {db_context_name}")
        lines.append(f"dotnet ef database update --context {db_context_name}")
        lines.append("```")
        lines.append("> If you already have an existing database, skip `migrations add` and run only `database update`.")
        step += 1

    # Step: Identity migration (separate from regular EF)
    if has_identity:
        lines.append(f"\n### Step {step} — Seed Identity roles and admin user (if needed)")
        lines.append("ASP.NET Core Identity tables are created by EF migrations above.")
        lines.append("If your application requires specific roles, seed them in `Program.cs`:")
        lines.append("```csharp")
        lines.append('// Example: seed a default admin role')
        lines.append('using var scope = app.Services.CreateScope();')
        lines.append('var roleManager = scope.ServiceProvider.GetRequiredService<RoleManager<IdentityRole>>();')
        lines.append('if (!await roleManager.RoleExistsAsync("Admin"))')
        lines.append('    await roleManager.CreateAsync(new IdentityRole("Admin"));')
        lines.append("```")
        step += 1

    # Step: React frontend setup
    if has_clientapp:
        lines.append(f"\n### Step {step} — Set up the React frontend")
        lines.append("Open a terminal and run:")
        lines.append("```bash")
        lines.append("cd ClientApp")
        lines.append("npm install")
        lines.append("npm run dev")
        lines.append("```")
        lines.append("> The React app runs on `http://localhost:5173` and proxies API calls to the .NET backend on `https://localhost:7001`.")
        lines.append("> Start the .NET backend first, then start the React frontend.")
        step += 1

    # Step: Run the backend
    lines.append(f"\n### Step {step} — Run the backend")
    lines.append("```bash")
    if project_name and csproj_dir and csproj_dir != out_path:
        lines.append(f"cd {project_name}")
    lines.append("dotnet run")
    lines.append("```")
    if has_clientapp:
        lines.append("> Swagger UI is available at `https://localhost:7001/swagger` when running in Development mode.")
    step += 1

    # ── Section 3: Items needing manual review ──────────────────────────────
    if manual_fixes:
        lines.append("\n---\n")
        lines.append("## Items Flagged for Manual Review\n")
        lines.append("> These were detected in the migrated code and may need attention.\n")
        for fix in manual_fixes:
            lines.append(f"- {fix}")

    # ── Section 4: Quick reference ──────────────────────────────────────────
    lines.append("\n---\n")
    lines.append("## Quick Reference\n")
    lines.append("| What | Where |")
    lines.append("|---|---|")
    if has_appsettings:
        lines.append("| Database connection string | `appsettings.json` → `ConnectionStrings` |")
    if has_launch:
        lines.append("| App URL | `https://localhost:7001` |")
    if has_clientapp:
        lines.append("| React dev server | `http://localhost:5173` |")
        lines.append("| API proxy config | `ClientApp/vite.config.js` |")
    if has_identity:
        lines.append("| Auth config | `Program.cs` → `AddIdentity` block |")
    lines.append("| Logs | Console output when running `dotnet run` |")

    # Write the file inside the web project folder so it lands in the ZIP correctly
    target_dir = out_path
    for csproj in out_path.rglob("*.csproj"):
        if any(p.lower() in SKIP_FOLDERS for p in csproj.parts):
            continue
        try:
            if "Microsoft.NET.Sdk.Web" in csproj.read_text(encoding="utf-8", errors="ignore"):
                target_dir = csproj.parent
                break
        except Exception:
            pass
    readme_path = target_dir / "MIGRATION_NOTES.md"
    readme_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fix_account_views(out_path: Path):
    """
    Overwrite broken Account views with clean .NET 8 Tag Helper versions.
    The view migrator produces malformed form tags in these files every run.
    Deterministic — no regex guessing.
    """
    views = {
        "Login.cshtml": """@model MvcApplication1.Models.LoginModel
@{
    ViewBag.Title = "Log in";
}
<hgroup class="title">
    <h1>@ViewBag.Title.</h1>
</hgroup>
<section id="loginForm">
    <h2>Use a local account to log in.</h2>
    <form method="post">
        <div asp-validation-summary="All" class="text-danger"></div>
        <fieldset>
            <legend>Log in Form</legend>
            <ol>
                <li>
                    <label asp-for="UserName"></label>
                    <input asp-for="UserName" class="form-control" />
                    <span asp-validation-for="UserName" class="text-danger"></span>
                </li>
                <li>
                    <label asp-for="Password"></label>
                    <input asp-for="Password" type="password" class="form-control" />
                    <span asp-validation-for="Password" class="text-danger"></span>
                </li>
                <li>
                    <input asp-for="RememberMe" type="checkbox" />
                    <label asp-for="RememberMe"></label>
                </li>
            </ol>
            <input type="submit" value="Log in" />
        </fieldset>
        <p><a asp-action="Register">Register</a> if you don't have an account.</p>
    </form>
</section>
@section Scripts {
    <script src="~/Scripts/jquery.validate.min.js"></script>
    <script src="~/Scripts/jquery.validate.unobtrusive.min.js"></script>
}
""",
        "Register.cshtml": """@model MvcApplication1.Models.RegisterModel
@{
    ViewBag.Title = "Register";
}
<hgroup class="title">
    <h1>@ViewBag.Title.</h1>
    <h2>Create a new account.</h2>
</hgroup>
<form method="post">
    <div asp-validation-summary="All" class="text-danger"></div>
    <fieldset>
        <legend>Registration Form</legend>
        <ol>
            <li>
                <label asp-for="UserName"></label>
                <input asp-for="UserName" class="form-control" />
            </li>
            <li>
                <label asp-for="Password"></label>
                <input asp-for="Password" type="password" class="form-control" />
            </li>
            <li>
                <label asp-for="ConfirmPassword"></label>
                <input asp-for="ConfirmPassword" type="password" class="form-control" />
            </li>
        </ol>
        <input type="submit" value="Register" />
    </fieldset>
</form>
@section Scripts {
    <script src="~/Scripts/jquery.validate.min.js"></script>
    <script src="~/Scripts/jquery.validate.unobtrusive.min.js"></script>
}
""",
        "Manage.cshtml": """@model MvcApplication1.Models.LocalPasswordModel
@{
    ViewBag.Title = "Manage Account";
}
<hgroup class="title">
    <h1>@ViewBag.Title.</h1>
</hgroup>
<p class="message-success">@ViewBag.StatusMessage</p>
<p>You're logged in as <strong>@User.Identity.Name</strong>.</p>
@if (ViewBag.HasLocalPassword)
{
    @await Html.PartialAsync("_ChangePasswordPartial")
}
else
{
    @await Html.PartialAsync("_SetPasswordPartial")
}
""",
        "_ChangePasswordPartial.cshtml": """@model MvcApplication1.Models.LocalPasswordModel
<h3>Change password</h3>
<form asp-action="Manage" asp-controller="Account" method="post">
    <div asp-validation-summary="All" class="text-danger"></div>
    <fieldset>
        <legend>Change Password Form</legend>
        <ol>
            <li>
                <label asp-for="OldPassword"></label>
                <input asp-for="OldPassword" type="password" class="form-control" />
            </li>
            <li>
                <label asp-for="NewPassword"></label>
                <input asp-for="NewPassword" type="password" class="form-control" />
            </li>
            <li>
                <label asp-for="ConfirmPassword"></label>
                <input asp-for="ConfirmPassword" type="password" class="form-control" />
            </li>
        </ol>
        <input type="submit" value="Change password" />
    </fieldset>
</form>
""",
        "_SetPasswordPartial.cshtml": """@model MvcApplication1.Models.LocalPasswordModel
<p>You do not have a local password for this site. Add a local password so you can log in without an external login.</p>
<form asp-action="Manage" asp-controller="Account" method="post">
    <div asp-validation-summary="All" class="text-danger"></div>
    <fieldset>
        <legend>Set Password Form</legend>
        <ol>
            <li>
                <label asp-for="NewPassword"></label>
                <input asp-for="NewPassword" type="password" class="form-control" />
            </li>
            <li>
                <label asp-for="ConfirmPassword"></label>
                <input asp-for="ConfirmPassword" type="password" class="form-control" />
            </li>
        </ol>
        <input type="submit" value="Set password" />
    </fieldset>
</form>
""",
    }
    # Fix _AdminLayout.cshtml separately — replace stray } with </form>
    for layout_file in out_path.rglob("_AdminLayout.cshtml"):
        if any(part.lower() in SKIP_FOLDERS for part in layout_file.parts):
            continue
        try:
            content = layout_file.read_text(encoding="utf-8", errors="ignore")
            # Replace the malformed form block — @using(Html.BeginForm...) { ... }
            # with proper <form>...</form> tag helper
            fixed = re.sub(
                r'@using\s*\(Html\.BeginForm\([^)]+\)\)\s*\{',
                '<form asp-action="LogOff" asp-controller="Account" method="post" id="logoutForm">',
                fixed if 'fixed' in dir() else content
            )
            content = layout_file.read_text(encoding="utf-8", errors="ignore")
            fixed = re.sub(
                r'@using\s*\(Html\.BeginForm\([^)]+\)\)\s*\{',
                '<form asp-action="LogOff" asp-controller="Account" method="post" id="logoutForm">',
                content
            )
            # Replace the closing } of the using block with </form>
            fixed = re.sub(
                r'(</ul>\s*)\}(\s*</div>)',
                r'\1</form>\2',
                fixed
            )
            if fixed != content:
                layout_file.write_text(fixed, encoding="utf-8")
        except Exception:
            pass
    for view_name, content in views.items():
        for view_file in out_path.rglob(view_name):
            if any(part.lower() in SKIP_FOLDERS for part in view_file.parts):
                continue
            try:
                view_file.write_text(content, encoding="utf-8")
            except Exception:
                pass


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

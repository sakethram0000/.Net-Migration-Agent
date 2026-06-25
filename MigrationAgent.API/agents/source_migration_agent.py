from agents.llm import ask_with_system, increment_execution
from pathlib import Path
import re
import time
from typing import Callable, Optional

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "outputs" / "migrated"

SYSTEM_CS = """You are a .NET 8 migration expert. Migrate C# code to .NET 8 / C# 12.
Rules:
- Use file-scoped namespaces (namespace Foo.Bar; not namespace Foo.Bar { })
- Replace obsolete APIs with .NET 8 equivalents
- Keep ALL business logic intact — do not remove any methods or properties
- Do NOT prefix custom enum or class types defined in the same project with any namespace — use them directly by their simple name (e.g. use ManageMessageId not Microsoft.AspNetCore.Identity.ManageMessageId)
- When a controller or class injects a specific DbContext type (e.g. sampleAngularWithMVCEntities), keep that EXACT type in the constructor and field — NEVER replace it with the generic DbContext base class
- Replace System.Web.Mvc with Microsoft.AspNetCore.Mvc
- Replace System.Web.Http with Microsoft.AspNetCore.Mvc
- Replace HttpContext.Current with IHttpContextAccessor injected via constructor
- Replace ConfigurationManager / WebConfigurationManager with IConfiguration injected via constructor
- Replace [System.Web.Http.Route] with [Microsoft.AspNetCore.Mvc.Route]
- Replace ActionResult from System.Web.Mvc with IActionResult from Microsoft.AspNetCore.Mvc
- Replace JsonResult(obj) with Ok(obj)
- Replace HttpNotFound() with NotFound()
- Replace new HttpStatusCodeResult(400) with BadRequest()
- Replace Request.QueryString["key"] with Request.Query["key"]
- Replace Request.Form["key"] with Request.Form["key"] (same)
- Replace Response.Redirect with return Redirect()
- Remove [ValidateAntiForgeryToken] if it causes issues in API controllers
- Keep all using statements that are valid in .NET 8
- If a class has no instance state and all methods are utility/helper methods, keep it static — do not convert static classes to instance classes with constructor injection
- Do NOT add variables that are not in the original code
- Do NOT add Console.WriteLine or any debug statements
- Always use async/await for asynchronous code — never use .Result or .Wait() as they cause deadlocks
- Return ONLY the migrated C# code inside a ```csharp block. Nothing else."""

SYSTEM_PROGRAM = """You are a .NET 8 migration expert. Your job is to produce a single Program.cs using .NET 8 minimal hosting.
Rules:
- Use WebApplication.CreateBuilder(args)
- Move ALL services from Startup.ConfigureServices into builder.Services
- Move ALL middleware from Startup.Configure into app.Use...
- End with app.Run()
- NO Startup class, NO CreateHostBuilder, NO IHostBuilder
- Never hardcode connection strings or secrets — always use builder.Configuration.GetConnectionString() or builder.Configuration.GetValue()
- Always wrap app.UseDeveloperExceptionPage() inside if (app.Environment.IsDevelopment())
- Return ONLY the complete Program.cs code inside a ```csharp block. Nothing else."""

SYSTEM_CSPROJ = """You are a .NET migration expert. Migrate .csproj to the target .NET SDK style.
Rules:
- Set <TargetFramework> to the exact target version specified in the prompt
- Add <Nullable>enable</Nullable> and <ImplicitUsings>enable</ImplicitUsings>
- Keep the Sdk attribute on the Project tag exactly as: <Project Sdk="Microsoft.NET.Sdk.Web">
- REMOVE these packages completely: Microsoft.AspNetCore.SpaServices.Extensions, Npgsql.EntityFrameworkCore.PostgreSQL.Design, Microsoft.AspNet.Mvc, Microsoft.AspNet.WebApi, Microsoft.AspNet.WebPages, Microsoft.Web.Infrastructure
- Set Microsoft.EntityFrameworkCore and all EF Core packages to the version matching the target .NET version (e.g. 8.0.4 for .NET 8, 9.0.0 for .NET 9, 10.0.0 for .NET 10)
- Set Npgsql.EntityFrameworkCore.PostgreSQL to the version matching the target .NET version
- Set Microsoft.AspNetCore.Authentication.JwtBearer to the version matching the target .NET version
- Set Swashbuckle.AspNetCore to Version 6.5.0
- Remove any <Target> blocks related to SPA, webpack, or npm
- Remove any <Reference> items pointing to System.Web or old .NET Framework assemblies
- Keep SDK-style format, clean and minimal
- Return ONLY the migrated XML inside a ```xml block. Nothing else."""

SYSTEM_REVIEWER = """You are a .NET 8 code reviewer. Review migrated C# code and fix any remaining issues.
Rules:
- Fix any remaining System.Web references
- Fix any remaining old-style namespaces (convert block namespace to file-scoped)
- Fix any remaining UseEndpoints — replace with app.MapControllers()
- Fix any remaining AddSpaStaticFiles or UseSpa calls — remove them
- Fix any remaining HttpContext.Current — replace with IHttpContextAccessor
- Fix any remaining ConfigurationManager — replace with IConfiguration
- Ensure all using statements are valid for .NET 8
- Keep ALL business logic intact — do not remove any methods or properties
- Remove any variables that are declared but never used anywhere in the file
- Remove any Console.WriteLine or debug statements that were NOT present in the original code
- If a class has no instance state and all methods are utility/helper methods, keep it static — do not convert static classes to instance classes
- Fix any duplicate type keywords in method/lambda parameters (e.g. 'double double posLong' should be 'double posLong')
- Never replace a specific named DbContext type with the generic DbContext base class — always preserve the original context type name
- If code is already correct, return it as-is
- Return ONLY the corrected code inside a ```csharp block. Nothing else."""

# Folders to always skip during file reading
SKIP_FOLDERS = {"obj", "bin", ".vs", ".git", "node_modules", ".idea", "packages"}

# Extensions that get LLM migration
CODE_EXTENSIONS = {".cs", ".csproj", ".sln"}

# Extensions to copy as-is (no LLM, no modification)
COPY_EXTENSIONS = {
    ".cshtml", ".razor", ".json", ".xml", ".yaml", ".yml",
    ".html", ".htm", ".css", ".js", ".ts", ".jsx", ".tsx",
    ".txt", ".md", ".ico", ".png", ".jpg", ".jpeg", ".gif",
    ".svg", ".woff", ".woff2", ".ttf", ".eot", ".map",
    ".aspx", ".ascx", ".master", ".resx", ".edmx",
}

# Folders to always skip
SKIP_COPY_FOLDERS = {"obj", "bin", ".vs", ".git", "node_modules", ".idea", "packages"}

def read_files_recursive(upload_dir: str) -> dict:
    """Read only code files that need LLM migration."""
    files = {}
    upload_path = Path(upload_dir)
    for file in upload_path.rglob("*"):
        if not file.is_file():
            continue
        if any(part.lower() in SKIP_FOLDERS for part in file.parts):
            continue
        if file.suffix.lower() not in CODE_EXTENSIONS:
            continue
        try:
            relative_path = file.relative_to(upload_path)
            files[str(relative_path)] = file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
    return files


def copy_non_code_files(upload_dir: str, output_dir: Path) -> int:
    """
    Copy all non-code files (views, static assets, config json etc.)
    from upload to output as-is. These are not touched by LLM.
    Static asset folders (Content, Scripts, fonts, images) are copied into
    wwwroot/ inside the web project folder so ASP.NET Core UseStaticFiles() works.
    Returns count of files copied.
    """
    upload_path = Path(upload_dir)
    copied = 0

    # Detect web project roots — folders containing a web .csproj
    # (has ProjectTypeGuids for web or Microsoft.WebApplication.targets)
    web_project_roots = set()
    for csproj in upload_path.rglob("*.csproj"):
        if any(p.lower() in SKIP_COPY_FOLDERS for p in csproj.parts):
            continue
        try:
            csproj_content = csproj.read_text(encoding="utf-8", errors="ignore")
            if (
                "{349c5851" in csproj_content.lower()
                or "Microsoft.WebApplication.targets" in csproj_content
                or "Microsoft.NET.Sdk.Web" in csproj_content
            ):
                web_project_roots.add(csproj.parent)
        except Exception:
            pass

    # Static asset folder names that must go into wwwroot/
    static_folders = {"content", "scripts", "fonts", "images", "img", "wwwroot"}

    for file in upload_path.rglob("*"):
        if not file.is_file():
            continue
        if any(part.lower() in SKIP_COPY_FOLDERS for part in file.parts):
            continue
        if file.suffix.lower() not in COPY_EXTENSIONS:
            continue
        try:
            rel = file.relative_to(upload_path)
            rel_parts = list(rel.parts)

            # Change 7: remap static asset folders into wwwroot/
            # Check if this file sits inside a static folder under a web project root
            dst = None
            for web_root in web_project_roots:
                try:
                    rel_to_web = file.relative_to(web_root)
                    top_folder = rel_to_web.parts[0].lower() if rel_to_web.parts else ""
                    if top_folder in static_folders and top_folder != "wwwroot":
                        # Remap: web_project/Content/x -> output/web_project/wwwroot/Content/x
                        web_root_rel = web_root.relative_to(upload_path)
                        dst = output_dir / web_root_rel / "wwwroot" / rel_to_web
                        break
                except ValueError:
                    continue

            if dst is None:
                dst = output_dir / rel

            dst.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(str(file), str(dst))
            copied += 1
        except Exception:
            pass
    return copied

def extract_code(response: str, lang: str = "csharp") -> str:
    match = re.search(rf'```(?:{lang}|cs|xml|text)?\s*(.*?)\s*```', response, re.DOTALL)
    if match:
        result = match.group(1).strip()
    else:
        result = response.strip()
    # GAP 4 fix: strip any leftover markdown fence language identifier from first line
    first_line = result.split('\n')[0].strip().lower()
    if first_line in ('csharp', 'cs', 'xml', 'html', 'razor', 'cshtml', 'text'):
        result = '\n'.join(result.split('\n')[1:]).strip()
    return result

def get_model_names(files: dict) -> list:
    """Extract class names from model files to build DbSet properties."""
    model_names = []
    for path, content in files.items():
        if "Models" in path and path.endswith(".cs"):
            match = re.search(r'public class (\w+)', content)
            if match:
                model_names.append(match.group(1))
    return model_names

def fix_application_context(content: str, model_names: list) -> str:
    """Replace any existing DbSet properties with correct ones based on actual model names."""
    if not model_names:
        return content
    dbsets = "\n".join([f"    public DbSet<{m}> {m}s {{ get; set; }}" for m in model_names])
    # Remove any existing DbSet lines first
    content = re.sub(r'\s*public DbSet<[^>]+>[^;]+;', '', content)
    # Inject correct DbSets after class opening brace
    content = re.sub(
        r'(public class ApplicationContext\s*:\s*DbContext\s*\{)',
        f'\\1\n{dbsets}\n',
        content
    )
    return content

def review_code(code: str, relative_path: str) -> str:
    """Single reviewer pass — catches what LLM missed. Only called for .cs files."""
    prompt = f"""Review this migrated .NET 8 C# file and fix any remaining issues.
File: {relative_path}

```csharp
{code[:8000]}
```"""
    try:
        reviewed = ask_with_system(SYSTEM_REVIEWER, prompt, agent_name="Reviewer")
        return extract_code(reviewed, "csharp")
    except Exception:
        return code  # if reviewer fails, keep original migrated code


def find_program_and_startup(files: dict) -> tuple:
    """Find Program.cs and Startup.cs paths in the files dict."""
    program_path = next((k for k in files if k.replace('\\','/').endswith('Program.cs')), None)
    startup_path = next((k for k in files if k.replace('\\','/').endswith('Startup.cs')), None)
    return program_path, startup_path


def _regenerate_sln(output_dir: Path, sln_relative_path: str, progress_callback=None):
    """
    Regenerate a clean modern .sln file from all .csproj files found in the output.
    Replaces the old .NET Framework solution format which has legacy GUIDs that
    break dotnet CLI commands.
    Generic — works for any number of projects in the solution.
    """
    import uuid
    try:
        csproj_files = [
            f for f in output_dir.rglob("*.csproj")
            if not any(p.lower() in SKIP_FOLDERS for p in f.parts)
        ]
        if not csproj_files:
            return

        sln_path = output_dir / sln_relative_path
        sln_path.parent.mkdir(parents=True, exist_ok=True)

        project_entries = []
        project_configs = []
        cs_project_type = "{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}"

        for csproj in csproj_files:
            proj_id = "{" + str(uuid.uuid4()).upper() + "}"
            proj_name = csproj.stem
            # Path relative to .sln location
            rel = csproj.relative_to(sln_path.parent)
            rel_str = str(rel).replace("/", "\\")
            project_entries.append(
                f'Project("{cs_project_type}") = "{proj_name}", "{rel_str}", "{proj_id}"\n'
                f'EndProject'
            )
            project_configs.append(
                f'\t\t{proj_id}.Debug|Any CPU.ActiveCfg = Debug|Any CPU\n'
                f'\t\t{proj_id}.Debug|Any CPU.Build.0 = Debug|Any CPU\n'
                f'\t\t{proj_id}.Release|Any CPU.ActiveCfg = Release|Any CPU\n'
                f'\t\t{proj_id}.Release|Any CPU.Build.0 = Release|Any CPU'
            )

        sln_content = (
            "\nMicrosoft Visual Studio Solution File, Format Version 12.00\n"
            "# Visual Studio Version 17\n"
            "VisualStudioVersion = 17.0.31903.59\n"
            "MinimumVisualStudioVersion = 10.0.40219.1\n"
            + "\n".join(project_entries)
            + "\nGlobal\n"
            "\tGlobalSection(SolutionConfigurationPlatforms) = preSolution\n"
            "\t\tDebug|Any CPU = Debug|Any CPU\n"
            "\t\tRelease|Any CPU = Release|Any CPU\n"
            "\tEndGlobalSection\n"
            "\tGlobalSection(ProjectConfigurationPlatforms) = postSolution\n"
            + "\n".join(project_configs)
            + "\n\tEndGlobalSection\n"
            "EndGlobal\n"
        )
        sln_path.write_text(sln_content, encoding="utf-8")
        if progress_callback:
            progress_callback(
                f"Source Migration Agent: Regenerated {sln_path.name} "
                f"with {len(csproj_files)} project(s)"
            )
    except Exception:
        pass


def generate_launch_settings(output_dir: Path, progress_callback=None):
    """
    Generate Properties/launchSettings.json for every web project in the output
    that doesn't already have one.
    Enables `dotnet run` to start on a predictable port with Development environment.
    Generic — derives project name from folder, works for any web project.
    """
    # Find all web .csproj files in the output
    for csproj in output_dir.rglob("*.csproj"):
        if any(p.lower() in SKIP_FOLDERS for p in csproj.parts):
            continue
        try:
            content = csproj.read_text(encoding="utf-8", errors="ignore")
            if "Microsoft.NET.Sdk.Web" not in content:
                continue
            props_dir = csproj.parent / "Properties"
            launch_file = props_dir / "launchSettings.json"
            if launch_file.exists():
                continue
            props_dir.mkdir(parents=True, exist_ok=True)
            project_name = csproj.stem
            import json
            settings = {
                "profiles": {
                    project_name: {
                        "commandName": "Project",
                        "dotnetRunMessages": True,
                        "launchBrowser": True,
                        "applicationUrl": "https://localhost:7001;http://localhost:5001",
                        "environmentVariables": {
                            "ASPNETCORE_ENVIRONMENT": "Development"
                        }
                    }
                }
            }
            launch_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
            if progress_callback:
                progress_callback(
                    f"Source Migration Agent: Generated launchSettings.json for {project_name}"
                )
        except Exception:
            pass


def generate_appsettings_from_webconfig(upload_dir: str, output_dir: Path, progress_callback=None) -> bool:
    """
    Scan any web.config in the upload for <appSettings> and <connectionStrings>.
    Generate a starter appsettings.json in the corresponding output web project folder.
    Generic — works for any .NET Framework project with a web.config.
    Returns True if a file was written.
    """
    upload_path = Path(upload_dir)
    written = False

    for web_config in upload_path.rglob("web.config"):
        if any(p.lower() in SKIP_FOLDERS for p in web_config.parts):
            continue
        # Skip Views/web.config — that's a view-layer config, not app config
        if web_config.parent.name.lower() == "views":
            continue
        try:
            content = web_config.read_text(encoding="utf-8", errors="ignore")

            app_settings = {}
            for m in re.finditer(r'<add\s+key="([^"]+)"\s+value="([^"]*)"', content):
                key, value = m.group(1), m.group(2)
                # Skip ASP.NET internal keys — not needed in .NET 8
                if key.lower().startswith("webpages:"):
                    continue
                app_settings[key] = value

            conn_strings = {}
            for m in re.finditer(
                r'<add\s+name="([^"]+)"\s+connectionString="([^"]*)"',
                content
            ):
                conn_strings[m.group(1)] = m.group(2)

            if not app_settings and not conn_strings:
                continue

            import json
            appsettings = {}
            if app_settings:
                appsettings["AppSettings"] = app_settings
            if conn_strings:
                appsettings["ConnectionStrings"] = conn_strings
            # Always include standard Logging and AllowedHosts
            appsettings["Logging"] = {
                "LogLevel": {"Default": "Information", "Microsoft.AspNetCore": "Warning"}
            }
            appsettings["AllowedHosts"] = "*"

            rel_folder = web_config.parent.relative_to(upload_path)
            out_folder = output_dir / rel_folder
            out_folder.mkdir(parents=True, exist_ok=True)
            out_file = out_folder / "appsettings.json"
            # Only write if not already there from a previous step
            if not out_file.exists():
                out_file.write_text(
                    json.dumps(appsettings, indent=2),
                    encoding="utf-8"
                )
                written = True
                if progress_callback:
                    progress_callback(
                        f"Source Migration Agent: Generated appsettings.json from {web_config.name}"
                    )
        except Exception:
            pass

    return written


def extract_project_references(csproj_content: str) -> str:
    """
    Deterministically extract <ProjectReference> entries from a source .csproj.
    Returns them as a hint string so the LLM never accidentally drops them.
    Generic — works for any multi-project solution.
    """
    refs = re.findall(r'<ProjectReference\s+Include="([^"]+)"', csproj_content)
    if not refs:
        return ""
    lines = [f'  <ProjectReference Include="{ref}" />' for ref in refs]
    return (
        "\nProjectReferences that MUST be kept exactly as-is in the output:\n"
        + "\n".join(lines)
    )


def read_packages_config(csproj_path: str, upload_dir: str) -> str:
    """
    For any .csproj, look for a packages.config in the same folder.
    Parse all <package> entries and return them as a hint string for the LLM prompt.
    Filters out frontend JS packages, old bundling infrastructure, and legacy
    ASP.NET Framework packages that have no place in a .NET 8 project.
    Generic — works for any .NET Framework project with a packages.config.
    Returns empty string if no packages.config found.
    """
    # Packages that must never be carried over to .NET 8
    # Category 1: Frontend JS libraries — already copied to wwwroot/ as physical files
    # Category 2: Old bundling infrastructure — BundleConfig is removed, not needed
    # Category 3: Old ASP.NET Framework packages — replaced by built-in ASP.NET Core
    BLOCKED_PREFIXES = {
        "jquery", "bootstrap", "modernizr", "respond",
        "microsoft.jquery", "antlr", "webgrease",
        "microsoft.aspnet.web.optimization",
        "microsoft.aspnet.mvc", "microsoft.aspnet.razor",
        "microsoft.aspnet.webpages", "microsoft.web.infrastructure",
        "microsoft.aspnet.identity", "microsoft.owin", "owin",
    }

    csproj_folder = Path(upload_dir) / Path(csproj_path).parent
    packages_config = csproj_folder / "packages.config"
    if not packages_config.exists():
        return ""
    try:
        content = packages_config.read_text(encoding="utf-8", errors="ignore")
        packages = re.findall(r'<package\s+id="([^"]+)"\s+version="([^"]+)"', content)
        if not packages:
            return ""
        # Filter out blocked packages
        filtered = [
            (pkg_id, version) for pkg_id, version in packages
            if not any(pkg_id.lower().startswith(prefix) for prefix in BLOCKED_PREFIXES)
        ]
        if not filtered:
            return ""
        lines = [f"  - {pkg_id} {version}" for pkg_id, version in filtered]
        return "\nPackages from packages.config (carry relevant ones over as PackageReference):\n" + "\n".join(lines)
    except Exception:
        return ""

def migrate(upload_dir: str, from_version: str, to_version: str,
            source_frontend: str = None, target_frontend: str = None,
            progress_callback: Optional[Callable[[str], None]] = None) -> dict:

    # Determine if target is REST API mode (frontend is separate — no Razor views)
    is_rest_api_target = target_frontend in ("react", "angular", "vue")
    files = read_files_recursive(upload_dir)
    if not files:
        return {"success": False, "error": "No C# files found in upload directory"}

    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # Copy all non-code files first (views, static assets, wwwroot, json configs)
    if progress_callback:
        progress_callback("Copying views, static assets and config files...")
    copied = copy_non_code_files(upload_dir, output_dir)
    if progress_callback:
        progress_callback(f"Copied {copied} non-code file(s) to output.")

    # Change 3: generate appsettings.json from web.config for any .NET Framework project
    generate_appsettings_from_webconfig(upload_dir, output_dir, progress_callback)

    migrated = {}
    total_files = len(files)
    model_names = get_model_names(files)

    # Find Program.cs and Startup.cs
    program_path, startup_path = find_program_and_startup(files)

    # GAP 1 fix: Generate Program.cs from scratch if Global.asax exists but no Program.cs
    skip_files = set()
    global_asax_cs = next(
        (k for k in files if Path(k).name.lower() == 'global.asax.cs'),
        None
    )
    if not program_path and global_asax_cs:
        global_content = files[global_asax_cs]
        ns_match = re.search(r'namespace\s+([\w\.]+)', global_content)
        app_namespace = ns_match.group(1) if ns_match else 'WebApplication'

        if is_rest_api_target:
            program_cs_code = """using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddControllers();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();
builder.Services.AddCors(options =>
{
    options.AddDefaultPolicy(policy =>
        policy.AllowAnyOrigin().AllowAnyMethod().AllowAnyHeader());
});

var app = builder.Build();

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

app.UseHttpsRedirection();
app.UseCors();
app.UseAuthorization();

app.MapControllers();

app.Run();
"""
        else:
            program_cs_code = """using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddControllersWithViews();

var app = builder.Build();

if (!app.Environment.IsDevelopment())
{
    app.UseExceptionHandler("/Home/Error");
    app.UseHsts();
}

app.UseHttpsRedirection();
app.UseStaticFiles();
app.UseRouting();
app.UseAuthorization();

app.MapControllerRoute(
    name: "default",
    pattern: "{controller=Home}/{action=Index}/{id?}");

app.Run();
"""
        project_root = Path(global_asax_cs).parent
        program_out_path = output_dir / project_root / 'Program.cs'
        program_out_path.parent.mkdir(parents=True, exist_ok=True)
        program_out_path.write_text(program_cs_code, encoding='utf-8')
        migrated[str(project_root / 'Program.cs')] = program_cs_code
        if progress_callback:
            progress_callback('Source Migration Agent: Generated Program.cs from Global.asax.cs')

        # Skip legacy framework-only files — not valid in .NET 8
        legacy_skip = {'global.asax.cs', 'global.asax', 'routeconfig.cs', 'filterconfig.cs', 'bundleconfig.cs'}
        for k in files:
            if Path(k).name.lower() in legacy_skip:
                skip_files.add(k)

    else:
        # Even when Program.cs+Startup.cs exist, still skip Global.asax legacy files
        legacy_skip = {'global.asax.cs', 'global.asax', 'routeconfig.cs', 'filterconfig.cs', 'bundleconfig.cs'}
        for k in files:
            if Path(k).name.lower() in legacy_skip:
                skip_files.add(k)

    # Handle Program.cs + Startup.cs merge first
    if program_path and startup_path:
        if progress_callback:
            progress_callback(f"Merging Program.cs + Startup.cs into .NET 8 minimal hosting...")

        program_content = files[program_path]
        startup_content = files[startup_path]

        rest_api_hint = (
            "\n- Target frontend is a separate SPA — use AddControllers() not AddControllersWithViews(), add CORS, no Razor views"
            if is_rest_api_target else ""
        )
        prompt = f"""Migrate these two files from {from_version} to .NET 8 minimal hosting.

--- Program.cs ---
{program_content[:4000]}

--- Startup.cs ---
{startup_content[:4000]}

Rules:
- Use WebApplication.CreateBuilder(args)
- Move ALL services from ConfigureServices into builder.Services — do not skip any
- Move ALL middleware from Configure into app pipeline in the same order
- Keep Swagger/OpenAPI setup if present (AddSwaggerGen, UseSwagger, UseSwaggerUI)
- Keep JWT authentication if present (AddAuthentication, AddJwtBearer)
- Keep CORS if present (AddCors, UseCors)
- Keep any custom services, repositories, or interfaces registered
- End with app.Run()
- NO Startup class, NO CreateHostBuilder, NO IHostBuilder{rest_api_hint}
- Return ONLY the complete Program.cs inside a ```csharp block."""

        response = ask_with_system(SYSTEM_PROGRAM, prompt, agent_name="Source Migration Agent")
        merged_code = extract_code(response, "csharp")
        if progress_callback:
            progress_callback("Source Migration Agent: Reviewing merged Program.cs...")
        merged_code = review_code(merged_code, program_path)
        increment_execution()
        time.sleep(1)

        out_path = output_dir / program_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(merged_code, encoding="utf-8")
        migrated[program_path] = merged_code
        migrated[startup_path] = "[merged into Program.cs]"

        if progress_callback:
            progress_callback(f"Merged Program.cs + Startup.cs (1/{total_files})")

        # Fix 3 — yield CPU after merge step
        time.sleep(0.1)
        time.sleep(1.9)

    elif program_path and not startup_path:
        # Program.cs only — migrate it directly with SYSTEM_PROGRAM
        if progress_callback:
            progress_callback("Migrating Program.cs to .NET 8 minimal hosting...")

        program_content = files[program_path]
        prompt = f"""Migrate this Program.cs from {from_version} to .NET 8 minimal hosting.

{program_content[:4000]}

Rules:
- Use WebApplication.CreateBuilder(args)
- Keep ALL services and middleware intact
- Keep Swagger, JWT, CORS, Razor Pages, MVC — whatever is already there
- End with app.Run()
- Return ONLY the complete Program.cs inside a ```csharp block."""

        response = ask_with_system(SYSTEM_PROGRAM, prompt, agent_name="Source Migration Agent")
        program_code = extract_code(response, "csharp")
        if progress_callback:
            progress_callback("Source Migration Agent: Reviewing Program.cs...")
        program_code = review_code(program_code, program_path)
        increment_execution()
        time.sleep(1)

        out_path = output_dir / program_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(program_code, encoding="utf-8")
        migrated[program_path] = program_code

        if progress_callback:
            progress_callback(f"Migrated Program.cs (1/{total_files})")

        # Fix 3 — yield CPU after Program.cs step
        time.sleep(0.1)
        time.sleep(1.9)

    for index, (relative_path, content) in enumerate(files.items(), start=1):
        # Skip Program.cs and Startup.cs — already handled
        if relative_path == program_path or relative_path == startup_path:
            continue
        # Skip legacy framework-only files — Global.asax, RouteConfig, FilterConfig, BundleConfig
        if relative_path in skip_files:
            if progress_callback:
                progress_callback(f'Source Migration Agent: Skipping legacy file {Path(relative_path).name}')
            continue

        # Skip obj/bin folders
        path_parts = Path(relative_path).parts
        if any(part.lower() in {"obj", "bin", ".vs", ".git"} for part in path_parts):
            continue

        if progress_callback:
            progress_callback(f"Migrating {relative_path} ({index}/{total_files})")

        file_type = Path(relative_path).suffix

        if file_type == '.cs':
            # Build known class names from other files to prevent stub generation
            known_classes = set()
            for other_path, other_content in files.items():
                if other_path != relative_path and other_path.endswith('.cs'):
                    for m in re.findall(r'public\s+(?:partial\s+)?(?:class|interface|enum)\s+(\w+)', other_content):
                        known_classes.add(m)
            known_classes_hint = ''
            if known_classes and 'Controller' in relative_path:
                known_classes_hint = f"\n- These classes already exist in other project files — do NOT redefine them here, import via using instead: {', '.join(sorted(known_classes))}"

            # Special fix for ApplicationContext — inject correct DbSets directly
            if 'ApplicationContext' in relative_path or 'ApplicationContext' in content:
                fixed = fix_application_context(content, model_names)
                prompt = f"""Migrate this C# DbContext file from {from_version} to .NET 8 / C# 12.
File: {relative_path}

```csharp
{fixed[:8000]}
```

Rules:
- Use file-scoped namespace
- Keep ALL DbSet properties exactly as they are in the input — do not remove or rename any
- Ensure constructor takes DbContextOptions
- Return ONLY the complete migrated C# code in a ```csharp block."""
            else:
                prompt = f"""Migrate this C# file from {from_version} to .NET 8 / C# 12.
File: {relative_path}

```csharp
{content[:8000]}
```

Rules:
- Never define a class, interface or enum that already exists in another file in this solution{known_classes_hint}
- Only add using statements for missing types — do not duplicate class definitions
- Return ONLY the complete migrated C# code in a ```csharp block."""
            try:
                response = ask_with_system(SYSTEM_CS, prompt, agent_name="Source Migration Agent")
                code = extract_code(response, "csharp")
            except Exception as e:
                if progress_callback:
                    progress_callback(f"Source Migration Agent: skipping {relative_path} — LLM error: {str(e)}")
                continue
            # Reviewer pass — catches what LLM missed
            if progress_callback:
                progress_callback(f"Source Migration Agent: Reviewing {relative_path}...")
            code = review_code(code, relative_path)
            # Fix 3 — yield CPU between migrate + review
            time.sleep(0.1)

        elif file_type == '.csproj':
            # GAP 2 fix: detect web vs class library to use correct SDK
            is_web = (
                '{349c5851' in content.lower()
                or 'Microsoft.WebApplication.targets' in content
                or 'Microsoft.NET.Sdk.Web' in content
            )
            sdk = 'Microsoft.NET.Sdk.Web' if is_web else 'Microsoft.NET.Sdk'
            # Change 1: read packages.config from same folder and pass as hint
            packages_hint = read_packages_config(relative_path, upload_dir)
            # Change 7: deterministically extract ProjectReferences so LLM cannot drop them
            proj_ref_hint = extract_project_references(content)
            prompt = f"""Migrate this .csproj from {from_version} to {to_version}.
File: {relative_path}

```xml
{content[:8000]}
```
{packages_hint}
{proj_ref_hint}
Rules:
- Use exactly: <Project Sdk="{sdk}">
- Set <TargetFramework> to the exact target version
- Add <Nullable>enable</Nullable> and <ImplicitUsings>enable</ImplicitUsings>
- Remove ALL <Compile>, <Content>, <None>, <Folder> explicit item entries — SDK includes them implicitly
- Remove ALL <Reference> items — use PackageReference only
- Remove ALL legacy <Import> statements
- MUST include every ProjectReference listed above — do not remove any
- Only add PackageReference entries that are genuinely needed based on the original packages
- Return ONLY the migrated XML in a ```xml block."""
            try:
                response = ask_with_system(SYSTEM_CSPROJ, prompt, agent_name="Source Migration Agent")
                code = extract_code(response, "xml")
            except Exception as e:
                if progress_callback:
                    progress_callback(f"Source Migration Agent: skipping {relative_path} — LLM error: {str(e)}")
                continue

        elif file_type == '.sln':
            # Change 4: regenerate .sln from output .csproj files instead of copying old format
            _regenerate_sln(output_dir, relative_path, progress_callback)
            continue

        else:
            # all other files already copied — skip
            continue

        out_path = output_dir / relative_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(code, encoding="utf-8")
        migrated[relative_path] = code
        increment_execution()

        if progress_callback:
            progress_callback(f"Saved {relative_path} ({index}/{total_files})")

        # Fix 3 — yield CPU before the rate-limit sleep
        # Lets OS scheduler serve queued polling requests before next LLM call
        time.sleep(0.1)
        # Remaining wait to avoid Groq rate limits
        time.sleep(1.9)

    # Change 8: generate launchSettings.json for every web project in the output
    generate_launch_settings(output_dir, progress_callback)

    return {"success": True, "migrated": migrated, "count": len(migrated), "output_dir": str(output_dir)}


# ── Agent wrapper ─────────────────────────────────────────────────────────
from agents.base_agent import BaseAgent
from agents.context import MigrationContext, AgentObservation

class MigratorAgent(BaseAgent):
    name = "Source Migration Agent"
    goal = "rewrite all source files to target .NET version using LLM"

    def act(self, context: MigrationContext) -> dict:
        result = migrate(
            upload_dir=context.upload_dir,
            from_version=context.from_version,
            to_version=context.to_version,
            source_frontend=context.source_frontend,
            target_frontend=context.target_frontend,
            progress_callback=context.progress_callback,
        )
        return result

    def observe(self, result: dict, context: MigrationContext) -> AgentObservation:
        context.migrated_files = result.get("migrated", {})
        # Filter placeholder
        context.migrated_files = {
            k: v for k, v in context.migrated_files.items()
            if v != "[merged into Program.cs]"
        }
        success = result.get("success", False)
        return AgentObservation(
            agent=self.name,
            status="completed" if success else "failed",
            summary=(
                f"Migrated {result.get('count', 0)} file(s) to {context.to_version}."
                if success else result.get("error", "Migration failed.")
            ),
            actionable=not success,
            recommended_next="auth_agent" if success else "",
            data=result,
        )

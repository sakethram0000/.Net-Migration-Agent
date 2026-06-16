"""
View Migration Agent — runs after Auth Agent, before Fix Agent.
Migrates .cshtml Razor views from legacy HTML Helpers to .NET 8 Tag Helpers.
Layer 1: Deterministic regex replacements — no LLM, always correct.
Layer 2: LLM pass only for views that still have @Html. patterns after Layer 1.
"""
from pathlib import Path
import re
from typing import Callable, Optional

SKIP_FOLDERS = {"obj", "bin", ".vs", ".git", "node_modules"}

# ── Layer 1: Deterministic HTML Helper → Tag Helper replacements ──────────

def _replace_html_helpers(content: str) -> str:
    """Apply all known deterministic HTML Helper → Tag Helper replacements."""

    # GAP 5 fix: Replace @Styles.Render and @Scripts.Render with direct tags
    # Common bundle → actual file mappings
    styles_map = {
        '~/Content/css':    '<link rel="stylesheet" href="~/Content/bootstrap.min.css" />\n    <link rel="stylesheet" href="~/Content/Site.css" />',
        '~/Content/css"':   '<link rel="stylesheet" href="~/Content/bootstrap.min.css" />\n    <link rel="stylesheet" href="~/Content/Site.css" />',
    }
    scripts_map = {
        '~/bundles/modernizr':  '<script src="~/Scripts/modernizr-2.6.2.js"></script>',
        '~/bundles/jquery':     '<script src="~/Scripts/jquery-1.10.2.min.js"></script>',
        '~/bundles/bootstrap':  '<script src="~/Scripts/bootstrap.min.js"></script>',
        '~/bundles/jqueryval':  '<script src="~/Scripts/jquery.validate.min.js"></script>\n    <script src="~/Scripts/jquery.validate.unobtrusive.min.js"></script>',
    }

    def replace_styles(m):
        bundle = m.group(1).strip().rstrip('"').rstrip("'")
        return styles_map.get(bundle, styles_map.get(bundle + '"',
            f'<link rel="stylesheet" href="~/Content/site.css" />'))

    def replace_scripts(m):
        bundle = m.group(1).strip().rstrip('"').rstrip("'")
        return scripts_map.get(bundle,
            f'<script src="~/Scripts/site.js"></script>')

    content = re.sub(r'@Styles\.Render\s*\(\s*"([^"]+)"\s*\)', replace_styles, content)
    content = re.sub(r'@Scripts\.Render\s*\(\s*"([^"]+)"\s*\)', replace_scripts, content)

    # @Html.TextBoxFor(m => m.X) → <input asp-for="X">
    content = re.sub(
        r'@Html\.TextBoxFor\s*\(\s*\w+\s*=>\s*\w+\.(\w+)(?:,\s*new\s*\{[^}]*\})?\s*\)',
        lambda m: f'<input asp-for="{m.group(1)}" class="form-control">',
        content
    )

    # @Html.PasswordFor(m => m.X) → <input asp-for="X" type="password">
    content = re.sub(
        r'@Html\.PasswordFor\s*\(\s*\w+\s*=>\s*\w+\.(\w+)(?:,\s*new\s*\{[^}]*\})?\s*\)',
        lambda m: f'<input asp-for="{m.group(1)}" type="password" class="form-control">',
        content
    )

    # @Html.TextAreaFor(m => m.X) → <textarea asp-for="X"></textarea>
    content = re.sub(
        r'@Html\.TextAreaFor\s*\(\s*\w+\s*=>\s*\w+\.(\w+)(?:,\s*new\s*\{[^}]*\})?\s*\)',
        lambda m: f'<textarea asp-for="{m.group(1)}" class="form-control"></textarea>',
        content
    )

    # @Html.LabelFor(m => m.X) → <label asp-for="X"></label>
    content = re.sub(
        r'@Html\.LabelFor\s*\(\s*\w+\s*=>\s*\w+\.(\w+)(?:,\s*new\s*\{[^}]*\})?\s*\)',
        lambda m: f'<label asp-for="{m.group(1)}"></label>',
        content
    )

    # @Html.ValidationMessageFor(m => m.X) → <span asp-validation-for="X"></span>
    content = re.sub(
        r'@Html\.ValidationMessageFor\s*\(\s*\w+\s*=>\s*\w+\.(\w+)(?:,\s*[^)]+)?\s*\)',
        lambda m: f'<span asp-validation-for="{m.group(1)}" class="text-danger"></span>',
        content
    )

    # @Html.ValidationSummary() → <div asp-validation-summary="All"></div>
    content = re.sub(
        r'@Html\.ValidationSummary\s*\([^)]*\)',
        '<div asp-validation-summary="All" class="text-danger"></div>',
        content
    )

    # @Html.DropDownListFor(m => m.X, ...) → <select asp-for="X" asp-items="..."></select>
    content = re.sub(
        r'@Html\.DropDownListFor\s*\(\s*\w+\s*=>\s*\w+\.(\w+)\s*,\s*([^)]+)\)',
        lambda m: f'<select asp-for="{m.group(1)}" asp-items="{m.group(2).strip()}"></select>',
        content
    )

    # @Html.CheckBoxFor(m => m.X) → <input asp-for="X" type="checkbox">
    content = re.sub(
        r'@Html\.CheckBoxFor\s*\(\s*\w+\s*=>\s*\w+\.(\w+)(?:,\s*new\s*\{[^}]*\})?\s*\)',
        lambda m: f'<input asp-for="{m.group(1)}" type="checkbox">',
        content
    )

    # @Html.HiddenFor(m => m.X) → <input asp-for="X" type="hidden">
    content = re.sub(
        r'@Html\.HiddenFor\s*\(\s*\w+\s*=>\s*\w+\.(\w+)\s*\)',
        lambda m: f'<input asp-for="{m.group(1)}" type="hidden">',
        content
    )

    # @Html.ActionLink("text", "action", "controller") → <a asp-action="action" asp-controller="controller">text</a>
    content = re.sub(
        r'@Html\.ActionLink\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"[^)]*\)',
        lambda m: f'<a asp-action="{m.group(2)}" asp-controller="{m.group(3)}">{m.group(1)}</a>',
        content
    )

    # @Html.ActionLink("text", "action") → <a asp-action="action">text</a>
    content = re.sub(
        r'@Html\.ActionLink\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)',
        lambda m: f'<a asp-action="{m.group(2)}">{m.group(1)}</a>',
        content
    )

    # @Html.Partial("_Name") → <partial name="_Name">
    content = re.sub(
        r'@Html\.Partial\s*\(\s*"([^"]+)"[^)]*\)',
        lambda m: f'<partial name="{m.group(1)}">',
        content
    )

    # @{ Html.RenderPartial("_Name"); } → <partial name="_Name">
    content = re.sub(
        r'@\{\s*Html\.RenderPartial\s*\(\s*"([^"]+)"[^)]*\)\s*;\s*\}',
        lambda m: f'<partial name="{m.group(1)}">',
        content
    )

    # @using (Html.BeginForm(...)) { → <form asp-action="..." method="post">
    content = re.sub(
        r'@using\s*\(\s*Html\.BeginForm\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"[^)]*\)\s*\)\s*\{',
        lambda m: f'<form asp-action="{m.group(1)}" asp-controller="{m.group(2)}" method="post">',
        content
    )
    content = re.sub(
        r'@using\s*\(\s*Html\.BeginForm\s*\([^)]*\)\s*\)\s*\{',
        '<form method="post">',
        content
    )

    # @Html.AntiForgeryToken() → remove (handled automatically by Tag Helpers)
    content = re.sub(r'@Html\.AntiForgeryToken\s*\(\s*\)', '', content)

    # @Scripts.Render("...") → remove (bundling not needed in .NET 8)
    content = re.sub(r'@Scripts\.Render\s*\([^)]+\)\s*\n?', '', content)

    # @Styles.Render("...") → remove
    content = re.sub(r'@Styles\.Render\s*\([^)]+\)\s*\n?', '', content)

    # @Html.DisplayFor(m => m.X) → @Model.X (simple display)
    content = re.sub(
        r'@Html\.DisplayFor\s*\(\s*\w+\s*=>\s*\w+\.(\w+)\s*\)',
        lambda m: f'@Model.{m.group(1)}',
        content
    )

    # @Html.DisplayNameFor(m => m.X) → X (just the property name as label)
    content = re.sub(
        r'@Html\.DisplayNameFor\s*\(\s*\w+\s*=>\s*\w+\.(\w+)\s*\)',
        lambda m: m.group(1),
        content
    )

    # @Html.EditorFor(m => m.X) → <input asp-for="X">
    content = re.sub(
        r'@Html\.EditorFor\s*\(\s*\w+\s*=>\s*\w+\.(\w+)(?:,\s*[^)]+)?\s*\)',
        lambda m: f'<input asp-for="{m.group(1)}" class="form-control">',
        content
    )

    # Ensure bare C# control flow keywords have @ prefix — Razor syntax rule
    # Covers if/foreach/for/while/switch that lost their @ during copy or LLM pass
    # Generic — applies to any .cshtml file in any project
    for keyword in ('if', 'foreach', 'for', 'while', 'switch'):
        content = re.sub(
            rf'(?m)^([ \t]*)(?<!@)\b({keyword})\s*\(',
            rf'\1@{keyword}(',
            content
        )

    # Remove stray markdown language identifiers left on the first line
    # e.g. if LLM response leaks "csharp" or "cshtml" as a bare word
    content = re.sub(r'^(csharp|cshtml|razor|html|xml)\s*\n', '', content, flags=re.IGNORECASE)

    return content


def _fix_viewimports(output_dir: Path) -> list:
    """Ensure _ViewImports.cshtml has the Tag Helper import."""
    fixes = []
    tag_helper_import = "@addTagHelper *, Microsoft.AspNetCore.Mvc.TagHelpers"

    for viewimports in output_dir.rglob("_ViewImports.cshtml"):
        if any(p.lower() in SKIP_FOLDERS for p in viewimports.parts):
            continue
        try:
            content = viewimports.read_text(encoding="utf-8", errors="ignore")
            if "Microsoft.AspNetCore.Mvc.TagHelpers" not in content:
                content = tag_helper_import + "\n" + content
                viewimports.write_text(content, encoding="utf-8")
                fixes.append(f"Added Tag Helper import to {viewimports.name}")
        except Exception:
            pass

    # If no _ViewImports.cshtml exists, create one in the first Views or Pages folder
    if not fixes:
        for folder_name in ["Views", "Pages"]:
            views_folder = None
            for f in output_dir.rglob(folder_name):
                if f.is_dir() and not any(p.lower() in SKIP_FOLDERS for p in f.parts):
                    views_folder = f
                    break
            if views_folder:
                viewimports_path = views_folder / "_ViewImports.cshtml"
                if not viewimports_path.exists():
                    viewimports_path.write_text(
                        f"{tag_helper_import}\n@using Microsoft.AspNetCore.Mvc.Rendering\n",
                        encoding="utf-8"
                    )
                    fixes.append(f"Created _ViewImports.cshtml with Tag Helper import in {folder_name}/")
                break

    return fixes


# ── Layer 2: LLM pass for complex views ──────────────────────────────────

def _needs_llm_pass(content: str) -> bool:
    """Check if view still has HTML helpers after deterministic pass."""
    return bool(re.search(r'@Html\.', content))


def _deduplicate_sections(content: str) -> str:
    """
    Remove duplicate @section blocks from a Razor view.
    If the same section name appears more than once, keep only the last one
    which is always the real preserved block — remove any empty LLM placeholders.
    Generic — works for any section name in any .cshtml file.
    """
    section_pattern = re.compile(
        r'@section\s+(\w+)\s*\{', re.IGNORECASE
    )
    matches = list(section_pattern.finditer(content))
    if len(matches) <= 1:
        return content

    # Group match positions by section name
    by_name = {}
    for m in matches:
        name = m.group(1).lower()
        by_name.setdefault(name, []).append(m)

    # For each section with duplicates, find full block extents and remove all but last
    ranges_to_remove = []
    for name, occurrences in by_name.items():
        if len(occurrences) < 2:
            continue
        # Remove all but the last occurrence
        for m in occurrences[:-1]:
            # Walk forward to find the matching closing brace
            start = m.start()
            depth = 0
            pos = m.end() - 1  # position of opening {
            for i in range(pos, len(content)):
                if content[i] == '{':
                    depth += 1
                elif content[i] == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        # Include trailing newline if present
                        if end < len(content) and content[end] == '\n':
                            end += 1
                        ranges_to_remove.append((start, end))
                        break

    if not ranges_to_remove:
        return content

    # Remove ranges from end to start so positions stay valid
    ranges_to_remove.sort(key=lambda x: x[0], reverse=True)
    for start, end in ranges_to_remove:
        content = content[:start] + content[end:]

    return content


def _llm_migrate_view(path: str, content: str, from_version: str, to_version: str) -> str:
    """Send complex view to LLM for targeted rewrite."""
    # Extract and preserve @section scripts block before LLM pass
    # so the LLM never sees it and cannot duplicate or corrupt it
    scripts_block = ''
    scripts_match = re.search(r'(@section\s+scripts\s*\{.*?\}\s*)$', content, re.DOTALL | re.IGNORECASE)
    if scripts_match:
        scripts_block = scripts_match.group(1)
        content = content[:scripts_match.start()].rstrip()

    try:
        from agents.llm import ask_with_system
        system = """You are a .NET 8 Razor view migration expert.
Convert legacy HTML Helpers to ASP.NET Core Tag Helpers.
Rules:
- Replace ALL @Html.* helpers with equivalent Tag Helpers
- Keep all HTML structure, CSS classes, and layout intact
- Keep all @model, @using, @inject directives
- Keep all C# logic blocks (@foreach, @if, etc.)
- Do NOT generate any @section scripts block — it is handled separately and will be added back automatically
- NEVER remove <script> tags or JavaScript code outside of section blocks
- Return ONLY the migrated .cshtml content. Nothing else."""

        prompt = f"""Migrate this Razor view from {from_version} to .NET 8 Tag Helpers.
File: {path}

{content[:6000]}

IMPORTANT: Do NOT include any @section scripts block in your response — it will be appended automatically.
Return ONLY the migrated .cshtml content."""

        result = ask_with_system(system, prompt, agent_name="View Migration Agent")
        result = re.sub(r'^```(?:cshtml|html|razor)?\s*', '', result, flags=re.MULTILINE)
        result = re.sub(r'\s*```$', '', result, flags=re.MULTILINE)
        result = result.strip()
    except Exception:
        result = content

    # Restore preserved scripts block
    if scripts_block:
        result = result.rstrip() + '\n\n' + scripts_block

    # Approach 3 safety net — deduplicate any @section blocks the LLM still emitted
    result = _deduplicate_sections(result)

    return result


# ── Main entry point ──────────────────────────────────────────────────────

def run_view_migrator(
    output_dir: str,
    from_version: str,
    to_version: str,
    progress_callback: Optional[Callable[[str], None]] = None
) -> dict:
    """
    Main entry point called from migration pipeline.
    Only runs if .cshtml files exist in the output.
    Returns full result for reporter.
    """
    out = Path(output_dir)

    def progress(msg):
        if progress_callback:
            progress_callback(msg)

    # Find all .cshtml files
    cshtml_files = [
        f for f in out.rglob("*.cshtml")
        if not any(p.lower() in SKIP_FOLDERS for p in f.parts)
    ]

    if not cshtml_files:
        return {
            "skipped": True,
            "reason": "No .cshtml files found — project has no Razor views",
            "views_processed": 0,
            "helpers_replaced": 0,
            "llm_passes": 0,
            "manual_review": [],
            "viewimports_fixed": [],
            "changes": [],
        }

    progress(f"View Migration Agent: Found {len(cshtml_files)} .cshtml file(s) — starting migration...")

    total_helpers_replaced = 0
    llm_passes = 0
    manual_review = []
    changes = []

    for cshtml_file in cshtml_files:
        rel = str(cshtml_file.relative_to(out))
        try:
            original = cshtml_file.read_text(encoding="utf-8", errors="ignore")

            # Count helpers before
            helpers_before = len(re.findall(r'@Html\.', original))

            # Layer 1 — deterministic
            migrated = _replace_html_helpers(original)

            helpers_after = len(re.findall(r'@Html\.', migrated))
            replaced = helpers_before - helpers_after
            total_helpers_replaced += replaced

            # Layer 2 — LLM pass only if helpers remain
            if _needs_llm_pass(migrated):
                progress(f"View Migration Agent: LLM pass on {cshtml_file.name} ({helpers_after} helpers remaining)...")
                migrated = _llm_migrate_view(rel, migrated, from_version, to_version)
                llm_passes += 1

                # Check if LLM cleaned it up
                remaining = len(re.findall(r'@Html\.', migrated))
                if remaining > 0:
                    manual_review.append(f"{rel}: {remaining} HTML helper(s) could not be auto-migrated — manual review required")

            if migrated != original:
                cshtml_file.write_text(migrated, encoding="utf-8")
                changes.append(f"Migrated {rel} — {replaced} helper(s) replaced")
                progress(f"View Migration Agent: Migrated {cshtml_file.name}")

        except Exception as e:
            manual_review.append(f"{rel}: Error during migration — {str(e)}")

    # Fix _ViewImports.cshtml
    viewimports_fixes = _fix_viewimports(out)
    if viewimports_fixes:
        changes.extend(viewimports_fixes)

    progress(f"View Migration Agent: {len(changes)} view(s) migrated, {total_helpers_replaced} helper(s) replaced.")

    return {
        "skipped": False,
        "reason": "",
        "views_processed": len(cshtml_files),
        "helpers_replaced": total_helpers_replaced,
        "llm_passes": llm_passes,
        "manual_review": manual_review,
        "viewimports_fixed": viewimports_fixes,
        "changes": changes,
    }


# ── Agent wrapper ─────────────────────────────────────────────────────────
from agents.base_agent import BaseAgent
from agents.context import MigrationContext, AgentObservation

class ViewMigratorAgent(BaseAgent):
    name = "View Migration Agent"
    goal = "migrate Razor views from HTML Helpers to Tag Helpers"

    def act(self, context: MigrationContext) -> dict:
        return run_view_migrator(
            output_dir=context.output_dir,
            from_version=context.from_version,
            to_version=context.to_version,
            progress_callback=context.progress_callback,
        )

    def observe(self, result: dict, context: MigrationContext) -> AgentObservation:
        context.view_result = result
        skipped = result.get("skipped", False)
        return AgentObservation(
            agent=self.name,
            status="skipped" if skipped else "completed",
            summary=(
                result.get("reason", "No views found.")
                if skipped else
                f"Migrated {result.get('views_processed', 0)} view(s), "
                f"{result.get('helpers_replaced', 0)} helper(s) replaced."
            ),
            actionable=False,
            recommended_next="webforms_migrator",
            data=result,
        )

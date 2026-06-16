"""
Build Error AI Agent — the missing piece in the pipeline.
Takes structured build errors from BuildValidatorAgent,
reads the specific broken .cs files, sends them to the LLM
with the exact error context, and rewrites only those files.

This is what closes the feedback loop:
  migrate → build fails → LLM sees exact errors → fixes them → build retries
"""
from pathlib import Path
import re
from agents.base_agent import BaseAgent
from agents.context import MigrationContext, AgentObservation
from agents.llm import ask_with_system

SKIP_FOLDERS = {"obj", "bin", ".vs", ".git", "node_modules"}

SYSTEM_LLM_FIXER = """You are a .NET 8 build error fixer. You will be given a C# file and the exact build errors it produced.
Your job is to fix ONLY the errors listed — do not change anything else.
Rules:
- Fix each error by its exact error code and line number
- Do not remove any business logic or methods
- Do not add new methods that were not there before
- Keep all using statements that are valid
- If error is CS0246/CS0234 (type not found) — add the correct using statement or replace with .NET 8 equivalent
- If error is CS0103 (name not found) — fix the reference or add the correct using
- If error is CS1061 (no definition) — replace with correct .NET 8 API
- If error is CS0029/CS0266 (cannot convert) — fix the type mismatch
- Return ONLY the fixed C# code inside a ```csharp block. Nothing else."""


def _extract_code(response: str) -> str:
    match = re.search(r'```(?:csharp|cs)?\s*(.*?)\s*```', response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return response.strip()


def _find_file_in_output(output_dir: Path, error_file_path: str) -> Path | None:
    """
    Find the actual file in output directory from the error file path.
    Build errors give absolute or relative paths — we need to locate the file.
    """
    # Try direct path first
    direct = Path(error_file_path)
    if direct.exists():
        return direct

    # Try finding by filename in output dir
    filename = Path(error_file_path).name
    for f in output_dir.rglob(filename):
        if not any(p.lower() in SKIP_FOLDERS for p in f.parts):
            return f

    return None


def _group_errors_by_file(build_errors: list) -> dict:
    """Group build errors by file path for efficient LLM calls."""
    grouped = {}
    for error in build_errors:
        file_path = error.get("file", "")
        if not file_path:
            continue
        if file_path not in grouped:
            grouped[file_path] = []
        grouped[file_path].append(error)
    return grouped


class BuildErrorAIAgent(BaseAgent):
    """
    LLM-powered build error fixer.

    Perceives: build errors from context
    Decides: only runs if there are LLM-fixable errors (CS* codes)
    Acts: sends each broken file + its errors to LLM for targeted rewrite
    Observes: reports which files were fixed, which could not be fixed
    """

    name = "Build Error AI Agent"
    goal = "fix C# files causing build errors using LLM with exact error context"

    def perceive(self, context: MigrationContext) -> dict:
        return {
            "build_errors": context.build_errors,
            "fixable_errors": context.get_fixable_errors(),
            "error_files": context.get_unique_error_files(),
            "attempt": context.attempts,
        }

    def decide(self, context: MigrationContext) -> bool:
        """Only run if there are LLM-fixable compiler errors."""
        fixable = context.get_fixable_errors()
        if not fixable:
            return False
        # Don't run if only environment errors — LLM cannot fix missing databases
        if context.has_only_environment_errors():
            return False
        return True

    def act(self, context: MigrationContext) -> dict:
        output_path = Path(context.output_dir)
        fixable_errors = context.get_fixable_errors()
        grouped = _group_errors_by_file(fixable_errors)

        files_fixed = []
        files_failed = []
        total_errors_addressed = 0

        for error_file_path, errors in grouped.items():
            actual_file = _find_file_in_output(output_path, error_file_path)
            if not actual_file:
                files_failed.append(f"{error_file_path}: file not found in output")
                continue

            try:
                original_content = actual_file.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                files_failed.append(f"{error_file_path}: could not read — {e}")
                continue

            # Build error summary for LLM
            error_lines = []
            for err in errors:
                line = err.get("line", "?")
                code = err.get("code", "?")
                message = err.get("message", "")
                error_lines.append(f"  Line {line} — {code}: {message}")
            error_summary = "\n".join(error_lines)

            context.progress(
                f"Build Error AI Agent: fixing {actual_file.name} "
                f"({len(errors)} error(s) on attempt {context.attempts + 1})..."
            )

            prompt = f"""Fix the following build errors in this C# file.

File: {actual_file.name}

Build Errors:
{error_summary}

Current file content:
```csharp
{original_content[:8000]}
```

Fix ONLY the errors listed above. Return the complete fixed file."""

            try:
                response = ask_with_system(
                    SYSTEM_LLM_FIXER, prompt, agent_name="Build Error AI Agent"
                )
                fixed_content = _extract_code(response)

                if fixed_content and fixed_content != original_content:
                    actual_file.write_text(fixed_content, encoding="utf-8")
                    files_fixed.append(actual_file.name)
                    total_errors_addressed += len(errors)
                    context.progress(
                        f"Build Error AI Agent: fixed {actual_file.name} — "
                        f"{len(errors)} error(s) addressed"
                    )
                else:
                    files_failed.append(f"{actual_file.name}: LLM returned no changes")

            except Exception as e:
                files_failed.append(f"{actual_file.name}: LLM call failed — {e}")

        # Record in fix history
        context.fix_history.append({
            "attempt": context.attempts + 1,
            "files_fixed": files_fixed,
            "files_failed": files_failed,
            "errors_addressed": total_errors_addressed,
        })

        return {
            "success": len(files_fixed) > 0,
            "files_fixed": files_fixed,
            "files_failed": files_failed,
            "errors_addressed": total_errors_addressed,
            "summary": (
                f"Fixed {len(files_fixed)} file(s), "
                f"{total_errors_addressed} error(s) addressed. "
                f"{len(files_failed)} file(s) could not be fixed."
            ),
        }

    def observe(self, result: dict, context: MigrationContext) -> AgentObservation:
        files_fixed = result.get("files_fixed", [])
        success = result.get("success", False)
        return AgentObservation(
            agent=self.name,
            status="completed" if success else "failed",
            summary=result.get("summary", "Build Error AI Agent completed."),
            actionable=True,
            recommended_next="build_validator",   # always retry build after fixing
            data=result,
            errors=result.get("files_failed", []),
        )

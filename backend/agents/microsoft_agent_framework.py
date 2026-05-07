from __future__ import annotations

from typing import Any


class MicrosoftAgentFrameworkAdapter:
    """Small adapter so the app can run today and use Microsoft Agent Framework when installed.

    Microsoft Agent Framework is the intended orchestration layer for this agent.
    The deterministic migration functions remain normal tools so builds, file edits,
    and validation are predictable even when the LLM is unavailable.
    """

    def __init__(self) -> None:
        self.available = False
        self.detail = "Microsoft Agent Framework package not installed; using local workflow runner"
        try:
            import agent_framework  # type: ignore  # noqa: F401

            self.available = True
            self.detail = "Microsoft Agent Framework available"
        except Exception:
            try:
                import microsoft_agents  # type: ignore  # noqa: F401

                self.available = True
                self.detail = "Microsoft agent runtime available"
            except Exception:
                pass

    def status(self) -> dict[str, Any]:
        return {"name": "Microsoft Agent Framework", "available": self.available, "detail": self.detail}

    def planned_agents(self) -> list[dict[str, str]]:
        return [
            {"name": "Ingestion Agent", "role": "Safely extracts uploads and creates job workspace"},
            {"name": "Inventory Agent", "role": "Scans solutions, projects, frameworks, packages, and migration blockers"},
            {"name": "Migration Planner Agent", "role": "Builds target-version plan and risk gates"},
            {"name": "Project Upgrade Agent", "role": "Upgrades csproj, package references, and SDK style"},
            {"name": "Code Migration Agent", "role": "Uses LLM to rewrite source files with project context"},
            {"name": "Build Validator Agent", "role": "Runs dotnet restore/build/test tools"},
            {"name": "Build Fix Agent", "role": "Uses compiler output to iterate fixes"},
            {"name": "Report Agent", "role": "Produces migration report, manual fixes, dependency map, and zip output"},
        ]

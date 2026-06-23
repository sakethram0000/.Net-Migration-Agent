"""
Migration Context — shared state object passed between all agents.
Every agent reads from this and writes its observations back to it.
This is what makes the system an agent system, not an automation pipeline.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from pathlib import Path
import time


@dataclass
class AgentObservation:
    """
    Structured result every agent returns after running.
    Orchestrator reads this to decide what to do next.
    """
    agent: str
    status: str                          # "completed" | "failed" | "skipped"
    summary: str                         # human-readable one-liner
    actionable: bool = False             # does orchestrator need to act on this?
    recommended_next: str = ""           # which agent should run next
    data: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class OrchestratorDecision:
    """
    Every decision the orchestrator makes is logged here.
    Gives full audit trail of why things happened.
    """
    round: int
    observation: str        # what the orchestrator saw
    decision: str           # what it decided to do
    reason: str             # why it made that decision
    timestamp: float = field(default_factory=time.time)


@dataclass
class MigrationContext:
    """
    The shared brain of the entire migration pipeline.
    Every agent reads from this and writes back to it.
    Orchestrator uses this to make decisions.
    """
    # ── Identity ──────────────────────────────────────────────────────────
    job_id: str
    from_version: str
    to_version: str
    upload_dir: str
    output_dir: str

    # ── Stack profile (optional — None means backend-only migration) ───────
    # source_frontend: detected frontend framework in uploaded project
    # target_frontend: desired frontend framework in migrated output
    # Both default to None — when None, pipeline runs exactly as before
    source_frontend: Optional[str] = None   # e.g. "angularjs", "jquery", "react", "vue"
    target_frontend: Optional[str] = None   # e.g. "react", "angular", "vue", "blazor"

    # ── User info (set from JWT token) ─────────────────────────────────
    user_id: str = ""
    user_email: str = ""
    user_role: str = "user"

    # ── Goal ──────────────────────────────────────────────────────────────
    goal: str = "produce a working .NET 8+ build from the uploaded project"
    goal_achieved: bool = False

    # ── Orchestrator state ────────────────────────────────────────────────
    current_agent: str = ""
    status: str = "running"              # "running" | "completed" | "failed"
    attempts: int = 0
    max_attempts: int = 3

    # ── Agent results (written by each agent) ─────────────────────────────
    analysis: dict = field(default_factory=dict)
    migrated_files: dict = field(default_factory=dict)
    auth_result: dict = field(default_factory=dict)
    view_result: dict = field(default_factory=dict)
    webforms_result: dict = field(default_factory=dict)
    blazor_result: dict = field(default_factory=dict)
    fix_result: dict = field(default_factory=dict)
    guardrail_result: dict = field(default_factory=dict)
    build_result: dict = field(default_factory=dict)

    # ── Build error tracking (key for orchestrator decisions) ─────────────
    build_errors: list = field(default_factory=list)
    build_passed: bool = False
    files_with_errors: list = field(default_factory=list)   # specific files that failed

    # ── Memory — what was tried ───────────────────────────────────────────
    fix_history: list = field(default_factory=list)         # list of AgentObservation
    decisions: list = field(default_factory=list)           # list of OrchestratorDecision
    observations: list = field(default_factory=list)        # all agent observations in order

    # ── Progress reporting ────────────────────────────────────────────────
    progress_callback: Optional[Callable[[str], None]] = None

    # ── Token tracking ────────────────────────────────────────────────────
    token_stats: dict = field(default_factory=dict)

    # ── Timing ────────────────────────────────────────────────────────────
    started_at: float = field(default_factory=time.time)

    # ── Helpers ───────────────────────────────────────────────────────────

    def progress(self, message: str):
        """Send progress update — updates current_agent display and calls callback."""
        if self.progress_callback:
            self.progress_callback(message)

    def record_observation(self, observation: AgentObservation):
        """Every agent calls this after running to log its result."""
        self.observations.append(observation)
        self.progress(f"{observation.agent}: {observation.summary}")

    def record_decision(self, decision: OrchestratorDecision):
        """Orchestrator calls this every time it makes a decision."""
        self.decisions.append(decision)
        self.progress(f"Orchestrator [{decision.decision}]: {decision.reason}")

    def get_fixable_errors(self) -> list:
        """
        Return only errors the LLM can fix (CS* compiler errors).
        Filters out environment/package errors that need different handling.
        """
        fixable_codes = {"CS0246", "CS0234", "CS0103", "CS1061", "CS0117",
                         "CS0029", "CS0019", "CS0266", "CS0161", "CS0165",
                         "CS0168", "CS0219", "CS1503", "CS0535", "CS0738"}
        return [e for e in self.build_errors if e.get("code", "") in fixable_codes]

    def get_package_errors(self) -> list:
        """Return package/restore errors (NU* codes) — need deterministic fix."""
        return [e for e in self.build_errors
                if e.get("code", "").startswith("NU") or
                e.get("code", "").startswith("MSB")]

    def get_environment_errors(self) -> list:
        """Return errors that indicate missing external services — cannot auto-fix."""
        env_keywords = ["connection string", "database", "server", "host=", "mongodb"]
        return [e for e in self.build_errors
                if any(k in e.get("message", "").lower() for k in env_keywords)]

    def get_unique_error_files(self) -> list:
        """Return deduplicated list of files that have build errors."""
        seen = set()
        files = []
        for e in self.build_errors:
            f = e.get("file", "")
            if f and f not in seen:
                seen.add(f)
                files.append(f)
        return files

    def has_fixable_errors(self) -> bool:
        return len(self.get_fixable_errors()) > 0

    def has_only_environment_errors(self) -> bool:
        env = self.get_environment_errors()
        fixable = self.get_fixable_errors()
        pkg = self.get_package_errors()
        return len(env) > 0 and len(fixable) == 0 and len(pkg) == 0

    def elapsed_seconds(self) -> float:
        return round(time.time() - self.started_at, 1)

    def to_summary_dict(self) -> dict:
        """Return a clean summary dict for the API response."""
        return {
            "job_id": self.job_id,
            "goal": self.goal,
            "goal_achieved": self.goal_achieved,
            "status": self.status,
            "attempts": self.attempts,
            "build_passed": self.build_passed,
            "elapsed_seconds": self.elapsed_seconds(),
            "decisions_made": len(self.decisions),
            "agents_run": len(self.observations),
            "triggered_by": self.user_email,
            "user_role": self.user_role,
            "decision_log": [
                {
                    "round": d.round,
                    "observation": d.observation,
                    "decision": d.decision,
                    "reason": d.reason,
                }
                for d in self.decisions
            ],
        }

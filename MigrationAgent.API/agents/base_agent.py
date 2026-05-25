"""
Base Agent — every agent in the system inherits from this.
Defines the perceive → decide → act → observe pattern.
This is what makes each component an agent, not just a function.
"""
from abc import ABC, abstractmethod
from agents.context import MigrationContext, AgentObservation
import time


class BaseAgent(ABC):
    """
    Base class for all migration agents.

    Every agent must implement:
        - act(context)  → do the actual work, return raw result dict

    Every agent can override:
        - perceive(context) → what does this agent see in the context?
        - decide(context)   → should this agent run given current context?
        - observe(result)   → build structured observation from raw result

    The orchestrator calls run(context) which executes the full cycle.
    """

    name: str = "BaseAgent"
    goal: str = "perform migration task"

    # ── Core lifecycle ────────────────────────────────────────────────────

    def perceive(self, context: MigrationContext) -> dict:
        """
        Read relevant state from context.
        Override to extract what this agent needs to make its decision.
        Default: returns basic project info.
        """
        return {
            "upload_dir": context.upload_dir,
            "output_dir": context.output_dir,
            "from_version": context.from_version,
            "to_version": context.to_version,
            "attempts": context.attempts,
            "build_passed": context.build_passed,
        }

    def decide(self, context: MigrationContext) -> bool:
        """
        Should this agent run given the current context?
        Override to add conditional logic.
        Default: always run.
        """
        return True

    @abstractmethod
    def act(self, context: MigrationContext) -> dict:
        """
        Do the actual work. Must be implemented by every agent.
        Returns a raw result dict.
        """
        pass

    def observe(self, result: dict, context: MigrationContext) -> AgentObservation:
        """
        Build a structured observation from the raw result.
        Override to provide agent-specific observation logic.
        Default: wraps result in a basic observation.
        """
        success = result.get("success", True)
        return AgentObservation(
            agent=self.name,
            status="completed" if success else "failed",
            summary=result.get("summary", f"{self.name} completed."),
            actionable=False,
            recommended_next="",
            data=result,
            errors=result.get("errors", []),
        )

    def run(self, context: MigrationContext) -> AgentObservation:
        """
        Full agent lifecycle:
        perceive → decide → act → observe → record

        This is what the orchestrator calls.
        Never override this — override the individual methods instead.
        """
        context.current_agent = self.name
        context.progress(f"{self.name}: starting...")

        # Perceive
        perception = self.perceive(context)

        # Decide
        should_run = self.decide(context)
        if not should_run:
            obs = AgentObservation(
                agent=self.name,
                status="skipped",
                summary=f"{self.name}: skipped — conditions not met.",
                actionable=False,
            )
            context.record_observation(obs)
            return obs

        # Act
        try:
            result = self.act(context)
        except Exception as e:
            obs = AgentObservation(
                agent=self.name,
                status="failed",
                summary=f"{self.name}: failed with error — {str(e)}",
                actionable=False,
                data={},
                errors=[str(e)],
            )
            context.record_observation(obs)
            context.progress(f"{self.name} warning: {str(e)}")
            return obs

        # Observe
        obs = self.observe(result, context)

        # Record
        context.record_observation(obs)

        return obs

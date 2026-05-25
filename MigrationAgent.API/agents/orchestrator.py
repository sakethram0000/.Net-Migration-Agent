"""
Migration Orchestrator — the brain of the agent system.
Reads context, makes decisions, runs agents in the right order.
This is what makes the system an agent system, not an automation pipeline.

Phase 1: Run all agents once in sequence (analyze → migrate → auth → views → fix → guardrails)
Phase 2: Goal-driven loop (build → decide → llm_fix → retry) until goal achieved or max attempts
"""
from agents.context import MigrationContext, OrchestratorDecision
from agents.analyzer import AnalyzerAgent
from agents.migrator import MigratorAgent
from agents.auth_agent import AuthAgentWrapper
from agents.view_migrator import ViewMigratorAgent
from agents.webforms_migrator import WebFormsMigratorAgent
from agents.blazor_migrator import BlazorMigratorAgent
from agents.fixer import FixerAgentWrapper
from agents.guardrail_agent import GuardrailAgentWrapper
from agents.build_validator import BuildValidatorAgentWrapper
from agents.llm_fixer_agent import LLMFixerAgent
from agents.reporter import store_context, ReporterAgent


class MigrationOrchestrator:
    """
    The orchestrator runs the full migration pipeline.
    It does not just call steps — it reads context and decides what to do next.

    Phase 1 — always runs once:
        Analyzer → Migrator → Auth → Views → WebForms → Blazor → Fixer → Guardrails

    Phase 2 — goal-driven loop (max 3 rounds):
        BuildValidator → (if failed) decide → LLMFixer or deterministic fix → retry
    """

    def __init__(self):
        # Phase 2 agents — used in goal-driven build-fix loop
        self.build_validator = BuildValidatorAgentWrapper()
        self.llm_fixer       = LLMFixerAgent()
        self.fixer           = FixerAgentWrapper()

    def run(self, context: MigrationContext) -> MigrationContext:
        """
        Main entry point. Runs full pipeline and returns updated context.
        """
        context.progress("Orchestrator: Starting migration pipeline...")

        # ── Phase 1a: Analyze first — orchestrator reads result to plan ───
        context.progress("Orchestrator: Running Analyzer Agent...")
        analyzer = AnalyzerAgent()
        analyzer.run(context)

        # Orchestrator reads analysis and decides which agents are needed
        ui_profile  = context.analysis.get("ui_profile", {})
        ui_type     = ui_profile.get("ui_type", "none")
        has_cshtml  = ui_profile.get("cshtml_count", 0) > 0
        has_aspx    = ui_profile.get("aspx_count", 0) > 0
        has_razor   = ui_profile.get("razor_count", 0) > 0
        complexity  = context.analysis.get("complexity", {}).get("level", "Low")

        self._decide(
            context,
            observation=(
                f"Analysis complete — ui_type={ui_type}, "
                f"cshtml={has_cshtml}, aspx={has_aspx}, razor={has_razor}, "
                f"complexity={complexity}"
            ),
            decision="plan_phase1",
            reason=(
                f"Orchestrator planned agents based on analysis: "
                f"ViewMigrator={'yes' if has_cshtml else 'skip'}, "
                f"WebFormsMigrator={'yes' if has_aspx else 'skip'}, "
                f"BlazorMigrator={'yes' if has_razor else 'skip'}."
            )
        )

        # ── Phase 1b: Run migrator ────────────────────────────────────────
        migrator = MigratorAgent()
        obs = migrator.run(context)
        if obs.status == "failed":
            self._decide(
                context,
                observation="Migrator failed — no output to work with",
                decision="abort",
                reason="LLM migration failed. Cannot proceed without migrated files."
            )
            context.status = "failed"
            return context

        # ── Phase 1c: Auth — always needed ───────────────────────────────
        AuthAgentWrapper().run(context)

        # ── Phase 1d: UI agents — orchestrator decides based on analysis ──
        if has_cshtml:
            self._decide(
                context,
                observation=f"{ui_profile.get('cshtml_count', 0)} .cshtml file(s) found",
                decision="run_view_migrator",
                reason="Razor views detected — running View Migration Agent."
            )
            ViewMigratorAgent().run(context)
        else:
            self._decide(
                context,
                observation="No .cshtml files found",
                decision="skip_view_migrator",
                reason="No Razor views in project — View Migration Agent skipped."
            )

        if has_aspx:
            self._decide(
                context,
                observation=f"{ui_profile.get('aspx_count', 0)} .aspx/.ascx/.master file(s) found",
                decision="run_webforms_migrator",
                reason="Web Forms files detected — running Web Forms Migration Agent."
            )
            WebFormsMigratorAgent().run(context)
        else:
            self._decide(
                context,
                observation="No Web Forms files found",
                decision="skip_webforms_migrator",
                reason="No Web Forms in project — Web Forms Agent skipped."
            )

        if has_razor:
            self._decide(
                context,
                observation=f"{ui_profile.get('razor_count', 0)} .razor file(s) found",
                decision="run_blazor_migrator",
                reason="Blazor components detected — running Blazor Migration Agent."
            )
            BlazorMigratorAgent().run(context)
        else:
            self._decide(
                context,
                observation="No .razor files found",
                decision="skip_blazor_migrator",
                reason="No Blazor components in project — Blazor Agent skipped."
            )

        # ── Phase 1e: Fix + Guardrails — always run ───────────────────────
        FixerAgentWrapper().run(context)
        GuardrailAgentWrapper().run(context)

        context.progress("Orchestrator: Phase 1 complete — all required agents ran.")

        # ── Phase 2: Goal-driven build-fix loop ───────────────────────────
        context.progress(
            f"Orchestrator: Phase 2 — goal-driven build loop "
            f"(max {context.max_attempts} attempt(s))..."
        )

        while not context.goal_achieved and context.attempts < context.max_attempts:

            context.progress(
                f"Orchestrator: Build attempt {context.attempts + 1}/{context.max_attempts}..."
            )

            # Run build validator — it writes errors to context
            build_obs = self.build_validator.run(context)

            # ── Decision point ────────────────────────────────────────────
            if context.goal_achieved:
                self._decide(
                    context,
                    observation=f"Build passed on attempt {context.attempts + 1}",
                    decision="goal_achieved",
                    reason="dotnet build succeeded — migration complete."
                )
                break

            if build_obs.status == "skipped":
                self._decide(
                    context,
                    observation="dotnet CLI not available",
                    decision="skip_build",
                    reason="No dotnet CLI found — treating as complete. Validate locally."
                )
                context.goal_achieved = True
                break

            # Read what kind of errors we have
            fixable = context.get_fixable_errors()
            pkg_errors = context.get_package_errors()
            env_errors = context.get_environment_errors()
            error_count = len(context.build_errors)

            if context.has_only_environment_errors():
                self._decide(
                    context,
                    observation=f"Build failed with environment errors: {[e.get('message','') for e in env_errors[:2]]}",
                    decision="escalate_to_user",
                    reason="Errors require external services (database/redis/etc) — cannot auto-fix. User must configure environment."
                )
                break

            if fixable:
                self._decide(
                    context,
                    observation=f"Build failed — {len(fixable)} LLM-fixable error(s) in {len(context.get_unique_error_files())} file(s)",
                    decision="run_llm_fixer",
                    reason=f"Compiler errors (CS*) detected — sending broken files to LLM Fixer with exact error context."
                )
                self.llm_fixer.run(context)

            elif pkg_errors:
                self._decide(
                    context,
                    observation=f"Build failed — {len(pkg_errors)} package/MSBuild error(s)",
                    decision="run_deterministic_fixer",
                    reason="Package version conflicts or MSBuild errors — running deterministic fixer."
                )
                self.fixer.run(context)

            else:
                self._decide(
                    context,
                    observation=f"Build failed — {error_count} error(s), no known fix strategy",
                    decision="give_up",
                    reason="No fixable errors identified — reporting failure with full error details."
                )
                break

            context.attempts += 1

        # ── Final status ──────────────────────────────────────────────────
        if context.goal_achieved:
            context.status = "completed"
            context.progress(
                f"Orchestrator: Goal achieved — build passed after "
                f"{context.attempts} fix attempt(s)."
            )
        else:
            context.status = "completed"
            context.progress(
                f"Orchestrator: Pipeline complete — build needs review. "
                f"{len(context.build_errors)} error(s) remain after "
                f"{context.attempts} attempt(s)."
            )

        # ── Reporter Agent — reads from context, no re-running ────────────
        store_context(context)
        ReporterAgent().run(context)

        return context

    def _decide(
        self,
        context: MigrationContext,
        observation: str,
        decision: str,
        reason: str,
    ):
        """Log an orchestrator decision to context."""
        d = OrchestratorDecision(
            round=context.attempts,
            observation=observation,
            decision=decision,
            reason=reason,
        )
        context.record_decision(d)


def run_orchestrator(
    job_id: str,
    upload_dir: str,
    output_dir: str,
    from_version: str,
    to_version: str,
    progress_callback=None,
    user_id: str = "",
    user_email: str = "",
    user_role: str = "user",
) -> MigrationContext:
    """
    Main entry point called from migration.py.
    Creates context, runs orchestrator, returns final context.
    """
    context = MigrationContext(
        job_id=job_id,
        from_version=from_version,
        to_version=to_version,
        upload_dir=upload_dir,
        output_dir=output_dir,
        progress_callback=progress_callback,
        user_id=user_id,
        user_email=user_email,
        user_role=user_role,
    )

    orchestrator = MigrationOrchestrator()
    return orchestrator.run(context)

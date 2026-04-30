"""CLI command class for `rdst ask`.

Mirrors `features/analyze/cli/command.py:AnalyzeCommand` and
`features/top/cli/command.py:TopCommand`: the per-feature CLI layer
owns the `command_run_sync` telemetry CM, so the dispatcher in
`shared/cli/rdst_cli.py` stays a thin pass-through and the CM lives
on the same side of the layering as the web CM in
`features/ask/api/routes.py`.
"""

from __future__ import annotations

from typing import Optional, Union

from shared.cli.types import RdstResult


class AskCommand:
    """Handles `rdst ask` CLI execution and telemetry."""

    def resolve_question(
        self,
        question: Optional[str],
        no_interactive: bool,
    ) -> Union[RdstResult, str]:
        """Resolve the question to ask.

        If `question` is supplied, return it. Otherwise prompt the user
        interactively (only if stdin is a TTY and `--no-interactive` is
        not set). Returns either the resolved question string or an
        `RdstResult` describing why no question could be obtained — the
        caller short-circuits on the latter.

        Owned here (not the dispatcher) so input handling matches
        `AnalyzeCommand.resolve_input`: every CLI-level concern lives
        in the per-feature command class.
        """
        if question:
            return question

        if no_interactive:
            return RdstResult(
                False,
                'ask requires a question in --no-interactive mode. '
                'Example: rdst ask "How many users are there?" --no-interactive',
            )

        import sys

        if not sys.stdin.isatty():
            return RdstResult(
                False,
                'ask requires a question. Example: rdst ask "How many users are there?"',
            )
        try:
            question = input("Question: ").strip()
        except (EOFError, KeyboardInterrupt):
            return RdstResult(False, "Cancelled")
        if not question:
            return RdstResult(False, "ask requires a question")
        return question

    def execute(
        self,
        question: Optional[str] = None,
        target: Optional[str] = None,
        dry_run: bool = False,
        timeout: int = 30,
        verbose: bool = False,
        agent_mode: bool = False,
        no_interactive: bool = False,
        **kwargs,
    ) -> RdstResult:
        """Run an ask query and emit a single `ask_run` telemetry event.

        The CM is owned here (not in the dispatcher) for symmetry with
        analyze/top/scan; the web side wraps the same way in
        `features/ask/api/routes.py`.
        """
        from shared.config.targets import TargetsConfig
        from shared.telemetry import telemetry

        resolved = self.resolve_question(question, no_interactive)
        if isinstance(resolved, RdstResult):
            return resolved
        question = resolved

        target_engine = "unknown"
        if target:
            try:
                cfg = TargetsConfig()
                cfg.load()
                tc = cfg.get(target)
                if tc:
                    target_engine = tc.get("engine", "unknown")
            except Exception:
                pass

        result: RdstResult
        with telemetry.command_run_sync(
            "ask",
            source="cli",
            target_engine=target_engine,
            agent_mode=agent_mode,
            dry_run=dry_run,
        ) as run:
            try:
                result = self._execute_impl(
                    question=question,
                    target=target,
                    dry_run=dry_run,
                    timeout=timeout,
                    verbose=verbose,
                    agent_mode=agent_mode,
                    no_interactive=no_interactive,
                    **kwargs,
                )
                run.success = result.ok
                if not result.ok and run.error_type is None:
                    run.error_type = "command_unsuccessful"
            except Exception as e:
                run.error(e)
                try:
                    telemetry.report_crash(
                        e, context={"command": "ask", "target": target}
                    )
                except Exception:
                    pass
                result = RdstResult(False, f"ask command failed: {e}")

        return result

    def _execute_impl(
        self,
        question: Optional[str] = None,
        target: Optional[str] = None,
        dry_run: bool = False,
        timeout: int = 30,
        verbose: bool = False,
        agent_mode: bool = False,
        no_interactive: bool = False,
        **kwargs,
    ) -> RdstResult:
        """Inner ask body — wrapped by `execute` for telemetry."""
        import asyncio

        try:
            from features.ask.engine.ask3.input_handler import (
                AskInputHandler,
                NonInteractiveInputHandler,
            )
            from features.ask.engine.ask3.renderer import AskRenderer
            from features.ask.events import (
                AskClarificationNeededEvent,
                AskResultEvent,
                AskErrorEvent,
            )
            from features.ask.models import AskInput, AskOptions
            from features.ask.service import AskService
        except ImportError as import_err:
            return RdstResult(
                False,
                "The 'ask' command is not available. "
                "Some required components could not be loaded. "
                "Try reinstalling rdst or check your installation. "
                f"(detail: {import_err})",
            )

        try:
            if not question:
                return RdstResult(
                    False,
                    'Question required. Usage: rdst ask "your question here" --target <target>',
                )

            renderer = AskRenderer(verbose=verbose)
            input_handler = (
                NonInteractiveInputHandler() if no_interactive else AskInputHandler()
            )

            service = AskService()

            input_data = AskInput(
                question=question,
                target=target,
                source="cli",
            )
            options_data = AskOptions(
                dry_run=dry_run,
                timeout_seconds=timeout,
                verbose=verbose,
                agent_mode=agent_mode,
                no_interactive=no_interactive,
            )

            result_event = None
            error_event = None

            async def _run_ask():
                nonlocal result_event, error_event

                async for event in service.ask(input_data, options_data):
                    renderer.render(event)

                    if isinstance(event, AskClarificationNeededEvent):
                        try:
                            answers = input_handler.collect_clarifications(event)
                            async for resume_event in service.resume(
                                event.session_id, answers
                            ):
                                renderer.render(resume_event)
                                if isinstance(resume_event, AskResultEvent):
                                    result_event = resume_event
                                elif isinstance(resume_event, AskErrorEvent):
                                    error_event = resume_event
                        except (EOFError, KeyboardInterrupt):
                            error_event = AskErrorEvent(
                                type="error",
                                message="Cancelled by user",
                                phase="clarify",
                            )
                            renderer.render(error_event)
                            return

                    elif isinstance(event, AskResultEvent):
                        result_event = event

                    elif isinstance(event, AskErrorEvent):
                        error_event = event

            asyncio.run(_run_ask())

            if result_event:
                message = f"\nSQL: {result_event.sql}\n"
                if not dry_run:
                    message += f"Rows: {result_event.row_count}\n"
                    message += f"Execution time: {result_event.execution_time_ms:.1f}ms\n"
                message += f"LLM calls: {result_event.llm_calls}\n"
                message += f"Total tokens: {result_event.total_tokens}\n"

                return RdstResult(
                    ok=True,
                    message=message,
                    data={
                        "sql": result_event.sql,
                        "rows": result_event.rows,
                        "columns": result_event.columns,
                        "row_count": result_event.row_count,
                        "execution_time_ms": result_event.execution_time_ms,
                        "llm_calls": result_event.llm_calls,
                        "total_tokens": result_event.total_tokens,
                        "status": "success",
                    },
                )

            elif error_event:
                if "cancelled" in error_event.message.lower():
                    return RdstResult(ok=False, message="Operation cancelled by user")
                # Renderer already displayed the error; return empty
                # message to avoid duplicate print in rdst.py main().
                return RdstResult(
                    ok=False,
                    message="",
                    data={"phase": error_event.phase} if error_event.phase else {},
                )

            else:
                return RdstResult(False, "Ask command failed unexpectedly")

        except Exception as e:
            import traceback

            traceback.print_exc()
            return RdstResult(False, f"ask command failed: {e}")


__all__ = ["AskCommand"]

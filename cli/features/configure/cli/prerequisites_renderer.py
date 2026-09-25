"""Terminal rendering of the monitoring prerequisites check."""

from __future__ import annotations

from typing import Any

from shared.ui import Group, MessagePanel, StyledPanel, StyleTokens, Text

_MARK = {
    "ok": ("PASS", StyleTokens.SUCCESS),
    "missing": ("FIX ", StyleTokens.WARNING),
    "warn": ("TIP ", StyleTokens.MUTED),
    "waiting": ("WAIT", StyleTokens.MUTED),
    "unknown": ("?   ", StyleTokens.MUTED),
}


def prerequisites_panel(result: dict[str, Any]) -> Any:
    """One panel: a line per check, then the fix for each failing check."""
    target = result.get("target") or "target"
    if result.get("connection_error"):
        return MessagePanel(
            f"Couldn't check monitoring prerequisites: {result['connection_error']}",
            variant="warning",
            title=f"Monitoring setup for {target}",
        )
    lines: list[Any] = []
    provider = result.get("provider_name")
    if provider:
        lines.append(Text(f"Detected: {provider}", style=StyleTokens.MUTED))
    fixes: list[Any] = []
    for check in result.get("checks") or []:
        mark, style = _MARK.get(check.get("status"), _MARK["unknown"])
        line = Text()
        line.append(f"{mark}  ", style=style)
        line.append(check.get("label") or check.get("id") or "")
        if not check.get("blocking", True) and check.get("status") != "ok":
            line.append("  (recommended)", style=StyleTokens.MUTED)
        lines.append(line)
        fix = check.get("fix")
        if fix and check.get("status") != "ok":
            fixes.append(Text(""))
            fixes.append(Text(check.get("label") or "", style=StyleTokens.EMPHASIS))
            if check.get("detail"):
                fixes.append(Text(check["detail"], style=StyleTokens.MUTED))
            fixes.append(Text(fix.get("summary") or ""))
            steps = list(fix.get("steps") or [])
            for index, step in enumerate(steps, start=1):
                fixes.append(Text(f"  {index}. {step}"))
            for statement in [*(fix.get("sql") or []), *(fix.get("commands") or [])]:
                fixes.append(Text(f"    {statement}", style=StyleTokens.PARAM))
            for index, step in enumerate(fix.get("after") or [], start=len(steps) + 1):
                fixes.append(Text(f"  {index}. {step}"))
            if fix.get("restart"):
                fixes.append(Text("  Needs a restart.", style=StyleTokens.WARNING))
            if fix.get("docs_url"):
                fixes.append(Text(f"  Guide: {fix['docs_url']}", style=StyleTokens.MUTED))
    if result.get("ready"):
        variant, title = "success", f"{target} is ready to monitor"
    else:
        variant, title = "warning", f"Monitoring setup for {target}"
    return StyledPanel(Group(*lines, *fixes), title=title, variant=variant, padding=(0, 1))


def is_green(result: dict[str, Any]) -> bool:
    """Everything monitoring needs passes, and every user's SQL is visible."""
    return bool(result.get("ready")) and not result.get("attention")


def _summary_word(result: dict[str, Any]) -> tuple[str, str]:
    if result.get("connection_error"):
        return "Can't connect", StyleTokens.ERROR
    if is_green(result):
        return "Ready to monitor", StyleTokens.SUCCESS
    if result.get("ready"):
        return "Can start, other users' SQL hidden", StyleTokens.WARNING
    count = sum(1 for check in result.get("checks") or []
                if check.get("blocking") and check.get("status") in ("missing", "unknown"))
    return ("1 fix needed" if count == 1 else f"{count} fixes needed"), StyleTokens.WARNING


def fleet_summary(results: list[dict[str, Any]]) -> Any:
    """One line per target: verdict, name and provider."""
    width = max(len(str(result.get("target") or "")) for result in results)
    lines = []
    for result in results:
        word, style = _summary_word(result)
        mark = "PASS" if is_green(result) else "FIX "
        line = Text()
        line.append(f"{mark}  ", style=StyleTokens.SUCCESS if is_green(result) else style)
        line.append(str(result.get("target") or "").ljust(width + 2), style=StyleTokens.EMPHASIS)
        line.append(word, style=style)
        if result.get("provider_name"):
            line.append(f"  {result['provider_name']}", style=StyleTokens.MUTED)
        lines.append(line)
    ready = sum(1 for result in results if result.get("ready"))
    title = f"Monitoring setup: {ready} of {len(results)} ready"
    variant = "success" if all(is_green(result) for result in results) else "warning"
    return StyledPanel(Group(*lines), title=title, variant=variant, padding=(0, 1))


def ready_line(results: list[dict[str, Any]]) -> Any:
    """The finish line once every target is green."""
    names = [str(result.get("target")) for result in results]
    text = Text()
    text.append("PASS  ", style=StyleTokens.SUCCESS)
    subject = names[0] if len(names) == 1 else f"All {len(names)} targets"
    verb = "is" if len(names) == 1 else "are"
    text.append(f"Ready to monitor: {subject} {verb} set up and RDST can see every query.",
                style=f"bold {StyleTokens.SUCCESS}")
    return text


def _interactive() -> bool:
    import sys

    return sys.stdin.isatty() and sys.stdout.isatty()


def _ask_recheck() -> bool:
    """True to check again, False to skip. EOF and Ctrl-C skip."""
    from shared.ui import Prompt

    try:
        answer = Prompt.ask(
            "Fix the items above in another window, then press Enter to check again "
            "(s to skip)",
            default="",
            show_default=False,
        )
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() not in {"s", "skip", "q", "n", "no"}


def run_prerequisite_flow(
    console: Any,
    targets: list[tuple[str, dict[str, Any]]],
    *,
    json_mode: bool = False,
    interactive: bool | None = None,
    check: Any = None,
    ask: Any = None,
) -> list[dict[str, Any]]:
    """Check monitoring prerequisites for newly added targets and show the result.

    One target gets the full panel; several get one line each, then a panel per
    target that needs a fix. In a terminal the user can re-check until every
    target is green or skip. JSON and non-interactive runs never prompt or print.
    """
    if not targets:
        return []
    if check is None:
        from features.configure.prerequisites import check_target as check
    ask = ask or _ask_recheck
    if interactive is None:
        interactive = _interactive()
    interactive = interactive and not json_mode

    configs = dict(targets)
    results = {name: check(name, config) for name, config in targets}
    while True:
        if not json_mode:
            ordered = [results[name] for name, _ in targets]
            if len(ordered) > 1:
                console.print(fleet_summary(ordered))
                for result in ordered:
                    if not is_green(result):
                        console.print(prerequisites_panel(result))
            else:
                console.print(prerequisites_panel(ordered[0]))
            if all(is_green(result) for result in ordered):
                console.print(ready_line(ordered))
        pending = [name for name, result in results.items() if not is_green(result)]
        if not pending or not interactive or not ask():
            break
        for name in pending:
            results[name] = check(name, configs[name])
    return [results[name] for name, _ in targets]


def prerequisites_data(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact machine-readable summary for command results and MCP."""
    return {
        "ready": all(result.get("ready") for result in results),
        "targets": results,
    }


def render_prerequisites(console: Any, target_name: str, target_config: dict[str, Any]) -> dict:
    return run_prerequisite_flow(console, [(target_name, target_config)])[0]

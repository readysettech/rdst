"""Trial balance display utilities.

Shows remaining trial credits after LLM calls when using the trial proxy.
"""

from __future__ import annotations


def show_trial_balance(result: dict, console) -> None:
    """Show trial balance after LLM call if applicable.

    Only displays when the result contains trial_remaining_cents
    (i.e., the request went through the trial proxy).
    """
    remaining = result.get("trial_remaining_cents")
    if remaining is None:
        return
    from ..ui import MessagePanel

    dollars = remaining / 100
    if dollars <= 0:
        console.print(
            MessagePanel(
                "Trial credits exhausted.\n\n"
                'To continue: export ANTHROPIC_API_KEY="sk-ant-..."\n'
                "Get a key at: https://console.anthropic.com/\n\n"
                "Want more trial credits? Email hello@readyset.io",
                variant="warning",
                title="Trial Exhausted",
            )
        )
    elif dollars < 0.50:
        console.print(
            MessagePanel(
                f"Trial balance: ${dollars:.2f} remaining",
                variant="warning",
                title="Low Balance",
            )
        )

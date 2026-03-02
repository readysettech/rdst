"""Trial balance display utilities.

Shows remaining trial tokens after LLM calls when using the trial proxy.

Internal accounting is in cents; display converts to Sonnet-equivalent tokens.
Token tiers: personal ≈ 275K tokens ($1.50), business ≈ 925K tokens ($5.00).
"""

from __future__ import annotations


# Sonnet blended rate at 4:1 input/output ratio (typical for RDST):
# (4 * $3 + 1 * $15) / 5 = $5.40 per MTok
# 1 cent = ~1,852 tokens.
_TOKENS_PER_CENT = 1_000_000 / 540  # ~1,852 tokens per cent


def cents_to_tokens(cents: float) -> int:
    """Convert cents to approximate Sonnet-equivalent tokens."""
    return int(cents * _TOKENS_PER_CENT)


def format_tokens(tokens: int) -> str:
    """Format token count as human-readable string (e.g., '150K', '1.2M')."""
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1_000:
        return f"{tokens // 1_000}K"
    return str(tokens)


def show_trial_balance(result: dict, console) -> None:
    """Show trial balance after LLM call if applicable.

    Only displays when the result contains trial_remaining_cents
    (i.e., the request went through the trial proxy).
    """
    remaining_cents = result.get("trial_remaining_cents")
    if remaining_cents is None:
        return
    from ..ui import MessagePanel

    limit_cents = result.get("trial_limit_cents", 500)  # default to business tier
    remaining_tokens = cents_to_tokens(remaining_cents)
    limit_tokens = cents_to_tokens(limit_cents)
    pct = int((remaining_cents / limit_cents) * 100) if limit_cents > 0 else 0

    if remaining_cents <= 0:
        console.print(
            MessagePanel(
                "Trial tokens exhausted.\n\n"
                'To continue: export ANTHROPIC_API_KEY="sk-ant-..."\n'
                "Get a key at: https://console.anthropic.com/\n\n"
                "Want more trial tokens? Email hello@readyset.io",
                variant="warning",
                title="Trial Exhausted",
            )
        )
    elif pct < 25:
        console.print(
            MessagePanel(
                f"Trial balance: {format_tokens(remaining_tokens)} of {format_tokens(limit_tokens)} tokens remaining ({pct}%)",
                variant="warning",
                title="Low Balance",
            )
        )

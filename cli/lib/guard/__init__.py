"""
Guard module - Reusable safety policies for rdst agents.

Guards define output masking, query restrictions, and execution limits
that can be applied to agents.

Usage:
    from lib.guard import GuardConfig, GuardManager

    # Create a guard
    config = GuardConfig(name="pii-safe")
    config.masking.patterns["*.email"] = "email"
    config.guards.require_where = True

    manager = GuardManager()
    manager.create(config)

    # Or create from natural language intent
    from lib.guard import derive_rules_from_intent

    config = derive_rules_from_intent(
        intent="Support agents can look up customers by ID. Protect passwords.",
        name="support-guard",
    )
"""

from .config import (
    GuardConfig,
    MaskingConfig,
    RestrictionsConfig,
    GuardsConfig,
    LimitsConfig,
    GUARDS_DIR,
)
from .manager import GuardManager, GuardNotFoundError, GuardExistsError
from .masking import mask_results, mask_value, get_masked_columns
from .checker import check_query, CheckResult
from .intent import derive_rules_from_intent, format_derived_rules

__all__ = [
    # Config
    "GuardConfig",
    "MaskingConfig",
    "RestrictionsConfig",
    "GuardsConfig",
    "LimitsConfig",
    "GUARDS_DIR",
    # Manager
    "GuardManager",
    "GuardNotFoundError",
    "GuardExistsError",
    # Masking
    "mask_results",
    "mask_value",
    "get_masked_columns",
    # Checker
    "check_query",
    "CheckResult",
    # Intent derivation
    "derive_rules_from_intent",
    "format_derived_rules",
]

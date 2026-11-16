"""The consent rule of the RDST distribution that's running, which the desktop shell passes
from its distributions.json."""

from __future__ import annotations

import os

OPT_IN = "opt_in"


def consent_opt_in() -> bool:
    """Diagnostics and AI data sharing wait for an opt-in (the Microsoft Store build)."""
    return os.environ.get("RDST_CONSENT", "").strip().lower() == OPT_IN

"""Distribution policy for uploading reports to Readyset-hosted services."""

from __future__ import annotations

import os


def hosted_reports_enabled() -> bool:
    value = os.environ.get("RDST_HOSTED_REPORTS", "disabled").strip().lower()
    return value not in {"0", "false", "no", "off", "disable", "disabled"}


def hosted_reports_disabled_message() -> str:
    return (
        "Hosted report upload is unavailable in this distribution. "
        "The report remains available locally."
    )

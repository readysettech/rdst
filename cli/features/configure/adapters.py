"""Adapters between typed configure models and compatibility payloads."""

from __future__ import annotations

from dataclasses import asdict

from .models import ConfigureOperation, TargetDetail, TargetSummary


LEGACY_OPERATION_ALIASES = {
    "default": ConfigureOperation.SET_DEFAULT.value,
    "edit": ConfigureOperation.UPDATE.value,
}


def operation_name(value: str | ConfigureOperation) -> str:
    if isinstance(value, ConfigureOperation):
        return value.value
    return LEGACY_OPERATION_ALIASES.get(value, value)


def cli_subcommand_to_operation(subcommand: str) -> str:
    return operation_name(subcommand)


def target_summary_to_dict(summary: TargetSummary) -> dict:
    return asdict(summary)


def target_detail_to_dict(detail: TargetDetail) -> dict:
    return asdict(detail)


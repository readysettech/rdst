"""User-facing messages for optional modules used during connection tests."""

from __future__ import annotations


def missing_module_message(
    error: ImportError,
    engine: str,
    *,
    multiline: bool = False,
) -> str:
    """Describe the module that actually failed to import."""
    module_name = getattr(error, "name", None)
    driver = next(
        (
            candidate
            for candidate in ("psycopg2", "pymysql")
            if module_name == candidate
            or (module_name and module_name.startswith(f"{candidate}."))
        ),
        None,
    )
    if driver or not module_name:
        driver = driver or ("psycopg2" if engine == "postgresql" else "pymysql")
        separator = "\n" if multiline else ". "
        return (
            f"Missing database driver: {driver}{separator}"
            f"Install with: pip install {driver}"
        )

    if module_name == "paramiko" or module_name.startswith("paramiko."):
        return (
            "SSH support is unavailable in this build because the paramiko "
            "module is missing."
        )

    return f"Missing required module: {module_name}."

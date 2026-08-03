"""Cross-platform helpers for serialized atomic persistence."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import toml
from filelock import FileLock


def merge_mapping_changes(
    latest: dict[str, Any],
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Apply changes from ``baseline`` to ``current`` onto ``latest``."""
    merged = copy.deepcopy(latest)
    for key in baseline.keys() - current.keys():
        merged.pop(key, None)
    for key, value in current.items():
        if key not in baseline:
            merged[key] = copy.deepcopy(value)
            continue
        previous = baseline[key]
        if value == previous:
            continue
        latest_value = merged.get(key)
        if all(isinstance(item, dict) for item in (previous, value, latest_value)):
            merged[key] = merge_mapping_changes(latest_value, previous, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _replace_with_retry(source: str, destination: Path) -> None:
    for attempt in range(4):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.05 * (attempt + 1))


def _atomic_write(path: Path, write) -> None:
    fd, temp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file_obj:
            write(file_obj)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        _replace_with_retry(temp_path, path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _write_toml(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(path, lambda file_obj: toml.dump(data, file_obj))


def write_text(path: Path, text: str) -> None:
    """Serialize and atomically replace a UTF-8 text file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(f"{path}.lock", timeout=10):
        _atomic_write(path, lambda file_obj: file_obj.write(text))


def write_json(path: Path, data: Any) -> None:
    """Serialize and atomically replace a UTF-8 JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(f"{path}.lock", timeout=10):
        _atomic_write(
            path,
            lambda file_obj: json.dump(data, file_obj, indent=2, default=str),
        )


def update_json(
    path: Path,
    update: Callable[[Any], Any],
    *,
    create: bool = True,
) -> Any:
    """Update a JSON document under the same lock used for replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(f"{path}.lock", timeout=10):
        if path.exists():
            with open(path, "r", encoding="utf-8") as file_obj:
                latest = json.load(file_obj)
        elif create:
            latest = {}
        else:
            return None
        current = update(copy.deepcopy(latest))
        _atomic_write(
            path,
            lambda file_obj: json.dump(current, file_obj, indent=2, default=str),
        )
        return current


def delete_file(path: Path) -> None:
    """Delete a persistent file under its writer lock."""
    with FileLock(f"{path}.lock", timeout=10):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def update_toml(
    path: Path,
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    validate: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Merge and atomically persist one TOML transaction under a file lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(f"{path}.lock", timeout=10)
    with lock:
        if path.exists():
            try:
                latest = toml.load(path)
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to load {path} while saving: {exc}"
                ) from exc
        else:
            latest = copy.deepcopy(baseline)
        merged = merge_mapping_changes(latest, baseline, current)
        if validate is not None:
            validate(merged)
        _write_toml(path, merged)
        return merged

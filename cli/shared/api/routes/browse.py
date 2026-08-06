"""Browse API endpoint for server-side directory navigation.

Provides filesystem directory listing for the directory/file picker UI,
since browser-native folder pickers return sandboxed paths.
"""

from __future__ import annotations

import os
import string
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.api.guards import require_local_request

router = APIRouter()

SKIP_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".tox",
    "eggs",
}


class DirectoryEntry(BaseModel):
    name: str
    path: str


class BrowseResponse(BaseModel):
    current: str
    parent: Optional[str]
    directories: list[DirectoryEntry]
    files: list[DirectoryEntry] = []


def _windows_drive_entries() -> list[DirectoryEntry]:
    return [
        DirectoryEntry(name=f"{letter}:", path=f"{letter}:\\")
        for letter in string.ascii_uppercase
        if os.path.exists(f"{letter}:\\")
    ]


@router.get("/browse")
async def browse_directory(
    request: Request, path: Optional[str] = None, ext: Optional[str] = None
) -> BrowseResponse:
    """List subdirectories (and optionally files) of a path for the picker UI.

    Args:
        path: Directory to list. Defaults to home directory if omitted.
        ext: When given, also list files with this extension (e.g. "csv").

    Returns:
        Current path, parent path, sorted subdirectories, and matching files.
    """
    require_local_request(request)

    if path:
        resolved = os.path.abspath(os.path.expanduser(path))
    else:
        resolved = os.path.expanduser("~")

    if not os.path.exists(resolved) or not os.path.isdir(resolved):
        raise HTTPException(
            status_code=400,
            detail=f"Path does not exist or is not a directory: {resolved}",
        )

    parent = os.path.dirname(resolved)
    if parent == resolved:
        parent = None

    suffix = f".{ext.lstrip('.').lower()}" if ext else None

    directories: list[DirectoryEntry] = []
    files: list[DirectoryEntry] = []
    try:
        entries = os.listdir(resolved)
    except PermissionError:
        entries = []

    for entry in entries:
        if entry.startswith("."):
            continue
        if entry in SKIP_DIRS:
            continue
        full_path = os.path.join(resolved, entry)
        try:
            if os.path.isdir(full_path):
                directories.append(DirectoryEntry(name=entry, path=full_path))
            elif suffix and entry.lower().endswith(suffix):
                files.append(DirectoryEntry(name=entry, path=full_path))
        except PermissionError:
            continue

    if os.name == "nt" and parent is None:
        known_paths = {entry.path.casefold() for entry in directories}
        directories.extend(
            drive
            for drive in _windows_drive_entries()
            if drive.path.casefold() not in known_paths
        )

    directories.sort(key=lambda d: d.name.lower())
    files.sort(key=lambda f: f.name.lower())

    return BrowseResponse(
        current=resolved,
        parent=parent,
        directories=directories,
        files=files,
    )

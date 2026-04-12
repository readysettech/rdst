"""Browse API endpoint for server-side directory navigation.

Provides filesystem directory listing for the directory picker UI,
since browser-native folder pickers return sandboxed paths.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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


@router.get("/browse")
async def browse_directory(path: Optional[str] = None) -> BrowseResponse:
    """List subdirectories of a given path for the directory picker.

    Args:
        path: Directory to list. Defaults to home directory if omitted.

    Returns:
        Current path, parent path, and sorted list of subdirectories.
    """
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

    directories: list[DirectoryEntry] = []
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
        except PermissionError:
            continue

    directories.sort(key=lambda d: d.name.lower())

    return BrowseResponse(
        current=resolved,
        parent=parent,
        directories=directories,
    )

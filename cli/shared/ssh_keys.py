"""Discover SSH authentication options available on this machine."""

from __future__ import annotations

import fnmatch
import os
import platform
import shlex
import shutil
import stat
import threading
from pathlib import Path
from typing import Any, Callable, Optional


def _read_ssh_config(ssh_dir: Path) -> list[dict[str, Any]]:
    """Return simple Host blocks needed by SSH auth pickers."""
    config_path = ssh_dir / "config"
    if not config_path.is_file():
        return []

    blocks: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []

    for raw_line in lines:
        try:
            parts = shlex.split(raw_line, comments=True)
        except ValueError:
            continue
        if len(parts) < 2:
            continue
        keyword = parts[0].lower()
        if keyword == "host":
            current = {"patterns": parts[1:], "identity_files": []}
            blocks.append(current)
        elif current is not None and keyword == "identityfile":
            current["identity_files"].extend(parts[1:])
        elif current is not None and keyword in {"hostname", "port", "user"}:
            current[keyword] = parts[1]
    return blocks


def _is_readable_private_key(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("rb") as key_file:
            prefix = key_file.read(4096)
    except OSError:
        return False
    return b"PRIVATE KEY" in prefix


def _has_private_key_banner(prefix: bytes) -> bool:
    first_line = prefix.splitlines()[0][:256] if prefix else b""
    return first_line.startswith(b"-----BEGIN ") and b"PRIVATE KEY-----" in first_line


def _download_key_paths(downloads_dir: Path) -> list[Path]:
    try:
        entries = list(downloads_dir.iterdir())
    except OSError:
        return []
    candidates = []
    for path in entries:
        if not path.is_file():
            continue
        try:
            with path.open("rb") as key_file:
                prefix = key_file.read(4096)
        except OSError:
            continue
        if b"PRIVATE KEY" in prefix and (
            path.suffix.lower() == ".pem" or _has_private_key_banner(prefix)
        ):
            candidates.append(path)
    return sorted(candidates)


def _windows_download_key_paths(timeout: float = 0.25) -> list[Path]:
    """Best-effort WSL lookup without delaying key discovery on a slow mount."""
    if "microsoft" not in platform.release().lower():
        return []

    result: list[Path] = []

    def find_accessible() -> None:
        try:
            users_dir = Path("/mnt/c/Users")
            for user_dir in users_dir.iterdir():
                candidate = user_dir / "Downloads"
                if candidate.is_dir() and os.access(candidate, os.R_OK):
                    result.extend(_download_key_paths(candidate))
                    return
        except (OSError, PermissionError):
            return

    worker = threading.Thread(target=find_accessible, daemon=True)
    worker.start()
    worker.join(timeout)
    return list(result)


def import_ssh_key(source_path: str, ssh_dir: Optional[Path] = None) -> Path:
    """Copy a private key into ~/.ssh with restrictive permissions."""
    source = Path(os.path.expandvars(os.path.expanduser(source_path)))
    if not source.is_file() or not _is_readable_private_key(source):
        raise ValueError(f"Not a readable SSH private key: {source}")

    ssh_dir = ssh_dir or Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = ssh_dir / source.name
    if destination.exists():
        index = 1
        while True:
            candidate = ssh_dir / f"{source.stem}-{index}{source.suffix}"
            if not candidate.exists():
                destination = candidate
                break
            index += 1

    shutil.copyfile(source, destination)
    destination.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return destination


def discover_ssh_auth_options(
    jump_host: str,
    ssh_dir: Optional[Path] = None,
    agent_factory: Optional[Callable[[], Any]] = None,
) -> list[dict[str, Any]]:
    """Discover agent keys, private key files, and matching SSH config entries."""
    ssh_dir = ssh_dir or Path.home() / ".ssh"
    options: list[dict[str, Any]] = []

    config_blocks = _read_ssh_config(ssh_dir)
    for block in config_blocks:
        for alias in block["patterns"]:
            if not alias or alias.startswith("!") or any(char in alias for char in "*?"):
                continue
            hostname = block.get("hostname") or alias
            label = alias if hostname == alias else f"{alias} ({hostname})"
            option: dict[str, Any] = {
                "kind": "host",
                "label": label,
                "host": alias,
                "hostname": hostname,
            }
            if block.get("port"):
                try:
                    option["port"] = int(block["port"])
                except (TypeError, ValueError):
                    pass
            if block.get("user"):
                option["user"] = block["user"]
            options.append(option)

    if agent_factory is None:
        try:
            import paramiko

            agent_factory = paramiko.Agent
        except ImportError:
            agent_factory = None

    agent = None
    if agent_factory is not None:
        try:
            agent = agent_factory()
            for key in agent.get_keys():
                key_type = getattr(key, "get_name", lambda: "key")()
                raw_fingerprint = getattr(key, "get_fingerprint", lambda: b"")()
                fingerprint = raw_fingerprint.hex()[:16] or "unknown"
                comment = getattr(key, "comment", "") or ""
                detail = f" {comment}" if comment else ""
                options.append(
                    {
                        "kind": "agent",
                        "label": f"SSH agent: {key_type} {fingerprint}{detail}",
                    }
                )
        except Exception:
            pass
        finally:
            if agent is not None:
                try:
                    agent.close()
                except Exception:
                    pass

    candidate_paths: list[Path] = []
    prevalidated_paths: set[Path] = set()
    path_locations: dict[Path, str] = {}
    if ssh_dir.is_dir():
        candidate_paths.extend([ssh_dir / "id_ed25519", ssh_dir / "id_rsa"])
        candidate_paths.extend(sorted(ssh_dir.glob("*.pem")))

    downloads_dirs = [(Path.home() / "Downloads", "Downloads")]
    for downloads_dir, location in downloads_dirs:
        download_paths = _download_key_paths(downloads_dir)
        prevalidated_paths.update(download_paths)
        for path in download_paths:
            candidate_paths.append(path)
            path_locations[path] = location
    windows_download_paths = _windows_download_key_paths()
    prevalidated_paths.update(windows_download_paths)
    for path in windows_download_paths:
        candidate_paths.append(path)
        path_locations[path] = "Windows Downloads"

    matching_blocks = []
    for block in config_blocks:
        patterns = block["patterns"]
        excluded = any(
            fnmatch.fnmatchcase(jump_host, pattern[1:])
            for pattern in patterns
            if pattern.startswith("!")
        )
        matched = any(
            fnmatch.fnmatchcase(jump_host, pattern)
            for pattern in patterns
            if pattern and not pattern.startswith("!")
        )
        if matched and not excluded:
            matching_blocks.append(block)
            for identity_file in block["identity_files"]:
                candidate_paths.append(
                    Path(os.path.expandvars(os.path.expanduser(identity_file)))
                )

    seen_paths: set[Path] = set()
    try:
        resolved_ssh_dir = ssh_dir.resolve()
    except OSError:
        resolved_ssh_dir = ssh_dir
    for path in candidate_paths:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen_paths or (
            path not in prevalidated_paths and not _is_readable_private_key(path)
        ):
            continue
        seen_paths.add(resolved)
        outside_ssh_dir = not resolved.is_relative_to(resolved_ssh_dir)
        location = path_locations.get(path)
        options.append(
            {
                "kind": "file",
                "label": (
                    f"{location}: {path.name}"
                    if location
                    else (
                        f"{path.parent}: {path.name}"
                        if outside_ssh_dir
                        else f"~/.ssh: {path.name}"
                    )
                ),
                "key_path": str(path),
                "outside_ssh_dir": outside_ssh_dir,
            }
        )

    for block in matching_blocks:
        patterns = ", ".join(block["patterns"])
        options.append(
            {
                "kind": "config",
                "label": f"SSH config Host {patterns}",
            }
        )

    return options


__all__ = ["discover_ssh_auth_options", "import_ssh_key"]

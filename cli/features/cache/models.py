"""Cache feature models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CacheInput:
    """Input for cache service operations."""

    target: str
    query: Optional[str] = None
    cache_id: Optional[str] = None
    tag: Optional[str] = None


@dataclass
class CacheOptions:
    """Options for cache service execution."""

    dry_run: bool = False
    mode: str = "docker"
    port: Optional[int] = None
    deploy_config: str = "readyset"
    json_output: bool = False
    yes: bool = False
    namespace: Optional[str] = None
    kubeconfig: Optional[str] = None
    host: Optional[str] = None
    ssh_key: Optional[str] = None
    ssh_user: Optional[str] = None
    no_request_path: bool = False  # opt-out: deploy with QUERY_CACHING=explicit
    memory_bytes: Optional[int] = None  # READYSET_MEMORY_LIMIT + Docker --memory (None = 4 GiB default)
    cpus: Optional[str] = None  # Docker --cpus value (None = "2" default)

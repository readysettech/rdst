"""Shared fixtures for unit tests.

Unit tests must never touch the developer's real OS keychain: a test that
persists a secret through `SecretStoreService` (or raw `keyring`) would
write into the actual keychain and leak past the test run. Every test in
this suite gets a process-local in-memory keyring instead.
"""

from __future__ import annotations

import keyring
import pytest
from keyring.backend import KeyringBackend

from shared.secret_store_service import SecretStoreService


class _InMemoryKeyring(KeyringBackend):
    """Process-local keyring backend backed by a plain dict."""

    priority = 100  # type: ignore[assignment]

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._store.pop((service, username), None)


@pytest.fixture(autouse=True)
def _isolated_keyring():
    """Route all keyring access to an in-memory backend for every test.

    Also resets `SecretStoreService._probe_cache`, which caches backend
    availability per service name across instances and would otherwise
    carry state between tests (or from process startup).
    """
    backend = _InMemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)

    saved_cache = dict(SecretStoreService._probe_cache)
    SecretStoreService._probe_cache.clear()

    try:
        yield backend
    finally:
        keyring.set_keyring(previous)
        SecretStoreService._probe_cache.clear()
        SecretStoreService._probe_cache.update(saved_cache)

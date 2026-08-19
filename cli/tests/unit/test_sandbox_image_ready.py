"""The sandbox must have its image local before any readiness clock runs.

A first-run pull is gigabyte-scale; racing it against the container
readiness timeout produced spurious "container is not running"
failures for first-time users.
"""

import asyncio

from shared.deploy.sandbox_manager import LocalDockerSandboxAdapter


def test_pulls_and_announces_when_image_missing(monkeypatch):
    import shared.deploy.local_docker as local_docker

    pulled = []
    monkeypatch.setattr(local_docker, "image_present", lambda image: False)
    monkeypatch.setattr(
        local_docker, "pull_image",
        lambda image: pulled.append(image) or {"success": True},
    )
    announced = []

    async def on_download():
        announced.append(True)

    asyncio.run(LocalDockerSandboxAdapter().ensure_image_ready(on_download))
    assert pulled and announced


def test_skips_pull_when_image_present(monkeypatch):
    import shared.deploy.local_docker as local_docker

    monkeypatch.setattr(local_docker, "image_present", lambda image: True)

    def _fail_pull(image):
        raise AssertionError("pull_image must not run when the image is local")

    monkeypatch.setattr(local_docker, "pull_image", _fail_pull)
    announced = []

    async def on_download():
        announced.append(True)

    asyncio.run(LocalDockerSandboxAdapter().ensure_image_ready(on_download))
    assert not announced


def test_raises_a_clear_error_when_pull_fails(monkeypatch):
    import shared.deploy.local_docker as local_docker

    monkeypatch.setattr(local_docker, "image_present", lambda image: False)
    monkeypatch.setattr(
        local_docker, "pull_image",
        lambda image: {"success": False, "error": "network unreachable"},
    )

    try:
        asyncio.run(LocalDockerSandboxAdapter().ensure_image_ready())
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "network unreachable" in str(exc)

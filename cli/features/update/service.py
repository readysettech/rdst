from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import NoReturn
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version

from .models import InstallerState, UpdateOutcome, VersionCheck

PYPI_JSON_URL = "https://pypi.org/pypi/rdst/json"
DEFAULT_INSTALLER_BASE_URL = "https://downloads.readyset.io/packages/rdst-cli"
_ENTRYPOINTS = frozenset(("rdst", "rdst-mcp"))
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*$")
# The transports install.sh accepts from an override, kept in step with it.
_LOCAL_INSTALLER_URL = re.compile(
    r"^(file:///|http://(127\.0\.0\.1|localhost)([:/]|$))"
)


class UpdateError(RuntimeError):
    pass


class UpdateService:
    def __init__(
        self,
        config_dir: Path | None = None,
        runtime_prefix: Path | None = None,
    ):
        self.config_dir = config_dir or Path.home() / ".rdst"
        if runtime_prefix is not None:
            self.runtime_prefix = runtime_prefix
        elif getattr(sys, "frozen", False):
            # A frozen build lives at <generation>/rdst/rdst, so the environment
            # is the directory holding the executable. sys.prefix points inside
            # the bundle instead.
            self.runtime_prefix = Path(sys.executable).resolve().parent
        else:
            self.runtime_prefix = Path(sys.prefix)

    def current_version(self):
        try:
            return package_version("rdst")
        except PackageNotFoundError:
            try:
                from _version import __version__

                return __version__
            except ImportError:
                return "unknown"

    def check(self, timeout: float = 5.0):
        return VersionCheck(
            current=self.current_version(), latest=self._latest_version(timeout)
        )

    def _latest_version(self, timeout: float = 5.0):
        request = Request(
            PYPI_JSON_URL,
            headers={"Accept": "application/json", "User-Agent": "rdst-update-check"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
        except Exception as error:
            raise UpdateError(f"could not check for updates: {error}") from error

        latest = str(payload.get("info", {}).get("version", "")).strip()
        try:
            Version(latest)
        except InvalidVersion as error:
            raise UpdateError(
                "the package index returned no valid RDST version"
            ) from error
        return latest

    def load_installer_state(self):
        state_path = self.config_dir / "install-state"
        try:
            lines = state_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return None

        values = {}
        for line in lines:
            key, separator, value = line.partition("=")
            if separator:
                if key in values:
                    raise UpdateError(
                        f"installer state contains duplicate {key} entries"
                    )
                values[key] = value

        if values.get("method") != "readyset-archive":
            return None

        required = ("format", "data_dir", "bin_dir", "platform")
        missing = [key for key in required if not values.get(key)]
        if missing:
            raise UpdateError(
                f"installer state is missing: {', '.join(sorted(missing))}"
            )

        if values["format"] != "1":
            raise UpdateError(f"unsupported installer state format: {values['format']}")

        state = InstallerState(
            data_dir=Path(values["data_dir"]),
            bin_dir=Path(values["bin_dir"]),
            platform=values["platform"],
        )
        if not all(path.is_absolute() for path in (state.data_dir, state.bin_dir)):
            raise UpdateError("installer state contains a relative path")
        if state.data_dir.name != "rdst":
            raise UpdateError("the installer data directory must end in /rdst")
        if state.data_dir.is_symlink():
            raise UpdateError("installer data directory must not be a symbolic link")
        if not re.fullmatch(r"[a-z0-9]+-[a-z0-9_]+", state.platform):
            raise UpdateError("installer state contains an invalid platform")
        try:
            self.config_dir.relative_to(state.data_dir)
        except ValueError:
            pass
        else:
            raise UpdateError("installer data directory contains the config directory")
        return state

    def update(self, requested_version: str | None = None):
        state = self.load_installer_state()
        if state is None:
            raise UpdateError(
                "this RDST installation is not managed by the Readyset installer. "
                f"Update it with: {self.external_update_command()}"
            )
        if not (state.data_dir / ".rdst-managed").is_file():
            raise UpdateError(
                "the installer data directory is unmarked. Re-run the RDST installer to repair it"
            )
        if state.data_dir.is_symlink():
            raise UpdateError("installer data directory must not be a symbolic link")
        state.data_dir.mkdir(parents=True, exist_ok=True)

        self._validate_entrypoints(state)
        self._validate_runtime_prefix(self._active_generation(state))

        target_version = self._target_version(requested_version)
        current_version = self.current_version()
        if self._is_noop(current_version, target_version, requested_version):
            return UpdateOutcome(version=current_version, changed=False)

        self._exec_installer(target_version)

    def _installer_base_url(self):
        """Where to fetch the installer from, on the terms install.sh accepts.

        The override exists so CI can upgrade from a build's own artifacts
        before they are published. It relaxes the https requirement only for
        loopback and local files, so an environment variable cannot redirect
        an upgrade to an arbitrary host.
        """
        override = os.environ.get("RDST_INSTALLER_BASE_URL")
        base_url = (override or DEFAULT_INSTALLER_BASE_URL).rstrip("/")
        if base_url.startswith("https://"):
            return base_url
        if override is not None and _LOCAL_INSTALLER_URL.match(base_url):
            return base_url
        raise UpdateError("the RDST download location must use https")

    def _exec_installer(self, target_version) -> NoReturn:
        """Hand the upgrade to the published installer, replacing this process.

        The installer already performs every step an upgrade needs: verifying
        the archive, staging a generation, activating it atomically, and
        retiring the one it replaces. Replacing this process rather than
        spawning one matters for that last step, because the generation being
        retired is the one running this code, and a frozen build resolves its
        modules from disk on demand.
        """
        base_url = self._installer_base_url()
        installer_url = f"{base_url}/versions/{target_version}/install.sh"
        work = Path(tempfile.mkdtemp(prefix="rdst-update-"))
        installer = work / "install.sh"
        self._download(installer_url, installer)
        self._verify_checksum(installer, f"{installer_url}.sha256")

        # The shell removes the download directory once the installer exits,
        # since nothing after execv runs in this process.
        runner = (
            'work=$1; shift; sh "$work/install.sh" "$@"; status=$?; '
            'rm -rf "$work"; exit $status'
        )
        try:
            os.execv(
                "/bin/sh",
                [
                    "sh",
                    "-c",
                    runner,
                    "rdst-update",
                    str(work),
                    "--version",
                    target_version,
                    "--force",
                    # An upgrade replaces the runtime; it has no business
                    # rewriting the shell profile.
                    "--no-modify-path",
                ],
            )
        except OSError as error:
            shutil.rmtree(work, ignore_errors=True)
            raise UpdateError(f"could not start the RDST installer: {error}") from error
        # execv replaces this process, so reaching here means it neither
        # replaced nor raised. Fail rather than return None to a caller that
        # expects an outcome.
        raise UpdateError("the RDST installer did not start")

    def _download(self, url, destination):
        request = Request(url, headers={"User-Agent": "rdst-update"})
        try:
            with urlopen(request, timeout=30) as response:
                destination.write_bytes(response.read())
        except Exception as error:
            raise UpdateError(f"could not download {url}: {error}") from error

    def _verify_checksum(self, artifact, checksum_url):
        request = Request(checksum_url, headers={"User-Agent": "rdst-update"})
        try:
            with urlopen(request, timeout=30) as response:
                published = response.read().decode("utf-8").split()
        except Exception as error:
            raise UpdateError(
                f"could not download {checksum_url}: {error}"
            ) from error
        expected = published[0] if published else ""
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise UpdateError("the published installer checksum is malformed")
        actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if actual != expected:
            raise UpdateError("installer checksum verification failed")

    def _target_version(self, requested_version):
        if requested_version is None:
            return self._latest_version()
        if not _VERSION_PATTERN.fullmatch(requested_version):
            raise UpdateError(f"invalid version: {requested_version}")
        try:
            Version(requested_version)
        except InvalidVersion as error:
            raise UpdateError(f"invalid version: {requested_version}") from error
        return requested_version

    def _is_noop(self, current, target, requested):
        try:
            current_version = Version(current)
            target_version = Version(target)
        except InvalidVersion:
            return False
        if requested is not None:
            return current_version == target_version
        return current_version >= target_version

    def _validate_runtime_prefix(self, active_generation):
        if active_generation is None:
            raise UpdateError(
                "the active RDST environment is missing. Re-run the RDST installer to repair it"
            )
        try:
            expected_prefix = (
                active_generation
                if active_generation.name == "rdst"
                else active_generation / "rdst"
            )
            expected = expected_prefix.resolve(strict=True)
            actual = self.runtime_prefix.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise UpdateError(
                "could not verify the active RDST environment. "
                "Re-run the RDST installer to repair it"
            ) from error
        if actual != expected:
            raise UpdateError(
                "this command is not running from the active Readyset installer environment. "
                "Run the installer-managed rdst executable or re-run the RDST installer"
            )

    def _active_generation(self, state):
        active = state.tool_dir / "current"
        if active.is_symlink():
            target = active.readlink()
            if not target.is_absolute():
                target = active.parent / target
            generation_name = target.name
            lexical_target = state.tool_dir / generation_name
            if target != lexical_target or not generation_name.startswith(
                ".rdst-generation-"
            ):
                raise UpdateError(
                    "the active RDST environment is no longer owned by the installer"
                )
            if not target.exists():
                return None
            try:
                resolved_target = target.resolve(strict=True)
                tool_dir = state.tool_dir.resolve(strict=True)
                owned = (
                    resolved_target.parent == tool_dir
                    and lexical_target.resolve(strict=True) == resolved_target
                    and not lexical_target.is_symlink()
                )
            except (OSError, RuntimeError):
                owned = False
            if not owned:
                raise UpdateError(
                    "the active RDST environment is no longer owned by the installer"
                )
            return lexical_target
        if active.exists():
            raise UpdateError(
                "the active RDST environment is no longer owned by the installer"
            )
        legacy = state.tool_dir / "rdst"
        return legacy.resolve() if legacy.is_dir() else None

    def _validate_entrypoints(self, state):
        tool_dir = state.tool_dir.resolve()
        for name in ("rdst", "rdst-mcp"):
            entrypoint = state.bin_dir / name
            if not entrypoint.exists() and not entrypoint.is_symlink():
                continue
            if not entrypoint.is_symlink():
                raise UpdateError(
                    f"{entrypoint} is no longer owned by the RDST installer"
                )
            target = entrypoint.readlink()
            if not target.is_absolute():
                target = entrypoint.parent / target
            try:
                owned = target.resolve().is_relative_to(tool_dir)
            except (OSError, RuntimeError):
                owned = False
            if not owned:
                raise UpdateError(
                    f"{entrypoint} is no longer owned by the RDST installer"
                )

    def external_update_command(self):
        location = f"{sys.executable} {sys.prefix}".lower()
        if "pipx" in location:
            return "pipx upgrade rdst"
        if "/uv/tools/" in location or "/share/uv/tools/" in location:
            return "uv tool upgrade rdst"
        return f"{sys.executable} -m pip install --upgrade rdst"

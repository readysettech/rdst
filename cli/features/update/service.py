from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version

from .models import InstallerState, UpdateOutcome, VersionCheck

DEFAULT_INDEX = "https://pypi.org/simple"
PYPI_JSON_URL = "https://pypi.org/pypi/rdst/json"
_ENTRYPOINTS = frozenset(("rdst", "rdst-mcp"))
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*$")


class UpdateError(RuntimeError):
    pass


class UpdateService:
    def __init__(
        self,
        config_dir: Path | None = None,
        runtime_prefix: Path | None = None,
    ):
        self.config_dir = config_dir or Path.home() / ".rdst"
        self.runtime_prefix = runtime_prefix or Path(sys.prefix)

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

        if values.get("method") != "readyset-uv":
            return None

        required = (
            "format",
            "data_dir",
            "bin_dir",
            "cache_dir",
            "python",
            "uv_version",
        )
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
            cache_dir=Path(values["cache_dir"]),
            python=values["python"],
        )
        if not all(
            path.is_absolute()
            for path in (state.data_dir, state.bin_dir, state.cache_dir)
        ):
            raise UpdateError("installer state contains a relative path")
        if state.data_dir.name != "rdst" or state.cache_dir.name != "rdst":
            raise UpdateError("installer data and cache directories must end in /rdst")
        if state.data_dir.is_symlink():
            raise UpdateError("installer data directory must not be a symbolic link")
        if not re.fullmatch(r"[0-9]+\.[0-9]+", state.python):
            raise UpdateError("installer state contains an invalid Python version")
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

        with self._operation_lock(state):
            return self._update_locked(state, requested_version)

    def _update_locked(self, state, requested_version):
        if not state.uv.is_file() or not os.access(state.uv, os.X_OK):
            raise UpdateError(
                "the installer runtime is missing. Re-run the RDST installer to repair it"
            )
        if state.tool_dir.is_symlink():
            raise UpdateError(
                "the installer tool directory must not be a symbolic link"
            )
        self._validate_entrypoints(state)
        active_generation = self._active_generation(state)
        self._validate_runtime_prefix(active_generation)

        target_version = self._target_version(requested_version)
        current_version = self.current_version()
        if self._is_noop(current_version, target_version, requested_version):
            return UpdateOutcome(version=current_version, changed=False)

        common = [
            "--python",
            state.python,
            "--managed-python",
            "--no-config",
            "--no-build",
            "--default-index",
            DEFAULT_INDEX,
        ]
        command = [
            str(state.uv),
            "tool",
            "install",
            *common,
            "--force",
            f"rdst=={target_version}",
        ]

        env = {
            key: value for key, value in os.environ.items() if not key.startswith("UV_")
        }
        env.update(
            {
                "UV_PYTHON_INSTALL_DIR": str(state.python_dir),
                "UV_CACHE_DIR": str(state.cache_dir / "uv"),
            }
        )

        state.cache_dir.mkdir(parents=True, exist_ok=True)
        state.tool_dir.mkdir(parents=True, exist_ok=True)
        generation = Path(
            tempfile.mkdtemp(prefix=".rdst-generation-", dir=state.tool_dir)
        )
        activation_started = False
        try:
            with tempfile.TemporaryDirectory(
                prefix="update-bin-", dir=state.cache_dir
            ) as candidate_bin:
                candidate_env = env | {
                    "UV_TOOL_DIR": str(generation),
                    "UV_TOOL_BIN_DIR": candidate_bin,
                }
                self._run_uv(
                    command,
                    candidate_env,
                    "update preparation failed; the existing installation was not changed. "
                    "Re-run the RDST installer to refresh its runtime",
                )
                self._validate_candidate(Path(candidate_bin), candidate_env)
            generation_bin = generation / "rdst" / "bin"
            self._validate_tool_environment(generation_bin)
            activation_started = True
            self._activate_generation(state, generation)
            self._point_entrypoints(state, state.tool_dir / "current" / "rdst" / "bin")
        except BaseException:
            if not activation_started:
                shutil.rmtree(generation, ignore_errors=True)
            raise

        return UpdateOutcome(version=target_version, changed=True)

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

    @contextmanager
    def _operation_lock(self, state):
        lock_dir = self.config_dir / ".rdst-operation-lock"
        token = f"{os.getpid()}-{secrets.token_hex(16)}"
        try:
            lock_dir.mkdir(mode=0o700)
        except FileExistsError as error:
            try:
                owner = (lock_dir / "owner").read_text(encoding="utf-8").strip()
                owner_pid = owner.partition("-")[0]
            except OSError:
                owner_pid = "unknown"
            raise UpdateError(
                f"another RDST install, update, or uninstall operation holds the lock "
                f"(PID {owner_pid}). If no operation is running, remove {lock_dir} and retry"
            ) from error
        owner_path = lock_dir / "owner"
        try:
            owner_path.write_text(f"{token}\n", encoding="utf-8")
        except OSError as error:
            shutil.rmtree(lock_dir, ignore_errors=True)
            raise UpdateError(
                f"could not initialize the RDST operation lock: {error}"
            ) from error
        try:
            yield
        finally:
            try:
                owned = owner_path.read_text(encoding="utf-8").strip() == token
            except OSError:
                owned = False
            if owned:
                shutil.rmtree(lock_dir, ignore_errors=True)

    def _validate_candidate(self, bin_dir, env):
        names = (
            {entry.name for entry in bin_dir.iterdir()} if bin_dir.is_dir() else set()
        )
        if names != _ENTRYPOINTS:
            raise UpdateError(
                "the prepared RDST package exposed unexpected executables; "
                "the existing installation was not changed"
            )
        try:
            validation = subprocess.run(
                [str(bin_dir / "rdst"), "--version"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise UpdateError(
                "the prepared RDST executable could not start; "
                "the existing installation was not changed"
            ) from error
        if validation.returncode != 0:
            raise UpdateError(
                "the prepared RDST executable did not start; "
                "the existing installation was not changed"
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

    def _activate_generation(self, state, generation):
        if (
            generation.parent.resolve() != state.tool_dir.resolve()
            or not generation.name.startswith(".rdst-generation-")
            or generation.is_symlink()
        ):
            raise UpdateError("refusing to activate an unmanaged RDST environment")
        active = state.tool_dir / "current"
        temporary = state.tool_dir / f".rdst-current-{os.getpid()}"
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(generation)
        os.replace(temporary, active)

    def _validate_tool_environment(self, entrypoint_dir):
        for name in _ENTRYPOINTS:
            target = entrypoint_dir / name
            if not target.is_file() or not os.access(target, os.X_OK):
                raise UpdateError(
                    f"the prepared RDST environment is missing {name}; "
                    "the existing installation was not changed"
                )

    def _point_entrypoints(self, state, entrypoint_dir):
        self._validate_tool_environment(entrypoint_dir)
        state.bin_dir.mkdir(parents=True, exist_ok=True)
        for name in sorted(_ENTRYPOINTS):
            target = entrypoint_dir / name
            temporary = state.bin_dir / f".{name}.rdst-update-{os.getpid()}"
            temporary.unlink(missing_ok=True)
            temporary.symlink_to(target)
            os.replace(temporary, state.bin_dir / name)
            published = state.bin_dir / name
            if published.resolve(strict=True) != target.resolve(strict=True):
                raise UpdateError(f"could not publish the {name} executable")

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

    def _run_uv(self, command, env, failure_message):
        try:
            result = subprocess.run(command, env=env, check=False)
        except OSError as error:
            raise UpdateError(
                f"could not start the installer runtime: {error}"
            ) from error
        if result.returncode != 0:
            raise UpdateError(f"{failure_message} (uv exit code {result.returncode})")

    def external_update_command(self):
        location = f"{sys.executable} {sys.prefix}".lower()
        if "pipx" in location:
            return "pipx upgrade rdst"
        if "/uv/tools/" in location or "/share/uv/tools/" in location:
            return "uv tool upgrade rdst"
        return f"{sys.executable} -m pip install --upgrade rdst"

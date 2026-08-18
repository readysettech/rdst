"""Release bootstrap contracts for the desktop sidecar Python runtime."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


RDST_DIR = Path(__file__).parents[2]
DESKTOP_BUILD_LIB = RDST_DIR / ".buildkite" / "desktop_build_lib.sh"
WINDOWS_BUILD = RDST_DIR / ".buildkite" / "build_rdst_desktop_windows.ps1"
LINUX_SIDECAR_BUILD = RDST_DIR / ".buildkite" / "build_sidecar_linux.sh"


def _executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def test_macos_bootstrap_reinstalls_probes_and_exports_exact_managed_python(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "uv-calls"
    managed_python = tmp_path / "managed" / "bin" / "python3.12"
    managed_python.parent.mkdir(parents=True)
    _executable(
        managed_python,
        '#!/bin/bash\nprintf "%s\\t3.51.3\\n" "$0"\n',
    )
    _executable(
        fake_bin / "uv",
        """#!/bin/bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_UV_CALLS"
if [[ "$1 $2" == "python find" ]]; then
  printf '%s\n' "$FAKE_MANAGED_PYTHON"
fi
""",
    )
    command = f"""
set -euo pipefail
source {DESKTOP_BUILD_LIB!s}
configure_safe_uv_python_312
printf 'selected=%s\\n' "$RDST_SAFE_PYTHON"
printf 'path_head=%s\\n' "${{PATH%%:*}}"
"""
    result = subprocess.run(
        ["bash", "-c", command],
        env=os.environ
        | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_UV_CALLS": str(calls),
            "FAKE_MANAGED_PYTHON": str(managed_python),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "python install --managed-python --reinstall 3.12",
        "python find --managed-python --no-project 3.12",
    ]
    assert f"selected={managed_python}" in result.stdout
    assert f"path_head={managed_python.parent}" in result.stdout


def test_macos_bootstrap_fails_closed_when_sqlite_probe_fails(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    managed_python = tmp_path / "managed" / "python3.12"
    _executable(
        managed_python,
        '#!/bin/bash\necho "unsafe SQLite 3.45.1" >&2\nexit 42\n',
    )
    _executable(
        fake_bin / "uv",
        """#!/bin/bash
set -euo pipefail
if [[ "$1 $2" == "python find" ]]; then
  printf '%s\n' "$FAKE_MANAGED_PYTHON"
fi
""",
    )
    command = f"""
set -euo pipefail
source {DESKTOP_BUILD_LIB!s}
configure_safe_uv_python_312
"""
    result = subprocess.run(
        ["bash", "-c", command],
        env=os.environ
        | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_MANAGED_PYTHON": str(managed_python),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "failed the SQLite WAL safety probe" in result.stderr


def test_windows_bootstrap_is_pinned_self_healing_and_fail_closed():
    script = WINDOWS_BUILD.read_text(encoding="utf-8")

    assert "uv-x86_64-pc-windows-msvc.zip" in script
    assert "0a23463216d09c6a72ff80ef5dc5a795f07dc1575cb84d24596c2f124a441b7b" in script
    assert "'python', 'install', '--managed-python', '--reinstall', '3.12'" in script
    assert "python find --managed-python --no-project 3.12" in script
    assert "sqlite3.sqlite_version_info" in script
    assert "version >= (3, 51, 3)" in script
    assert "$env:RDST_SAFE_PYTHON = $safePython" in script
    assert '$env:Path = "$(Split-Path $safePython -Parent);$($env:Path)"' in script
    assert "Install-ChocolateyPackage 'python312'" not in script
    assert script.index("Install-SafeUvPython312 $uvPath") < script.index(
        "Invoke-Checked 'pnpm' @('--filter', 'rdst-desktop', 'prepackage')"
    )


def test_linux_sidecar_bootstrap_is_unchanged():
    script = LINUX_SIDECAR_BUILD.read_text(encoding="utf-8")

    assert "install_pinned_uv_linux" in script
    assert "uv python install 3.12" in script
    assert "configure_safe_uv_python_312" not in script

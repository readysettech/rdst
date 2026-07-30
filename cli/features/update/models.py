from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version


@dataclass(frozen=True)
class InstallerState:
    data_dir: Path
    bin_dir: Path
    platform: str

    @property
    def tool_dir(self):
        return self.data_dir / "tools"


@dataclass(frozen=True)
class UpdateOutcome:
    version: str
    changed: bool


@dataclass(frozen=True)
class VersionCheck:
    current: str
    latest: str

    @property
    def update_available(self):
        try:
            return Version(self.latest) > Version(self.current)
        except InvalidVersion:
            return self.current != self.latest

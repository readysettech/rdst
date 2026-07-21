from __future__ import annotations

from features.update.service import UpdateError, UpdateService
from shared.cli.types import RdstResult


class UpdateCommand:
    def __init__(self, service: UpdateService | None = None):
        self.service = service or UpdateService()

    def execute(self, *, check: bool = False, version: str | None = None):
        try:
            if check:
                result = self.service.check()
                if result.current == "unknown":
                    return RdstResult(
                        True,
                        f"The latest RDST release is {result.latest}; the installed "
                        "version could not be determined.",
                        data={
                            "current": result.current,
                            "latest": result.latest,
                            "update_available": None,
                        },
                    )
                if result.update_available:
                    return RdstResult(
                        True,
                        f"RDST {result.latest} is available; currently installed: "
                        f"{result.current}. Run 'rdst update' to install it.",
                        data={
                            "current": result.current,
                            "latest": result.latest,
                            "update_available": True,
                        },
                    )
                return RdstResult(
                    True,
                    f"RDST is up to date ({result.current}).",
                    data={
                        "current": result.current,
                        "latest": result.latest,
                        "update_available": False,
                    },
                )

            outcome = self.service.update(requested_version=version)
            if not outcome.changed:
                return RdstResult(
                    True,
                    f"RDST is already up to date ({outcome.version}).",
                    data={"version": outcome.version, "changed": False},
                )
            return RdstResult(
                True,
                f"RDST {outcome.version} was installed successfully.",
                data={"version": outcome.version, "changed": True},
            )
        except UpdateError as error:
            return RdstResult(False, str(error))

from __future__ import annotations

import shutil
import subprocess
import threading
from typing import Optional

from shared.deploy import READYSET_IMAGE

_pull_thread: Optional[threading.Thread] = None
_pull_status: dict = {"started": False, "completed": False, "success": False, "error": None}

def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _pull_image() -> None:
    try:
        result = subprocess.run(
            ["docker", "pull", READYSET_IMAGE],
            capture_output=True,
            text=True,
            timeout=600,
        )

        if result.returncode == 0:
            _pull_status["success"] = True
            print(f"Pre-pulled ReadySet image: {READYSET_IMAGE}")
        else:
            _pull_status["error"] = result.stderr.strip() or "Pull failed"
            print("ReadySet image pre-pull failed (Docker not running?)")

    except subprocess.TimeoutExpired:
        _pull_status["error"] = "Pull timed out after 10 minutes"
        print("ReadySet image pre-pull timed out")
    except Exception as e:
        _pull_status["error"] = str(e)
        print(f"ReadySet image pre-pull failed: {e}")
    finally:
        _pull_status["completed"] = True


def start_prepull() -> None:
    """Kick off background pull. Non-blocking, silent if Docker unavailable."""
    if _pull_status["started"]:
        return

    if not _docker_available():
        return

    _pull_status["started"] = True
    global _pull_thread
    _pull_thread = threading.Thread(target=_pull_image, daemon=True)
    _pull_thread.start()
    print(f"Pre-pulling ReadySet Docker image: {READYSET_IMAGE}")


def get_prepull_status() -> dict:
    return _pull_status.copy()

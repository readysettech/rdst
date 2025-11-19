import json
from typing import Dict, Any


def check_container_needs_start(container_detection: Dict[str, Any] = None, **kwargs) -> Dict[str, Any]:
    """
    Check if a container needs to be started or created.

    Args:
        container_detection: Result from detect container function
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing:
        - success: boolean
        - needs_start: boolean - container exists but is stopped
        - needs_create: boolean - container doesn't exist at all
        - message: status message
    """
    # Parse JSON string if needed
    if isinstance(container_detection, str):
        container_detection = json.loads(container_detection)

    try:
        if not container_detection:
            return {
                "success": True,
                "needs_start": False,
                "needs_create": True,
                "message": "No container detection results provided"
            }

        exists = container_detection.get("exists", False)
        running = container_detection.get("running", False)

        if not exists:
            return {
                "success": True,
                "needs_start": False,
                "needs_create": True,
                "message": "Container needs to be created"
            }

        if exists and not running:
            return {
                "success": True,
                "needs_start": True,
                "needs_create": False,
                "message": "Container exists but is stopped"
            }

        return {
            "success": True,
            "needs_start": False,
            "needs_create": False,
            "message": "Container is already running"
        }

    except Exception as e:
        return {
            "success": False,
            "needs_start": False,
            "needs_create": False,
            "error": f"Failed to check container status: {str(e)}"
        }

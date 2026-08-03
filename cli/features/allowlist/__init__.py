"""Provider IP allowlist guidance and explicit write-back."""

from .service import (
    connection_failure_category,
    PROVIDER_IP_BLOCKED_MAYBE,
    is_provider_network_failure,
    provider_for_target,
)

__all__ = [
    "connection_failure_category",
    "PROVIDER_IP_BLOCKED_MAYBE",
    "is_provider_network_failure",
    "provider_for_target",
]

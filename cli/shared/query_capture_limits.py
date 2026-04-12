MAX_PROXIED_QUERIES = 1000
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 1

# Maximum query length to capture from database query logs.
# This limit applies to queries captured from rdst top and saved to the registry.
MAX_QUERY_LENGTH = 64 * 1024  # 64KB

# Threshold below which we warn about database query size settings.
DB_QUERY_SIZE_WARN_THRESHOLD = 4 * 1024  # 4KB

__all__ = [
    "DB_QUERY_SIZE_WARN_THRESHOLD",
    "DEFAULT_TIMEOUT",
    "MAX_PROXIED_QUERIES",
    "MAX_QUERY_LENGTH",
    "MAX_RETRIES",
    "RETRY_DELAY",
]

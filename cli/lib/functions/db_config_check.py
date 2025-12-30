"""
Database Configuration Types

Type definitions for database target configuration.
"""

from typing import TypedDict

try:
    # Python 3.11+
    from typing import Required, NotRequired
except ImportError:
    # Python 3.8-3.10
    from typing_extensions import Required, NotRequired


class TargetConfig(TypedDict):
    """Type definition for database target configuration.

    Uses Required[] and NotRequired[] to explicitly mark field optionality,
    similar to TypeScript's key?: value syntax.

    Example:
        config: TargetConfig = {
            'engine': 'postgresql',  # Required
            'host': 'localhost',     # Required
            'port': 5432,            # Required
            'database': 'mydb',      # Required
            'user': 'admin',         # Required
            'tls': True,             # Optional
        }
    """
    # Required fields (must be present)
    engine: Required[str]
    host: Required[str]
    port: Required[int]
    database: Required[str]
    user: Required[str]

    # Optional fields (may be omitted)
    password: NotRequired[str]
    password_env: NotRequired[str]
    tls: NotRequired[bool]
    proxy: NotRequired[str]
    name: NotRequired[str]

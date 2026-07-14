"""QPRouter internals for an SQP (squeepy) proxy fronting ReadySet.

This package is the reusable core the demo builds on: it reads SQP digests and
pattern rules, drives manual cache create/drop, computes QueryPilot-style
selection reasons, and scrapes ReadySet cache metrics.
"""

from __future__ import annotations

from features.qprouter.qprouter import QPRouter, PatternRow
from features.qprouter.sqp_client import SqpAdminClient, SqpError
from features.qprouter.readyset_client import ReadysetClient

__all__ = ["QPRouter", "PatternRow", "SqpAdminClient", "SqpError", "ReadysetClient"]

"""Email address validation shared by CLI and API entry points."""

from __future__ import annotations

import re

# Kept byte-for-byte in sync with the client gate regex in
# web-apps/apps/rdst/src/components/emailValidation.ts. The parity test
# (tests/unit/test_settings_email_route.py + emailValidation.test.tsx) drives
# both against the same fixture so a divergence turns a suite red.
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

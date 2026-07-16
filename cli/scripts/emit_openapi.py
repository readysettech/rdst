#!/usr/bin/env python3
"""Dump the RDST FastAPI OpenAPI schema to stdout as JSON.

Used by the frontend type generator (see
web-apps/apps/rdst/package.json scripts.gen:api) and by CI via
rdst/scripts/check_api_contract.sh.

Must be runnable as a standalone script from the rdst/ directory:
    uv run python scripts/emit_openapi.py > ../web-apps/apps/rdst/src/lib/openapi.schema.json

Output is deterministic (sorted keys) so regeneration only produces diffs
when the API surface actually changes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure `rdst/` is on sys.path so `shared.api.app` imports resolve when run
# from anywhere (CI, pre-commit, ad-hoc).
RDST_ROOT = Path(__file__).resolve().parent.parent
if str(RDST_ROOT) not in sys.path:
    sys.path.insert(0, str(RDST_ROOT))


def main() -> int:
    # create_app() only constructs the FastAPI instance and registers
    # routers; the lifespan callback does not fire unless the app is
    # actually started. `app.openapi()` just walks the route table, so
    # this is side-effect-free.
    from shared.api.app import create_app

    app = create_app()
    schema = app.openapi()
    json.dump(schema, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

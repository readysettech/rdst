#!/usr/bin/env bash
#
# Enforce the typed contract between the FastAPI backend and the rdst-web
# frontend.
#
# Regenerates src/lib/openapi.schema.json from the live FastAPI app, then
# regenerates src/lib/api.generated.ts from it, then runs `tsc --noEmit` on
# the frontend. If any call site disagrees with the current backend shape
# (missing field, renamed field, changed type, new required property),
# tsc fails and so does this script.
#
# Deliberately *not* a diff-gate: legitimate schema changes should not fail
# CI — only type mismatches that actually break frontend call sites should.
# The regenerated files are still committed for IDE/DX; CI just refreshes
# them before typechecking to make sure staleness can't hide a real break.
#
# Usage (from repo root or anywhere):
#     rdst/scripts/check_api_contract.sh
#
# Assumes:
#   - `uv` is available and rdst/ has its venv set up
#   - `pnpm` is available and web-apps/ dependencies are installed
#

set -euo pipefail

export COREPACK_ENABLE_DOWNLOAD_PROMPT=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RDST_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${RDST_DIR}/.." && pwd)"
WEB_APPS_DIR="${REPO_ROOT}/web-apps"
SCHEMA_FILE="${WEB_APPS_DIR}/apps/rdst/src/lib/openapi.schema.json"
GENERATED_TS="${WEB_APPS_DIR}/apps/rdst/src/lib/api.generated.ts"

echo "==> Emitting FastAPI OpenAPI schema"
(cd "${RDST_DIR}" && uv run python scripts/emit_openapi.py) > "${SCHEMA_FILE}"

echo "==> Regenerating TypeScript types"
(cd "${WEB_APPS_DIR}" && pnpm --filter rdst-web gen:api)

echo "==> Typechecking rdst-web"
(cd "${WEB_APPS_DIR}" && pnpm --filter rdst-web exec tsc --noEmit)

echo "==> API contract OK"
echo "    Schema:    ${SCHEMA_FILE}"
echo "    Types:     ${GENERATED_TS}"

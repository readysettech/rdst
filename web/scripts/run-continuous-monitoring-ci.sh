#!/usr/bin/env bash
# RDST Continuous Monitoring E2E for one engine (RDST_E2E_ENGINE=postgresql or
# mysql): the ci-shop database, its load and a cuttable proxy, monitored by the
# real server with every AI call going through this CL's preview Worker after a
# real mailbox sign-in.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
WEB_APPS_DIR="${REPO_ROOT}/web-apps"
RDST_ROOT="${REPO_ROOT}/rdst"
PNPM_VERSION="${PNPM_VERSION:-11.20.0}"
ENGINE="${RDST_E2E_ENGINE:?RDST_E2E_ENGINE must be postgresql or mysql}"

# The preview Worker is named for the CL and the build (run-web-e2e-ci.sh).
if [[ -z "${RDST_KEYSERVICE_URL:-}" && -n "${GERRIT_CHANGE_ID:-}" ]]; then
  export RDST_KEYSERVICE_URL="https://keyservice-pr-${GERRIT_CHANGE_ID}-${BUILDKITE_BUILD_NUMBER:-0}.readyset.io"
fi
for req in RDST_KEYSERVICE_URL RDST_CI_RESEND_API_KEY; do
  if [[ -z "${!req:-}" ]]; then
    echo "ERROR: $req is not set" >&2
    exit 1
  fi
done
if [[ "$RDST_KEYSERVICE_URL" != "https://keyservice-pr-"* ]]; then
  echo "ERROR: RDST_KEYSERVICE_URL must be a per-CL preview Worker, got:" >&2
  echo "       $RDST_KEYSERVICE_URL" >&2
  exit 1
fi
echo "--- Target: ${RDST_KEYSERVICE_URL} (${ENGINE})"

# shellcheck source=/dev/null
source "${RDST_ROOT}/.buildkite/ensure_node.sh"
ensure_node

echo "--- Preparing the environment"
COREPACK_BIN="${COREPACK_BIN:-/tmp/rdst-cim-corepack-bin}"
mkdir -p "$COREPACK_BIN"
corepack enable --install-directory "$COREPACK_BIN"
export PATH="${COREPACK_BIN}:${PATH}"
corepack prepare "pnpm@${PNPM_VERSION}" --activate

if ! command -v uv >/dev/null 2>&1; then
  UV_INSTALL_DIR=/tmp/uv-bin
  export UV_INSTALL_DIR
  mkdir -p "$UV_INSTALL_DIR"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${UV_INSTALL_DIR}:${PATH}"
fi
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/rdst-cim-uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/tmp/rdst-cim-python}"
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-/tmp/rdst-cim-venv}"
uv python install 3.12 --quiet
# Stubs for the release-only files rdst/pyproject.toml expects (run-web-e2e-ci.sh).
if [[ ! -f "${RDST_ROOT}/_version_build.py" ]]; then
  echo '__version__ = "0.0.0.dev0"' > "${RDST_ROOT}/_version_build.py"
fi
mkdir -p "${RDST_ROOT}/web_dist"
uv sync --directory "$RDST_ROOT" --quiet

echo "--- Installing frontend dependencies"
cd "$WEB_APPS_DIR"
ELECTRON_SKIP_BINARY_DOWNLOAD=1 pnpm install --frozen-lockfile

echo "--- Running RDST Continuous Monitoring E2E (${ENGINE})"
cd "$WEB_APPS_DIR/apps/rdst"
RESULTS_DIR="test-results"
mkdir -p "$RESULTS_DIR"
node ./e2e/run.mjs --config playwright.continuous-monitoring.config.ts 2>&1 \
  | tee "${RESULTS_DIR}/rdst-continuous-monitoring-${ENGINE}.log"

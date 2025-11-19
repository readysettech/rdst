#!/usr/bin/env bash

# Usage:
#   ./test_al23_locally.sh [postgresql|mysql]
#   SKIP_BUILD=1 ./test_al23_locally.sh postgresql  # Skip build, use existing binary
#   TENANT=sean01 ./test_al23_locally.sh postgresql  # Use custom tenant for build

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Script is at: rdst/tests/integration/
# Go up 3 levels to get to repository root
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
RDST_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Default tenant
TENANT="${TENANT:-dev01}"

# Check required environment variables
if [[ -z "${API_BASE_URL:-}" ]]; then
  echo "ERROR: API_BASE_URL environment variable must be set"
  echo "Example: export API_BASE_URL='https://api-dev01.apps.readyset.cloud'"
  exit 1
fi

# Build AL23 binary if needed
if [[ "${SKIP_BUILD:-}" == "1" ]]; then
  echo "Skipping build (SKIP_BUILD=1)"
else
  echo "========================================================================"
  echo "Building AL23 Binary (tenant: $TENANT)"
  echo "========================================================================"
  cd "$REPO_ROOT"
  "$RDST_ROOT/orchestrate_rdst.sh" al23 "" "$TENANT" || {
    echo "ERROR: Failed to build AL23 binary"
    exit 1
  }
fi

# Find the build directory
BUILD_DIR=$(ls -td /tmp/rdst_build_* | head -1)
echo "Build directory: $BUILD_DIR"

# Check if RPM exists
if [[ ! -f "$BUILD_DIR/rdst.rpm.al23" ]]; then
  echo "ERROR: rdst.rpm.al23 not found in $BUILD_DIR"
  echo "Build may have failed. Check the build output above."
  exit 1
fi

RDST_BINARY="$BUILD_DIR/usr/bin/rdst"

# Extract the binary from RPM if not already extracted
if [[ ! -f "$RDST_BINARY" ]]; then
  echo ""
  echo "========================================================================"
  echo "Extracting Binary from RPM"
  echo "========================================================================"
  docker run --rm \
    -v "$BUILD_DIR:/work" \
    -w /work \
    amazonlinux:2023 \
    bash -c "dnf install -y cpio && rpm2cpio rdst.rpm.al23 | cpio -idmv"

  if [[ ! -f "$RDST_BINARY" ]]; then
    echo "ERROR: Binary not extracted to $RDST_BINARY"
    exit 1
  fi
  echo "Binary extracted: $RDST_BINARY"
else
  echo "Binary already extracted (skipping extraction)"
fi

echo "Using AL23 binary: $RDST_BINARY"
echo "Binary size: $(du -h "$RDST_BINARY" | cut -f1)"
echo "Binary timestamp: $(stat -f "%Sm" "$RDST_BINARY")"

# Run integration tests inside amazonlinux:2023 container
echo ""
echo "========================================================================"
echo "Running Tests in AL23 Environment"
echo "========================================================================"
echo "Test runtime: amazonlinux:2023 Docker container"
echo "RDST binary: $RDST_BINARY (mounted read-only)"
echo ""

# Build environment variables to pass through
ENV_ARGS=(
  -e "API_BASE_URL=$API_BASE_URL"
  -e "RDST_BINARY=/rdst_binary/rdst"
  -e "PYTHONPATH=/workspace"
  -e "RDST_LLM_SHARED_KEY=${RDST_LLM_SHARED_KEY:-ALPHA-STATIC-SHARED-KEY}"
  -e "RDST_LLM_PROVIDER=${RDST_LLM_PROVIDER:-lmstudio}"
  -e "LMSTUDIO_BASE_URL=${LMSTUDIO_BASE_URL:-http://127.0.0.1:65535/v1/chat/completions}"
)

# Add optional environment variables if set
[[ -n "${ADMIN_API_TOKEN:-}" ]] && ENV_ARGS+=(-e "ADMIN_API_TOKEN=$ADMIN_API_TOKEN")
[[ -n "${PSQL_CONNECTION_STRING:-}" ]] && ENV_ARGS+=(-e "PSQL_CONNECTION_STRING=$PSQL_CONNECTION_STRING")
[[ -n "${MYSQL_CONNECTION_STRING:-}" ]] && ENV_ARGS+=(-e "MYSQL_CONNECTION_STRING=$MYSQL_CONNECTION_STRING")

# Run tests in container
echo "Mounting $RDST_ROOT to /workspace in container"
docker run --rm -it \
  --network host \
  -v "$RDST_ROOT:/workspace" \
  -v "$BUILD_DIR/usr/bin:/rdst_binary:ro" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -w /workspace \
  "${ENV_ARGS[@]}" \
  amazonlinux:2023 \
  bash -c "
    echo '→ Installing AL23 dependencies...'
    dnf install -y python3.11 python3.11-pip docker tar gzip --allowerasing -q &&

    echo '→ Installing Python requirements...'
    python3.11 -m pip install --no-cache-dir -q -r requirements.txt &&

    echo '→ Verifying RDST binary...'
    /rdst_binary/rdst version &&
    echo ''

    # Run tests
    tests/integration/run_tests.sh \"\$@\"
  " -- "$@"

echo ""
echo "========================================================================"
echo "AL23 Integration Tests Complete"
echo "========================================================================"

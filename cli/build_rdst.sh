#!/usr/bin/env bash
# Build RDST Nuitka binary and package it for the selected distro.
# Expects to run inside the builder container with /workspace mounted to rdst/ dir.

set -Eeuo pipefail
set -x

echo "[dbg] whoami: $(whoami)"
echo "[dbg] script: $(readlink -f "$0")"
echo "[dbg] pwd:    $(pwd)"

# Expect: debian | genericrpm | al2023
DISTRO="${DISTRO:-debian}"

# Always use a numeric default version (Debian/RPM-safe)
VERSION="$(date -u +%Y.%m.%d.%H%M%S)"
echo "[dbg] VERSION=${VERSION}"

# New repo layout: rdst/ mounted to /workspace
RDST_DIR="/workspace"

echo "[dbg] RDST_DIR=${RDST_DIR}"
echo "[dbg] ls /workspace:"; ls -la /workspace || true

# fpm check
if ! command -v fpm >/dev/null 2>&1; then
  echo "❌ fpm not found in builder image"; exit 1
fi
fpm --version || true

BUILD_DIR="${BUILD_DIR:-/build}"
SRC_DIR="${BUILD_DIR}/src"
PKG_DIR="${BUILD_DIR}/package"

rm -rf "${BUILD_DIR:?}/"*
mkdir -p "$SRC_DIR" "$PKG_DIR/usr/bin"

# Copy rdst source from /workspace
echo "[dbg] Copying rdst.py..."
cp "${RDST_DIR}/rdst.py" "$SRC_DIR/"

# Copy lib/ tree
echo "[dbg] Copying lib/..."
if [[ -d "${RDST_DIR}/lib" ]]; then
  mkdir -p "${SRC_DIR}/lib"
  cp -a "${RDST_DIR}/lib/." "${SRC_DIR}/lib/"
  find "${SRC_DIR}/lib" -type d -exec bash -c 'f="$1/__init__.py"; [[ -f "$f" ]] || :> "$f"' _ {} \;
fi

echo "[dbg] src root listing:"; ls -la "$SRC_DIR" || true
if [[ -d "${SRC_DIR}/lib" ]]; then
  echo "[dbg] src/lib sample:"
  find "${SRC_DIR}/lib" -maxdepth 2 -type f -name '*.py' | head -n 30
fi

echo "[⚙️] Compiling with Nuitka…"
cd "$SRC_DIR"

# Use python3.11 inside python:3.11-slim-bullseye builder
python3.11 -m nuitka \
  --standalone \
  --onefile \
  --onefile-no-compression \
  --lto=yes \
  --output-dir="$BUILD_DIR" \
  --output-filename=rdst \
  --enable-plugin=implicit-imports \
  --enable-plugin=anti-bloat \
  --nofollow-import-to=unittest \
  --nofollow-import-to=boto3 \
  --nofollow-import-to=botocore \
  --include-package=lib \
  --include-data-dir=lib/workflows=lib/workflows \
  --include-module=psycopg2 \
  --include-module=pymysql \
  --include-module=pygments \
  rdst.py

install -m755 "$BUILD_DIR/rdst" "$PKG_DIR/usr/bin/rdst"

case "$DISTRO" in
  debian)
    fpm -s dir -t deb -n rdst -v "$VERSION" \
        --description "ReadySet rdst CLI (.deb)" \
        -C "$PKG_DIR" -p "$BUILD_DIR/rdst.deb"
    ;;
  genericrpm)
    fpm -s dir -t rpm -n rdst -v "$VERSION" \
        --description "ReadySet rdst CLI (.rpm)" \
        -C "$PKG_DIR" -p "$BUILD_DIR/rdst.rpm"
    ;;
  al2023)
    fpm -s dir -t rpm -n rdst -v "$VERSION" \
        --description "ReadySet rdst CLI (.rpm.al23)" \
        -C "$PKG_DIR" -p "$BUILD_DIR/rdst.rpm.al23"
    ;;
  *)
    echo "Unknown DISTRO: ${DISTRO}"; exit 1 ;;
esac

echo "✅ Build complete. Artifacts in $BUILD_DIR"

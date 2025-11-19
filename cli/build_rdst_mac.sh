#!/usr/bin/env bash
# Build RDST Nuitka binary for macOS and package it as tar.gz
# This script runs directly on macOS (not in Docker)

set -Eeuo pipefail
set -x

echo "[dbg] whoami: $(whoami)"
echo "[dbg] script: $(readlink -f "$0" || echo "$0")"
echo "[dbg] pwd:    $(pwd)"

# Expect: macos
DISTRO="${DISTRO:-macos}"

# Always use a numeric default version
VERSION="$(date -u +%Y.%m.%d.%H%M%S)"
echo "[dbg] VERSION=${VERSION}"

# Source code should be in current directory (rdst package structure)
RDST_DIR="$(pwd)"

echo "[dbg] RDST_DIR=${RDST_DIR}"

# Check that we have the main files we need
if [[ ! -f "${RDST_DIR}/rdst.py" ]]; then
    echo "❌ Required file not found: ${RDST_DIR}/rdst.py"
    exit 1
fi

if [[ ! -d "${RDST_DIR}/lib" ]]; then
    echo "❌ Required directory not found: ${RDST_DIR}/lib"
    exit 1
fi

BUILD_DIR="${BUILD_DIR:-${RDST_DIR}/build}"
SRC_DIR="${BUILD_DIR}/src"
PKG_DIR="${BUILD_DIR}/package"

rm -rf "${BUILD_DIR:?}/"*
mkdir -p "$SRC_DIR" "$PKG_DIR"

# 1) Copy ALL top-level .py from rdst directory
cp "${RDST_DIR}"/*.py "$SRC_DIR/" 2>/dev/null || true
find "$SRC_DIR" -maxdepth 1 -type f \( -name 'test_*.py' -o -name '*_test.py' -o -name '*tests*.py' \) ! -name 'rdst.py' -delete

# 2) Ensure rdst.py is present; if not top-level, search and copy it in.
if [[ ! -f "$SRC_DIR/rdst.py" ]]; then
  echo "[dbg] rdst.py not found at top-level; searching under ${RDST_DIR}"
  RDST_ENTRY="$(find "${RDST_DIR}" -maxdepth 4 -type f -name 'rdst.py' | head -n1 || true)"
  echo "[dbg] RDST_ENTRY='${RDST_ENTRY:-<none found>}'"
  if [[ -n "${RDST_ENTRY:-}" && -f "$RDST_ENTRY" ]]; then
    cp "$RDST_ENTRY" "$SRC_DIR/rdst.py"
  else
    echo "❌ rdst.py not found anywhere under ${RDST_DIR}"
    exit 1
  fi
fi

# 3) Copy full lib/ tree (for imports like from lib.cache_manager... import CacheManager)
if [[ -d "${RDST_DIR}/lib" ]]; then
  mkdir -p "${SRC_DIR}/lib"
  cp -a "${RDST_DIR}/lib/." "${SRC_DIR}/lib/"
  find "${SRC_DIR}/lib" -type d -exec bash -c 'f="$1/__init__.py"; [[ -f "$f" ]] || :> "$f"' _ {} \;
fi

# 4) Copy any additional files
cp "${RDST_DIR}"/*.{sh,json,txt} "${SRC_DIR}/" 2>/dev/null || true

echo "[dbg] src root listing:"; ls -la "$SRC_DIR" || true
if [[ -d "${SRC_DIR}/lib" ]]; then
  echo "[dbg] src/lib sample:"
  find "${SRC_DIR}/lib" -maxdepth 2 -type f -name '*.py' | head -n 30
fi

echo "[⚙️] Compiling with Nuitka on macOS…"
cd "$SRC_DIR"

# Auto-include copied top-level modules (covers dynamic imports)
INCLUDE_FLAGS=()
shopt -s nullglob
for f in *.py; do
  mod="${f%.py}"
  [[ "$mod" == "rdst" || "$mod" == "__init__" ]] && continue
  INCLUDE_FLAGS+=( "--include-module=${mod}" )
done
shopt -u nullglob

# Use system python3 on macOS - prefer stable versions over 3.13 (randbits issues)
PYTHON_CMD=""
for py in python3.12 python3.11 python3.10 python3.9; do
    if command -v "$py" >/dev/null 2>&1; then
        PYTHON_CMD="$py"
        PY_VERSION=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        echo "[dbg] Found Python: $PYTHON_CMD ($("$py" --version)) -> $PY_VERSION"
        
        # Explicitly reject Python 3.13+ due to numpy compatibility issues
        if [[ "$PY_VERSION" > "3.12" ]]; then
            echo "[⚠️] Skipping Python $PY_VERSION due to numpy randbits compatibility issues"
            continue
        fi
        
        # Check if this Python version has basic randbits support
        if "$py" -c "from random import randbits; import secrets; print('randbits available')" 2>/dev/null; then
            echo "[✅] Python $PY_VERSION has working randbits support"
            break
        else
            echo "[⚠️] Python $PY_VERSION has randbits issues, trying next..."
            continue
        fi
    fi
done

if [[ -z "$PYTHON_CMD" ]]; then
    echo "❌ No Python 3 found on system"
    exit 1
fi

# Create a build requirements file with explicit version constraints
cat > "$SRC_DIR/build-requirements.txt" << 'EOF'
# Build requirements for RDST macOS - Python 3.12/3.11 compatible versions
numpy>=1.24.0,<2.0.0
pandas>=2.0.0,<3.0.0
nuitka>=1.8.0
# Ensure we get the right versions of numpy dependencies
setuptools>=60.0.0
wheel>=0.37.0
EOF

# Install/upgrade dependencies with version constraints for Python 3.13 compatibility
echo "[⚙️] Installing Python dependencies with explicit version constraints..."
"$PYTHON_CMD" -m pip install --upgrade pip setuptools wheel

# Install dependencies from requirements file to ensure compatibility
"$PYTHON_CMD" -m pip install -r "$SRC_DIR/build-requirements.txt" --force-reinstall --no-cache-dir

# Check if Nuitka is available
if ! "$PYTHON_CMD" -m nuitka --version >/dev/null 2>&1; then
    echo "❌ Nuitka not found. Installing..."
    "$PYTHON_CMD" -m pip install nuitka
fi

# Verify numpy can import randbits correctly
echo "[🔍] Testing numpy compatibility..."
if ! "$PYTHON_CMD" -c "import numpy; import numpy.random; print('✅ Numpy import successful')" 2>/dev/null; then
    echo "⚠️ Numpy compatibility issues detected, trying alternative approach..."
    # Try installing from conda-forge which has better compatibility
    if command -v conda >/dev/null 2>&1; then
        echo "[🔧] Attempting conda-forge install..."
        conda install -c conda-forge 'numpy>=1.24.0' 'pandas>=2.0.0' -y || true
    fi
fi

"$PYTHON_CMD" -m nuitka \
  --standalone \
  --lto=yes \
  --output-dir="$BUILD_DIR" \
  --output-filename=rdst \
  --enable-plugin=implicit-imports \
  --enable-plugin=anti-bloat \
  --include-package=lib \
  "${INCLUDE_FLAGS[@]}" \
  rdst.py

# Create Mac installation structure
INSTALL_DIR="${BUILD_DIR}/rdst-install"
mkdir -p "$INSTALL_DIR"
cp -r "$BUILD_DIR/rdst.dist" "$INSTALL_DIR/rdst"
chmod +x "$INSTALL_DIR/rdst/rdst"

# Create install script
cat > "$INSTALL_DIR/install.sh" << 'EOF'
#!/bin/bash
# RDST Installation Script for macOS

set -e

INSTALL_PATH="/opt/readyset"
BINARY_PATH="/usr/local/bin/rdst"

echo "🚀 Installing RDST for macOS..."

# Check if running as root for /opt installation
if [[ $EUID -ne 0 ]]; then
   echo "❌ This script must be run as root (use sudo)"
   exit 1
fi

# Create installation directory
mkdir -p "$INSTALL_PATH"

# Copy RDST distribution
echo "📁 Installing to $INSTALL_PATH/rdst..."
cp -r rdst "$INSTALL_PATH/"
chmod +x "$INSTALL_PATH/rdst/rdst"

# Create symlink
echo "🔗 Creating symlink at $BINARY_PATH..."
ln -sf "$INSTALL_PATH/rdst/rdst" "$BINARY_PATH"

echo "✅ RDST installed successfully!"
echo "💡 You can now run: rdst --help"
EOF

chmod +x "$INSTALL_DIR/install.sh"

# Create README
cat > "$INSTALL_DIR/README.md" << 'EOF'
# RDST for macOS

## Installation

1. Extract this archive:
   ```bash
   tar -xzf rdst-mac.tar.gz
   cd rdst-install
   ```

2. Run the installer:
   ```bash
   sudo ./install.sh
   ```

3. Verify installation:
   ```bash
   rdst --help
   ```

## Usage

Once installed, you can use RDST commands:
- `rdst configure` - Set up database connections
- `rdst top` - View slow queries
- `rdst analyze` - Analyze SQL queries

For more help: `rdst --help`
EOF

# Create tar.gz package
echo "[📦] Creating Mac package..."
cd "$BUILD_DIR"
tar -czf "rdst-mac.tar.gz" -C rdst-install .

echo "✅ Build complete. Mac package: $BUILD_DIR/rdst-mac.tar.gz"
ls -la "$BUILD_DIR/rdst-mac.tar.gz"
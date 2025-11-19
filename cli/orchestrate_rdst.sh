#!/usr/bin/env bash
set -eo pipefail

# Usage: orchestrate_rdst.sh <distro> [version] [tenant]
if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "Usage: $0 <distro: deb|rpm|al23|mac> [version] [tenant]"
  exit 1
fi

DISTRO_TARGET="$1"
VERSION="$2"
TENANT="${3:-dev01}"

# This file lives in rdst/ at repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# AWS Build Account for rdst binaries
BUILD_ACCOUNT_ID="305232526136"
BUILD_REGION="us-east-2"
# Use temp directory to avoid WSL2/Windows filesystem bullshit
BUILD_DIR="/tmp/rdst_build_$(date +%s)"
mkdir -p "${BUILD_DIR}"
echo "[🔧] Using build directory: ${BUILD_DIR}"

build_rdst () {
  local docker_image="$1" dockerfile="$2" env_distro="$3" artifact_ext="$4"

  echo "[🐳] Building image ${docker_image} …"
  docker build -t "${docker_image}" -f "${dockerfile}" "${SCRIPT_DIR}"

  echo "[🚀] Building RDST for ${env_distro} …"
  docker run --rm \
    -v "${SCRIPT_DIR}":/workspace \
    -v "${BUILD_DIR}":/build \
    -w /workspace \
    -e DISTRO="${env_distro}" \
    -e BUILD_DIR="/build" \
    "${docker_image}" \
    bash build_rdst.sh

  artifact="rdst.${artifact_ext}"
  artifact_path="${BUILD_DIR}/${artifact}"
  [[ -f "${artifact_path}" ]] || { echo "❌  Build produced no file at ${artifact_path}"; exit 1; }

  # tenants/buckets (same pattern as obs)
  declare -a tenants=()
  if [[ "${TENANT}" == "dev01" || "${TENANT}" == "dev02" ]]; then
    tenants=("dev01" "dev02")
  else
    tenants=("${TENANT}")
  fi

  for t in "${tenants[@]}"; do
    if [[ "${t}" == "stage01" ]]; then
      bucket="readysetobservabilityagent"
    else
      bucket="readysetobservabilityagent-test"
    fi
    prefix="${t}/latest"
    key="${prefix}/${artifact}"
    s3_path="s3://${bucket}/${key}"

    if aws s3 ls "${s3_path}" >/dev/null 2>&1; then
      ts=$(date +%Y%m%d%H%M%S)
      aws s3 mv "${s3_path}" "s3://${bucket}/${prefix}/rdst.backup-${ts}.${artifact_ext}"
    fi

    echo "[☁️] Uploading ${artifact} to ${s3_path} …"
    aws s3 cp "${artifact_path}" "${s3_path}"
    echo "[✅] Uploaded: https://${bucket}.s3.amazonaws.com/${key}"

    # Upload raw binary for integration testing
    binary_path="${BUILD_DIR}/rdst"
    if [[ -f "${binary_path}" ]]; then
      binary_key="${prefix}/rdst-binary"
      binary_s3_path="s3://${bucket}/${binary_key}"
      echo "[☁️] Uploading raw binary to ${binary_s3_path} …"
      if aws s3 cp "${binary_path}" "${binary_s3_path}"; then
        echo "[✅] Binary uploaded: https://${bucket}.s3.amazonaws.com/${binary_key}"
      else
        echo "[⚠️] Binary upload failed (continuing anyway)"
      fi
    fi
  done
}

case "${DISTRO_TARGET}" in
  deb)  build_rdst "readyset-rdst-builder-deb"       "${SCRIPT_DIR}/Dockerfile.deb.ubuntu" "debian"   "deb"      ;;
  rpm)  build_rdst "readyset-rdst-builder-rpm"       "${SCRIPT_DIR}/Dockerfile.rpm"        "genericrpm" "rpm"     ;;
  al23) build_rdst "readyset-rdst-builder-rpm-al23"  "${SCRIPT_DIR}/Dockerfile.rpm.al2023" "al2023"    "rpm.al23" ;;
  mac)
    echo "[🍎] Building RDST for macOS with numpy 1.24.4..."
    bash "${SCRIPT_DIR}/orchestrate_mac_build.sh" "${TENANT}"
    ;;
  *) echo "❌  Unknown distro '${DISTRO_TARGET}'. Use deb | rpm | al23 | mac."; exit 1 ;;
esac

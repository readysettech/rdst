#!/usr/bin/env bash
set -eo pipefail
BUCKET="readysetobservabilityagent"
SRC_TENANT="stage01"
DST_FOLDER="release"

for ext in deb rpm rpm.al23; do
  artifact="rdst.${ext}"
  src="s3://${BUCKET}/${SRC_TENANT}/latest/${artifact}"
  dst="s3://${BUCKET}/${DST_FOLDER}/${artifact}"

  if aws s3 ls "${dst}" >/dev/null 2>&1; then
    ts=$(date +%Y%m%d-%H%M%S)
    aws s3 cp "${dst}" "s3://${BUCKET}/${DST_FOLDER}/${artifact}.backup-${ts}"
  fi

  aws s3 cp "${src}" "${dst}"
done

echo "Copied RDST artifacts to release."

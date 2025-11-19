#!/usr/bin/env bash
set -eo pipefail

# Buildkite wrapper for Mac RDST builds
# Usage: build_rdst_mac_buildkite.sh <tenant>

TENANT="${1:-dev01}"

echo "🍎 Starting Mac RDST build for buildkite (tenant: $TENANT)"

# Source AWS credentials export function (same as observability agent)
source cloud/control_plane/.buildkite/export_aws_creds.sh

# Set up AWS credentials via Tailscale (same pattern as obs agent)
BUILD_ACCOUNT_ID="305232526136"
BUILD_REGION="us-east-2"

if [ "${DUPLO_ENV}" == "dev" ]; then
  AUTH0_SECRETS_PATH="dev/deployment/duplo"
  AUTH0_SECRETS_SUFFIX="5lydzr"
  export ECR_ACCOUNT_ID="701495964134"
else
  AUTH0_SECRETS_PATH="prod_stage/deployment/duplo"
  AUTH0_SECRETS_SUFFIX="OjsaBh"
  export ECR_ACCOUNT_ID="828804413457"
fi

DUPLO_TOKEN_SECRET_NAME="${AUTH0_SECRETS_PATH}-${AUTH0_SECRETS_SUFFIX}"
DUPLO_TOKEN_ARN="arn:aws:secretsmanager:${BUILD_REGION}:${BUILD_ACCOUNT_ID}:secret:${DUPLO_TOKEN_SECRET_NAME}"

RESULT=$(aws secretsmanager get-secret-value --secret-id ${DUPLO_TOKEN_ARN} \
          --cli-connect-timeout 1 | jq -r ".SecretString | fromjson | .${DUPLO_ENV}")
echo "{$RESULT}"

READYSET_CLOUD_TOKEN=$(aws secretsmanager get-secret-value \
  --secret-id "${DUPLO_ENV}/token-service" \
  --region us-east-2 \
  | jq -r --arg key "readyset-cloud-token-${DUPLO_TENANT}" '.SecretString | fromjson | .[$key]')
export READYSET_CLOUD_TOKEN

READYSET_TOKEN_SERVICE_URL=$(aws secretsmanager get-secret-value \
  --secret-id "${DUPLO_ENV}/token-service" \
  --region us-east-2 \
  | jq -r '.SecretString | fromjson | ."token-service-url"')
export READYSET_TOKEN_SERVICE_URL

TAILSCALE_AUTH_KEY=$(aws secretsmanager get-secret-value \
  --secret-id "${DUPLO_ENV}/token-service" \
  --region us-east-2 \
  | jq -r '.SecretString | fromjson | ."tailscale-auth-key"')
export TAILSCALE_AUTH_KEY

# Export AWS access tokens via containerized Tailscale
export_aws_access_tokens

echo "✅ AWS credentials configured for Mac RDST build"

# Now run the actual Mac RDST build orchestration
echo "🚀 Running Mac RDST build orchestration..."
cd cloud/rdst
bash orchestrate_rdst.sh mac "" "$TENANT"

echo "🎉 Mac RDST build completed successfully!"
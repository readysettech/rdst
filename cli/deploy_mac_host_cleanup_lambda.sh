#!/usr/bin/env bash
#
# Deploy Mac Host Cleanup Lambda to Production
#
# Assumes AWS credentials with Lambda and EventBridge permissions are already exported
#
# Usage: ./deploy_mac_host_cleanup_lambda.sh [tenant] [role-arn]
#   tenant:   prod01, dev01, etc (default: prod01)
#   role-arn: Lambda execution role ARN (optional, will prompt if not provided)

set -Eeuo pipefail

TENANT="${1:-prod01}"
ROLE_ARN="${2:-}"
REGION="us-east-2"
LAMBDA_NAME="cleanup-old-mac-hosts-${TENANT}"
SCHEDULE_NAME="daily-mac-host-cleanup-${TENANT}"

echo "=========================================="
echo "Mac Host Cleanup Lambda Deployment"
echo "=========================================="
echo "Tenant:  $TENANT"
echo "Region:  $REGION"
echo "Lambda:  $LAMBDA_NAME"
echo "=========================================="
echo ""

# Get account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# If role ARN not provided, look for common Lambda execution role
if [[ -z "$ROLE_ARN" ]]; then
    echo "[📋] Looking for existing Lambda execution role..."

    # Try common role names
    for ROLE_NAME in "lambda-execution-role" "LambdaExecutionRole" "lambda-admin-role"; do
        if ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text 2>/dev/null); then
            echo "  ✓ Found role: $ROLE_NAME"
            break
        fi
    done

    # If still not found, prompt user
    if [[ -z "$ROLE_ARN" ]]; then
        echo "  ⚠️  No default Lambda execution role found"
        echo ""
        echo "Please provide Lambda execution role ARN:"
        echo "  Example: arn:aws:iam::${ACCOUNT_ID}:role/lambda-execution-role"
        echo ""
        read -p "Role ARN: " ROLE_ARN

        if [[ -z "$ROLE_ARN" ]]; then
            echo "❌ ERROR: Role ARN is required"
            exit 1
        fi
    fi
fi

echo "  Using role: $ROLE_ARN"
echo ""

# Step 1: Package Lambda function
echo "[1/3] Packaging Lambda function..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAMBDA_CODE="$SCRIPT_DIR/lambda_cleanup_old_mac_hosts.py"

if [[ ! -f "$LAMBDA_CODE" ]]; then
    echo "  ❌ ERROR: Lambda code not found at $LAMBDA_CODE"
    exit 1
fi

# Create deployment package
TEMP_DIR=$(mktemp -d)
cp "$LAMBDA_CODE" "$TEMP_DIR/lambda_function.py"
cd "$TEMP_DIR"
zip -q lambda.zip lambda_function.py
LAMBDA_ZIP="$TEMP_DIR/lambda.zip"

echo "  ✓ Created deployment package"

# Step 2: Create or update Lambda function
echo "[2/3] Deploying Lambda function..."

if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$REGION" >/dev/null 2>&1; then
    # Update existing function
    aws lambda update-function-code \
        --function-name "$LAMBDA_NAME" \
        --region "$REGION" \
        --zip-file "fileb://$LAMBDA_ZIP" \
        >/dev/null

    echo "  ✓ Updated existing Lambda function"
else
    # Create new function
    aws lambda create-function \
        --function-name "$LAMBDA_NAME" \
        --region "$REGION" \
        --runtime python3.12 \
        --role "$ROLE_ARN" \
        --handler lambda_function.lambda_handler \
        --zip-file "fileb://$LAMBDA_ZIP" \
        --timeout 300 \
        --memory-size 256 \
        --description "Cleanup Mac dedicated hosts older than 2 days" \
        --tags "Purpose=RDST-Build,Tenant=$TENANT,ManagedBy=RDST" \
        >/dev/null

    echo "  ✓ Created new Lambda function"
fi

# Cleanup temp files
rm -rf "$TEMP_DIR"

# Step 3: Create EventBridge schedule
echo "[3/3] Setting up EventBridge schedule..."

# Create EventBridge rule (runs daily at 2 AM UTC)
if aws events describe-rule --name "$SCHEDULE_NAME" --region "$REGION" >/dev/null 2>&1; then
    echo "  ✓ EventBridge rule already exists"
else
    aws events put-rule \
        --name "$SCHEDULE_NAME" \
        --region "$REGION" \
        --schedule-expression "cron(0 2 * * ? *)" \
        --description "Run Mac host cleanup daily at 2 AM UTC" \
        --state ENABLED \
        >/dev/null

    echo "  ✓ Created EventBridge rule"
fi

# Add Lambda permission for EventBridge
LAMBDA_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${LAMBDA_NAME}"
STATEMENT_ID="EventBridgeTrigger-${SCHEDULE_NAME}"

aws lambda add-permission \
    --function-name "$LAMBDA_NAME" \
    --region "$REGION" \
    --statement-id "$STATEMENT_ID" \
    --action "lambda:InvokeFunction" \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${SCHEDULE_NAME}" \
    >/dev/null 2>&1 || true

echo "  ✓ Added Lambda invoke permission"

# Add Lambda as target for EventBridge rule
aws events put-targets \
    --rule "$SCHEDULE_NAME" \
    --region "$REGION" \
    --targets "Id=1,Arn=${LAMBDA_ARN}" \
    >/dev/null

echo "  ✓ Added Lambda as EventBridge target"

echo ""
echo "=========================================="
echo "✅ Deployment Complete!"
echo "=========================================="
echo ""
echo "Lambda Function: $LAMBDA_NAME"
echo "Region:          $REGION"
echo "Schedule:        Daily at 2 AM UTC"
echo "Execution Role:  $ROLE_ARN"
echo ""
echo "Test the Lambda manually:"
echo "  aws lambda invoke --function-name $LAMBDA_NAME --region $REGION response.json && cat response.json"
echo ""
echo "View logs:"
echo "  aws logs tail /aws/lambda/$LAMBDA_NAME --region $REGION --follow"
echo ""
echo "=========================================="

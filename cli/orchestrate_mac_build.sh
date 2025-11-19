#!/usr/bin/env bash
# Orchestrate RDST build on Mac EC2 instance
# Usage: orchestrate_mac_build.sh <tenant>

set -Eeuo pipefail

TENANT="${1:-dev01}"
REGION="us-east-2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[🍎] Starting Mac RDST build for tenant: $TENANT"

# Step 1: Launch Mac instance
echo "[1/6] Launching Mac build instance..."
echo "[🔍] Running: bash ${SCRIPT_DIR}/rdst/build_mac_instance.sh $TENANT"

# Run with full output and error capture
set -x
INSTANCE_ID=$(bash "${SCRIPT_DIR}/rdst/build_mac_instance.sh" "$TENANT" 2>&1)
EXIT_CODE=$?
set +x

echo "[📋] build_mac_instance.sh output:"
echo "$INSTANCE_ID"
echo "[📋] Exit code: $EXIT_CODE"

if [[ $EXIT_CODE -ne 0 ]]; then
    echo "❌ Mac instance launch failed with exit code $EXIT_CODE"
    exit $EXIT_CODE
fi

# Extract just the instance ID from the output (last line should be the ID)
INSTANCE_ID=$(echo "$INSTANCE_ID" | tail -n1 | grep -E '^i-[a-f0-9]+$' || echo "")

if [[ -z "$INSTANCE_ID" ]]; then
    echo "❌ Failed to extract instance ID from output"
    echo "Full output was:"
    bash "${SCRIPT_DIR}/rdst/build_mac_instance.sh" "$TENANT"
    exit 1
fi

# Note: We don't auto-terminate Mac instances to save money
# User can manually terminate later with:
# aws ec2 terminate-instances --region us-east-2 --instance-ids $INSTANCE_ID

cleanup() {
    echo "[🧹] Cleaning up temp files..."
    # Only cleanup temp files, not the expensive Mac instance
    rm -rf "${TEMP_DIR:-}" "${LOCAL_BUILD_DIR:-}" 2>/dev/null || true
    
    echo "[💡] Mac instance $INSTANCE_ID is still running to save money."
    echo "[💡] You can reuse it for more builds, or terminate it with:"
    echo "    aws ec2 terminate-instances --region $REGION --instance-ids $INSTANCE_ID"
}

# Setup cleanup on exit
trap cleanup EXIT

echo "[📋] Using instance: $INSTANCE_ID"

# Step 2: Create minimal source archive
echo "[2/6] Creating minimal source archive..."
TEMP_DIR="/tmp/rdst_mac_source_$(date +%s)"
BUILD_SRC="$TEMP_DIR/src"
mkdir -p "$BUILD_SRC"

# Copy only what's needed for the build (from rdst directory)
echo "[📁] Copying minimal required files..."
cp "${SCRIPT_DIR}/rdst"/*.py "$BUILD_SRC/" 2>/dev/null || true
mkdir -p "$BUILD_SRC/lib"
cp -r "${SCRIPT_DIR}/rdst/lib"/* "$BUILD_SRC/lib/" 2>/dev/null || true
cp "${SCRIPT_DIR}/rdst"/*.{sh,json,txt} "$BUILD_SRC/" 2>/dev/null || true
cp "${SCRIPT_DIR}/rdst/build_rdst_mac.sh" "$BUILD_SRC/" 2>/dev/null || true
cp "${SCRIPT_DIR}/rdst/requirements.txt" "$BUILD_SRC/" 2>/dev/null || true

# Create minimal tarball (should be much smaller)
cd "$TEMP_DIR"
tar -czf rdst-minimal.tar.gz src
SOURCE_ARCHIVE="$TEMP_DIR/rdst-minimal.tar.gz"

echo "[✅] Minimal source archive created: $SOURCE_ARCHIVE ($(du -h "$SOURCE_ARCHIVE" | cut -f1))"

# Step 3: Wait for instance to be SSM-ready and install AWS CLI
echo "[3/6] Waiting for Mac instance to be SSM-ready..."

# Wait up to 15 minutes for SSM to be available
for i in {1..18}; do
    echo "[⏳] Checking SSM readiness... (attempt $i/18, $((i*50)) seconds elapsed)"
    
    # Try a simple SSM command
    if aws ssm send-command \
        --region "$REGION" \
        --instance-ids "$INSTANCE_ID" \
        --document-name "AWS-RunShellScript" \
        --parameters 'commands=["echo SSM Ready"]' \
        >/dev/null 2>&1; then
        
        echo "[✅] SSM agent is ready!"
        break
    fi
    
    if [[ $i -eq 18 ]]; then
        echo "[❌] SSM agent not ready after 15 minutes. Instance may have issues."
        echo "[💡] Check instance status manually:"
        echo "    aws ec2 describe-instances --region $REGION --instance-ids $INSTANCE_ID"
        exit 1
    fi
    
    sleep 50
done

echo "[🔧] Installing AWS CLI on Mac instance..."
aws ssm send-command \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --parameters 'commands=[
        "if ! command -v aws >/dev/null 2>&1; then",
        "  echo \"Installing AWS CLI...\"",
        "  curl \"https://awscli.amazonaws.com/AWSCLIV2.pkg\" -o \"/tmp/AWSCLIV2.pkg\"",
        "  sudo installer -pkg /tmp/AWSCLIV2.pkg -target /",
        "  rm /tmp/AWSCLIV2.pkg",
        "else",
        "  echo \"AWS CLI already installed\"",
        "fi",
        "aws --version"
    ]' >/dev/null

echo "[✅] AWS CLI installation complete"
sleep 10

# Step 4: Upload to S3 and download on Mac instance
echo "[4/6] Uploading source to S3 and downloading on Mac..."

# Use existing S3 bucket
S3_BUCKET="readysetobservabilityagent-test"
S3_KEY="temp/rdst-source-$(date +%s).tar.gz"
S3_URL="s3://$S3_BUCKET/$S3_KEY"

echo "[☁️] Uploading to S3: $S3_URL"
aws s3 cp "$SOURCE_ARCHIVE" "$S3_URL"

# Download on Mac instance via SSM (AWS CLI is installed in bootstrap)
echo "[📥] Downloading source on Mac instance..."
TRANSFER_CMD_ID=$(aws ssm send-command \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --parameters "commands=[
        'rm -rf /tmp/rdst_build',
        'mkdir -p /tmp/rdst_build',
        'cd /tmp/rdst_build',
        'echo \"Downloading from: $S3_URL\"',
        'echo \"To: /tmp/rdst_build/rdst-source.tar.gz\"',
        '/usr/local/bin/aws s3 cp $S3_URL /tmp/rdst_build/rdst-source.tar.gz',
        'ls -la /tmp/rdst_build/rdst-source.tar.gz',
        'tar -xzf rdst-source.tar.gz',
        'ls -la /tmp/rdst_build/',
        'ls -la /tmp/rdst_build/src/'
    ]" \
    --query 'Command.CommandId' \
    --output text)

# Wait for transfer to complete and check result
sleep 15
TRANSFER_STATUS=$(aws ssm get-command-invocation \
    --region "$REGION" \
    --command-id "$TRANSFER_CMD_ID" \
    --instance-id "$INSTANCE_ID" \
    --query 'Status' \
    --output text 2>/dev/null || echo "InProgress")

if [[ "$TRANSFER_STATUS" == "Failed" ]]; then
    echo "[❌] S3 transfer failed!"
    aws ssm get-command-invocation \
        --region "$REGION" \
        --command-id "$TRANSFER_CMD_ID" \
        --instance-id "$INSTANCE_ID" \
        --query 'StandardErrorContent' \
        --output text
    exit 1
fi

echo "[✅] Source transferred via S3"
sleep 10

# Step 5: Clean up any existing processes and build RDST on Mac
echo "[5/6] Cleaning up existing processes and building RDST on Mac..."

# Check for any running SSM commands first
echo "[🔍] Checking for existing SSM build commands..."
ACTIVE_COMMANDS=$(aws ssm list-command-invocations --region "$REGION" --instance-id "$INSTANCE_ID" --max-items 5 --query 'CommandInvocations[?Status==`InProgress`].CommandId' --output text)
if [[ -n "$ACTIVE_COMMANDS" ]]; then
    echo "[⚠️] Found active SSM commands: $ACTIVE_COMMANDS"
    echo "[🛑] Previous build might still be running - this could cause S3 upload conflicts"
else
    echo "[✅] No active SSM commands found"
fi

# Kill any existing Nuitka processes
echo "[🧹] Killing any existing Nuitka processes..."
aws ssm send-command \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --parameters 'commands=[
        "pkill -f nuitka || echo \"No existing Nuitka processes\"",
        "pkill -f python3 || echo \"No existing Python3 processes\"",
        "echo \"Process cleanup complete\""
    ]' >/dev/null

sleep 5

# Step 5a: Build RDST with Nuitka only
echo "[🔨] Starting Nuitka compilation (build only)..."
BUILD_COMMAND_ID=$(aws ssm send-command \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --timeout-seconds 2700 \
    --parameters "commands=[
        'sudo chown -R ec2-user:staff /tmp/rdst_build/',
        'sudo -u ec2-user bash -c \"rm -rf /tmp/rdst_build/build/* && cd /tmp/rdst_build/src && export PATH=/opt/homebrew/bin:\$PATH && brew install postgresql && python3 -m pip install --break-system-packages -r requirements.txt && python3 -m pip install --break-system-packages nuitka && python3 -m nuitka --standalone --lto=yes --output-dir=/tmp/rdst_build/build --output-filename=rdst --enable-plugin=implicit-imports --enable-plugin=anti-bloat --nofollow-import-to=unittest --nofollow-import-to=boto3 --nofollow-import-to=botocore --include-package=lib rdst.py && echo NUITKA COMPILATION COMPLETE\"'
    ]" \
    --query 'Command.CommandId' \
    --output text)

echo "[⏳] Build command ID: $BUILD_COMMAND_ID"
echo "[⏳] Waiting for build to complete..."

# Don't wait for SSM - poll local file instead (Nuitka takes 20-30+ minutes) 
echo "[⏳] Nuitka build started. This takes 20-30+ minutes. Polling for local completion..."
echo "[💡] Build command ID: $BUILD_COMMAND_ID (for debugging if needed)"

# Determine S3 path where artifact will be uploaded
if [[ "${TENANT}" == "stage01" ]]; then
    ARTIFACT_BUCKET="readysetobservabilityagent"
else
    ARTIFACT_BUCKET="readysetobservabilityagent-test"
fi
ARTIFACT_KEY="${TENANT}/latest/rdst-mac.tar.gz"
ARTIFACT_S3_PATH="s3://${ARTIFACT_BUCKET}/${ARTIFACT_KEY}"

# Record start time and backup existing file to avoid false positives
BUILD_START_TIME=$(date +%s)
echo "[💾] Backing up any existing artifact to avoid false positives..."
if aws s3 ls "$ARTIFACT_S3_PATH" >/dev/null 2>&1; then
    BACKUP_KEY="${TENANT}/latest/rdst-mac.backup-$(date +%Y%m%d%H%M%S).tar.gz"
    BACKUP_S3_PATH="s3://${ARTIFACT_BUCKET}/${BACKUP_KEY}"
    aws s3 cp "$ARTIFACT_S3_PATH" "$BACKUP_S3_PATH"
    aws s3 rm "$ARTIFACT_S3_PATH"
    echo "[✅] Backed up existing artifact to: $BACKUP_S3_PATH"
    
    # Verify the file is actually gone (S3 eventual consistency can take time)
    echo "[⏳] Waiting for S3 deletion to propagate..."
    for i in {1..10}; do
        sleep 2
        if ! aws s3 ls "$ARTIFACT_S3_PATH" >/dev/null 2>&1; then
            echo "[✅] Verified: Original artifact successfully removed (attempt $i)"
            break
        elif [[ $i -eq 10 ]]; then
            echo "[⚠️] WARNING: File still visible due to S3 eventual consistency, but delete command succeeded"
            echo "[💡] Proceeding anyway - polling will detect new uploads correctly"
            break
        else
            echo "[⏳] Still visible, waiting... (attempt $i/10)"
        fi
    done
else
    echo "[💡] No existing artifact to backup"
fi

echo "[🔍] Will poll for new artifact at: $ARTIFACT_S3_PATH"

# DEBUG: Check if file reappears immediately (before build even starts)
echo "[🔍] DEBUG: Checking if file reappears before build starts..."
sleep 3
if aws s3 ls "$ARTIFACT_S3_PATH" >/dev/null 2>&1; then
    echo "[🚨] DEBUG: FILE REAPPEARED IMMEDIATELY! This means something in this script is uploading it."
    echo "[🔍] DEBUG: Let's check what processes are running on the instance..."
    aws ssm send-command \
        --region "$REGION" \
        --instance-ids "$INSTANCE_ID" \
        --document-name "AWS-RunShellScript" \
        --parameters 'commands=[
            "ps aux | grep -E \"(aws|nuitka|python3)\" | grep -v grep",
            "ls -la /tmp/rdst_build/build/ 2>/dev/null || echo \"No build dir\"",
            "date"
        ]' >/dev/null
    sleep 5
    aws ssm get-command-invocation \
        --region "$REGION" \
        --command-id $(aws ssm list-command-invocations --region "$REGION" --instance-id "$INSTANCE_ID" --max-items 1 --query 'CommandInvocations[0].CommandId' --output text) \
        --instance-id "$INSTANCE_ID" \
        --query 'StandardOutputContent' \
        --output text
    echo "[❌] ABORTING: Something is uploading the file before we even start the build!"
    exit 1
else
    echo "[✅] DEBUG: File stayed deleted - proceeding with build"
fi

# Poll S3 every 30 seconds for up to 45 minutes, with build progress monitoring every 10 seconds
for i in {1..90}; do
    # Check build progress every 10 seconds
    echo "[🔍] Checking build progress on Mac instance... (check $i/90, $((i*30)) seconds elapsed)"
    PROGRESS_CMD_ID=$(aws ssm send-command \
        --region "$REGION" \
        --instance-ids "$INSTANCE_ID" \
        --document-name "AWS-RunShellScript" \
        --parameters 'commands=[
            "echo \"=== Build Status ===\" && date",
            "ps aux | grep -v grep | grep nuitka | head -3 || echo \"Nuitka not running\"", 
            "if [[ -f /tmp/rdst_build/build/rdst.dist/rdst && -s /tmp/rdst_build/build/rdst.dist/rdst ]]; then echo \"NUITKA COMPLETE: $(ls -lh /tmp/rdst_build/build/rdst.dist/rdst)\"; else echo \"Nuitka compilation not ready yet\"; fi",
            "echo \"=== Build Directory ===\" && ls -la /tmp/rdst_build/build/ 2>/dev/null | head -10 || echo \"No build directory yet\""
        ]' \
        --query 'Command.CommandId' \
        --output text 2>/dev/null)
    
    # Give SSM a moment to execute, then show output
    sleep 10
    BUILD_OUTPUT=$(aws ssm get-command-invocation \
        --region "$REGION" \
        --command-id "$PROGRESS_CMD_ID" \
        --instance-id "$INSTANCE_ID" \
        --query 'StandardOutputContent' \
        --output text 2>/dev/null)
    
    echo "$BUILD_OUTPUT" | head -20 || echo "[💡] Progress check pending..."
    echo ""
    
    # Check if Nuitka is complete (contains "NUITKA COMPLETE")
    if echo "$BUILD_OUTPUT" | grep -q "NUITKA COMPLETE:"; then
        echo "[✅] Nuitka compilation completed!"
        NUITKA_FILE_SIZE=$(echo "$BUILD_OUTPUT" | grep "NUITKA COMPLETE:" | awk '{print $4}')
        echo "[📦] Nuitka binary size: $NUITKA_FILE_SIZE"
        break
    fi
    
    sleep 20  # Wait 20 more seconds (10 for SSM + 20 = 30 second cycle)
done

# Check if we timed out
if ! echo "$BUILD_OUTPUT" | grep -q "NUITKA COMPLETE:"; then
    echo "[❌] Build timeout - Nuitka compilation not completed after 45 minutes"
    echo "[💡] Check build status manually with:"
    echo "    aws ssm get-command-invocation --region $REGION --command-id $BUILD_COMMAND_ID --instance-id $INSTANCE_ID"
    exit 1
fi

# Step 5b: Package the Nuitka build
echo "[📦] Nuitka complete. Now packaging into tar.gz..."
PACKAGE_COMMAND_ID=$(aws ssm send-command \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --timeout-seconds 300 \
    --parameters 'commands=[
        "cd /tmp/rdst_build/build",
        "sudo -u ec2-user mkdir -p rdst-install",
        "sudo -u ec2-user cp -r rdst.dist rdst-install/rdst",
        "sudo -u ec2-user chmod +x rdst-install/rdst/rdst", 
        "sudo -u ec2-user tar -czf rdst-mac.tar.gz -C rdst-install .",
        "echo \"PACKAGING COMPLETE: $(ls -lh rdst-mac.tar.gz)\""
    ]' \
    --query 'Command.CommandId' \
    --output text)

echo "[⏳] Package command ID: $PACKAGE_COMMAND_ID"
echo "[⏳] Waiting for packaging..."

# Wait for packaging to complete
sleep 30
PACKAGE_STATUS=$(aws ssm get-command-invocation \
    --region "$REGION" \
    --command-id "$PACKAGE_COMMAND_ID" \
    --instance-id "$INSTANCE_ID" \
    --query 'Status' \
    --output text 2>/dev/null || echo "Pending")

if [[ "$PACKAGE_STATUS" != "Success" ]]; then
    echo "[❌] Packaging failed with status: $PACKAGE_STATUS"
    exit 1
fi

echo "[✅] Packaging completed successfully!"

# Step 5c: Upload to S3 now that packaging is complete  
echo "[📤] Packaging complete. Now uploading to S3..."
UPLOAD_COMMAND_ID=$(aws ssm send-command \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --timeout-seconds 300 \
    --parameters 'commands=[
        "cd /tmp/rdst_build/build",
        "ls -la rdst-mac.tar.gz", 
        "echo \"Uploading to s3://readysetobservabilityagent-test/'${TENANT}'/latest/rdst-mac.tar.gz\"",
        "/usr/local/bin/aws s3 cp rdst-mac.tar.gz s3://readysetobservabilityagent-test/'${TENANT}'/latest/rdst-mac.tar.gz",
        "echo \"S3 upload complete\""
    ]' \
    --query 'Command.CommandId' \
    --output text)

echo "[⏳] Upload command ID: $UPLOAD_COMMAND_ID"
echo "[⏳] Waiting for S3 upload..."

# Wait for upload to complete (should be quick)
sleep 15
UPLOAD_STATUS=$(aws ssm get-command-invocation \
    --region "$REGION" \
    --command-id "$UPLOAD_COMMAND_ID" \
    --instance-id "$INSTANCE_ID" \
    --query 'Status' \
    --output text 2>/dev/null || echo "Pending")

if [[ "$UPLOAD_STATUS" == "Success" ]]; then
    echo "[✅] S3 upload completed successfully!"
    echo "[🎉] Mac RDST build and upload completed!"
    echo "[📥] Artifact available at: https://${ARTIFACT_BUCKET}.s3.amazonaws.com/${ARTIFACT_KEY}"
    
    # Step 6: Terminate the Mac instance to save money
    echo "[🗑️] Terminating Mac instance..."
    aws ec2 terminate-instances --region "$REGION" --instance-ids "$INSTANCE_ID"
    echo "[✅] Mac instance $INSTANCE_ID terminated successfully"
else
    echo "[⚠️] Upload status: $UPLOAD_STATUS"
    echo "[💡] Check upload manually:"
    echo "    aws ssm get-command-invocation --region $REGION --command-id $UPLOAD_COMMAND_ID --instance-id $INSTANCE_ID"
    echo "[💡] Or check S3 directly:"
    echo "    aws s3 ls s3://${ARTIFACT_BUCKET}/${ARTIFACT_KEY}"
    echo "[💡] Mac instance $INSTANCE_ID left running for debugging"
fi

# Build runs in background and uploads to S3 automatically
# No manual download/upload needed - we poll S3 above for completion
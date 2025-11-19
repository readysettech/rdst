#!/usr/bin/env bash
# Cleanup script to find and terminate Mac build instances
# Usage: cleanup_mac_instance.sh [tenant]
# This should be called by Buildkite on timeout or failure to prevent runaway costs

set -Eeuo pipefail

TENANT="${1:-}"
REGION="us-east-2"

echo "[🧹] Starting Mac instance cleanup for tenant: ${TENANT:-all}"

# Find running Mac build instances
echo "[🔍] Searching for running Mac build instances..."

if [[ -n "$TENANT" ]]; then
    # Search for specific tenant
    INSTANCE_IDS=$(aws ec2 describe-instances \
        --region "$REGION" \
        --filters \
            "Name=instance-state-name,Values=running,pending" \
            "Name=tag:Purpose,Values=RDST-Build" \
            "Name=tag:Tenant,Values=$TENANT" \
            "Name=instance-type,Values=mac2.metal" \
        --query 'Reservations[].Instances[].InstanceId' \
        --output text 2>/dev/null || echo "")
else
    # Search for all RDST build instances
    INSTANCE_IDS=$(aws ec2 describe-instances \
        --region "$REGION" \
        --filters \
            "Name=instance-state-name,Values=running,pending" \
            "Name=tag:Purpose,Values=RDST-Build" \
            "Name=instance-type,Values=mac2.metal" \
        --query 'Reservations[].Instances[].InstanceId' \
        --output text 2>/dev/null || echo "")
fi

if [[ -z "$INSTANCE_IDS" || "$INSTANCE_IDS" == "None" ]]; then
    echo "[✅] No running Mac build instances found"
    exit 0
fi

echo "[🎯] Found Mac build instances: $INSTANCE_IDS"

# Get details about each instance
for INSTANCE_ID in $INSTANCE_IDS; do
    echo "[📋] Checking instance: $INSTANCE_ID"
    
    # Get instance details
    INSTANCE_INFO=$(aws ec2 describe-instances \
        --region "$REGION" \
        --instance-ids "$INSTANCE_ID" \
        --query 'Reservations[0].Instances[0].{LaunchTime:LaunchTime,State:State.Name,Tenant:Tags[?Key==`Tenant`]|[0].Value}' \
        --output json 2>/dev/null || echo "{}")
    
    if [[ "$INSTANCE_INFO" != "{}" ]]; then
        LAUNCH_TIME=$(echo "$INSTANCE_INFO" | jq -r '.LaunchTime // "unknown"')
        STATE=$(echo "$INSTANCE_INFO" | jq -r '.State // "unknown"')
        INSTANCE_TENANT=$(echo "$INSTANCE_INFO" | jq -r '.Tenant // "unknown"')
        
        echo "[📊] Instance $INSTANCE_ID:"
        echo "    State: $STATE"
        echo "    Tenant: $INSTANCE_TENANT" 
        echo "    Launch Time: $LAUNCH_TIME"
        
        # Calculate age if launch time is available
        if [[ "$LAUNCH_TIME" != "unknown" && "$LAUNCH_TIME" != "null" ]]; then
            LAUNCH_EPOCH=$(date -d "$LAUNCH_TIME" +%s 2>/dev/null || echo "0")
            CURRENT_EPOCH=$(date +%s)
            AGE_MINUTES=$(( (CURRENT_EPOCH - LAUNCH_EPOCH) / 60 ))
            echo "    Age: ${AGE_MINUTES} minutes"
            
            # Warn if instance is very old (over 2 hours)
            if [[ $AGE_MINUTES -gt 120 ]]; then
                echo "    ⚠️  WARNING: Instance is over 2 hours old!"
            fi
        fi
    fi
done

# Confirm termination (skip in Buildkite)
if [[ -z "${BUILDKITE:-}" ]]; then
    echo ""
    echo "💰 WARNING: Each Mac instance costs \$25.60 for 24 hours minimum!"
    echo "🤔 Do you want to terminate these instances? (y/N)"
    read -r CONFIRM
    if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
        echo "[❌] Termination cancelled"
        exit 0
    fi
fi

# Terminate instances
echo "[🗑️] Terminating Mac build instances..."
for INSTANCE_ID in $INSTANCE_IDS; do
    echo "[🗑️] Terminating instance: $INSTANCE_ID"
    
    if aws ec2 terminate-instances --region "$REGION" --instance-ids "$INSTANCE_ID" >/dev/null 2>&1; then
        echo "[✅] Successfully initiated termination of $INSTANCE_ID"
    else
        echo "[❌] Failed to terminate $INSTANCE_ID"
    fi
done

# Intelligent host cleanup - only release hosts nearing end of billing cycle
echo ""
echo "[🔍] Checking for dedicated hosts to release..."

# Find all mac2.metal hosts tagged for RDST (both available and released state)
# Tag filter ensures we only touch hosts managed by RDST
ALL_HOSTS=$(aws ec2 describe-hosts \
    --region "$REGION" \
    --filter \
        "Name=instance-type,Values=mac2.metal" \
        "Name=tag:Purpose,Values=RDST-Build" \
    --query 'Hosts[].{HostId:HostId,AllocationTime:AllocationTime,State:State}' \
    --output json 2>/dev/null || echo "[]")

if [[ "$ALL_HOSTS" == "[]" || -z "$ALL_HOSTS" ]]; then
    echo "[✅] No dedicated hosts found"
else
    echo "$ALL_HOSTS" | jq -c '.[]' | while read -r host; do
        HOST_ID=$(echo "$host" | jq -r '.HostId')
        ALLOC_TIME=$(echo "$host" | jq -r '.AllocationTime')
        STATE=$(echo "$host" | jq -r '.State')

        # Only process available hosts (instance terminated, but host still allocated)
        if [[ "$STATE" != "available" ]]; then
            echo "[⏭️] Host $HOST_ID is in state '$STATE', skipping"
            continue
        fi

        # Calculate host age in hours
        if [[ "$ALLOC_TIME" != "null" && -n "$ALLOC_TIME" ]]; then
            ALLOC_EPOCH=$(date -d "$ALLOC_TIME" +%s 2>/dev/null || echo "0")
            CURRENT_EPOCH=$(date +%s)
            AGE_HOURS=$(( (CURRENT_EPOCH - ALLOC_EPOCH) / 3600 ))

            echo "[📊] Host $HOST_ID: ${AGE_HOURS}h old (state: $STATE)"

            # Only release if >= 23 hours (near end of 24h billing cycle)
            # This allows reuse for retry attempts within the billing window
            if [[ $AGE_HOURS -ge 23 ]]; then
                echo "[💰] Releasing host $HOST_ID (approaching 24h billing cycle)"
                if aws ec2 release-hosts --region "$REGION" --host-ids "$HOST_ID" >/dev/null 2>&1; then
                    echo "[✅] Successfully released host $HOST_ID"
                else
                    echo "[❌] Failed to release host $HOST_ID"
                fi
            else
                REMAINING_HOURS=$((24 - AGE_HOURS))
                echo "[♻️] Keeping host $HOST_ID for reuse (~${REMAINING_HOURS}h left in billing cycle)"
            fi
        fi
    done
fi

echo ""
echo "[✅] Cleanup complete!"
echo "[💰] Instances will stop accruing charges once termination completes"
echo "[ℹ️] Hosts <23h old are kept for potential retry attempts within billing window"

# Show any remaining instances
echo "[🔍] Checking for remaining instances..."
REMAINING=$(aws ec2 describe-instances \
    --region "$REGION" \
    --filters \
        "Name=instance-state-name,Values=running,pending" \
        "Name=tag:Purpose,Values=RDST-Build" \
        "Name=instance-type,Values=mac2.metal" \
    --query 'length(Reservations[].Instances[])' \
    --output text 2>/dev/null || echo "0")

if [[ "$REMAINING" == "0" ]]; then
    echo "[✅] All Mac build instances cleaned up!"
else
    echo "[⚠️] $REMAINING Mac build instances still exist"
fi
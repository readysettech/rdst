#!/usr/bin/env bash
# Age-based cleanup for Mac dedicated hosts
#
# PURPOSE: Prevent cost leaks by releasing hosts older than 2 days
# USAGE: Run daily via cron/scheduled task in production account
#
# Example cron entry (runs daily at 2 AM):
#   0 2 * * * /path/to/cleanup_old_mac_hosts.sh >> /var/log/mac_host_cleanup.log 2>&1

set -Eeuo pipefail

REGION="us-east-2"
MAX_AGE_DAYS=2

echo "=================================================="
echo "Mac Dedicated Host Age-Based Cleanup"
echo "$(date)"
echo "=================================================="
echo ""
echo "[🧹] Checking for Mac dedicated hosts older than ${MAX_AGE_DAYS} days..."
echo "[📍] Region: $REGION"
echo ""

# Find all mac2.metal dedicated hosts tagged for RDST
# Tag filter ensures we only touch hosts managed by RDST, not other team's Mac hosts
HOSTS=$(aws ec2 describe-hosts \
    --region "$REGION" \
    --filter \
        "Name=instance-type,Values=mac2.metal" \
        "Name=tag:Purpose,Values=RDST-Build" \
    --query 'Hosts[].{HostId:HostId,AllocationTime:AllocationTime,State:State,AvailableCapacity:AvailableCapacity.AvailableInstanceCapacity[0].AvailableCapacity}' \
    --output json 2>/dev/null || echo "[]")

if [[ "$HOSTS" == "[]" || -z "$HOSTS" ]]; then
    echo "[✅] No Mac dedicated hosts found in $REGION"
    exit 0
fi

TOTAL_HOSTS=$(echo "$HOSTS" | jq 'length')
echo "[📊] Found $TOTAL_HOSTS Mac dedicated host(s)"
echo ""

RELEASED_COUNT=0
KEPT_COUNT=0
ERROR_COUNT=0

echo "$HOSTS" | jq -c '.[]' | while read -r host; do
    HOST_ID=$(echo "$host" | jq -r '.HostId')
    ALLOC_TIME=$(echo "$host" | jq -r '.AllocationTime')
    STATE=$(echo "$host" | jq -r '.State')
    AVAILABLE_CAP=$(echo "$host" | jq -r '.AvailableCapacity // 0')

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "[🖥️] Host: $HOST_ID"
    echo "   State: $STATE"
    echo "   Available Capacity: $AVAILABLE_CAP"
    echo "   Allocated: $ALLOC_TIME"

    # Skip if already released
    if [[ "$STATE" == "released" || "$STATE" == "pending" ]]; then
        echo "   ⏭️  Skipping (state: $STATE)"
        echo ""
        continue
    fi

    # Calculate age
    if [[ "$ALLOC_TIME" != "null" && -n "$ALLOC_TIME" ]]; then
        ALLOC_EPOCH=$(date -d "$ALLOC_TIME" +%s 2>/dev/null || echo "0")
        CURRENT_EPOCH=$(date +%s)
        AGE_DAYS=$(( (CURRENT_EPOCH - ALLOC_EPOCH) / 86400 ))
        AGE_HOURS=$(( (CURRENT_EPOCH - ALLOC_EPOCH) / 3600 ))

        echo "   Age: ${AGE_DAYS} days (${AGE_HOURS} hours)"

        # Release if older than MAX_AGE_DAYS
        if [[ $AGE_DAYS -ge $MAX_AGE_DAYS ]]; then
            echo "   ⚠️  WARNING: Host is ${AGE_DAYS} days old (threshold: ${MAX_AGE_DAYS} days)"
            echo "   💰 Releasing host to prevent cost leak..."

            if aws ec2 release-hosts --region "$REGION" --host-ids "$HOST_ID" >/dev/null 2>&1; then
                echo "   ✅ Successfully released host $HOST_ID"
                RELEASED_COUNT=$((RELEASED_COUNT + 1))
            else
                echo "   ❌ FAILED to release host $HOST_ID"
                ERROR_COUNT=$((ERROR_COUNT + 1))
            fi
        else
            REMAINING_DAYS=$((MAX_AGE_DAYS - AGE_DAYS))
            echo "   ✅ Host is within retention policy (${REMAINING_DAYS} day(s) until cleanup)"
            KEPT_COUNT=$((KEPT_COUNT + 1))
        fi
    else
        echo "   ❌ Unable to determine age (allocation time unavailable)"
        ERROR_COUNT=$((ERROR_COUNT + 1))
    fi

    echo ""
done

echo "=================================================="
echo "Cleanup Summary"
echo "=================================================="
echo "Total Hosts Found: $TOTAL_HOSTS"
echo "Hosts Released:    $RELEASED_COUNT"
echo "Hosts Kept:        $KEPT_COUNT"
echo "Errors:            $ERROR_COUNT"
echo ""

if [[ $RELEASED_COUNT -gt 0 ]]; then
    ESTIMATED_SAVINGS=$(echo "$RELEASED_COUNT * 15.60" | bc)
    echo "💰 Estimated daily savings: \$${ESTIMATED_SAVINGS}"
fi

echo "=================================================="
echo "Completed at $(date)"
echo "=================================================="

# Exit with error code if any releases failed
if [[ $ERROR_COUNT -gt 0 ]]; then
    exit 1
fi

exit 0

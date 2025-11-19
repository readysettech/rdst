#!/usr/bin/env bash
# Manage Mac EC2 instance for RDST builds
# Usage: build_mac_instance.sh <tenant>

set -Eeu
# Don't use -o pipefail here because we want to capture all output

# Log everything to stderr, return only instance ID to stdout
exec 2>&1

TENANT="${1:-dev01}"
REGION="us-east-2"

# Instance configuration - Mac instances require specific instance types
INSTANCE_TYPE="mac2.metal"  # Apple M1 Mac instances - cheapest option with universal compatibility
IAM_INSTANCE_PROFILE="EC2InstanceS3Access"


# Use hardcoded AMI for reliability (latest ARM64 macOS as of Dec 2024)
if [[ "$INSTANCE_TYPE" == "mac2.metal"* ]]; then
    MAC_AMI_ID="ami-08afd3e7c72f507c2"  # amzn-ec2-macos-15.6.1-20250902-081507-arm64 (Sequoia)
    echo "[🍎] Using hardcoded ARM64 macOS AMI: $MAC_AMI_ID"
else
    MAC_AMI_ID="ami-00ec7619e3f3befd7"  # amzn-ec2-macos-12.7.4-20240411-184458 (x86_64)
    echo "[🍎] Using hardcoded x86_64 macOS AMI: $MAC_AMI_ID"
fi

if [[ "$MAC_AMI_ID" == "None" || -z "$MAC_AMI_ID" ]]; then
    echo "❌ No macOS AMI found in region ${REGION}"
    exit 1
fi

echo "[🍎] Using macOS AMI: $MAC_AMI_ID"

# Check if we already have a running Mac instance to reuse
echo "[💰] Checking for existing Mac build instances..."
EXISTING_INSTANCE=$(aws ec2 describe-instances \
    --region "$REGION" \
    --filters \
        "Name=tag:Purpose,Values=RDST-Build" \
        "Name=tag:Tenant,Values=${TENANT}" \
        "Name=instance-type,Values=${INSTANCE_TYPE}" \
        "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].InstanceId' \
    --output text 2>/dev/null || echo "None")

if [[ "$EXISTING_INSTANCE" != "None" && -n "$EXISTING_INSTANCE" ]]; then
    echo "[♻️] Found existing Mac instance: $EXISTING_INSTANCE"
    echo "[💡] Reusing to avoid additional charge"
    echo "$EXISTING_INSTANCE"
    exit 0
fi

echo "[🏗️] No existing instance found. Creating new one (will cost $15.60 for 24h minimum)..."

# Step 1: Create or find VPC infrastructure
echo "[🌐] Setting up VPC infrastructure..."

# Check if VPC with mac-rdst tag exists
VPC_ID=$(aws ec2 describe-vpcs \
    --region "$REGION" \
    --filters "Name=tag:Name,Values=mac-rdst-vpc" "Name=state,Values=available" \
    --query 'Vpcs[0].VpcId' \
    --output text 2>/dev/null || echo "None")

if [[ "$VPC_ID" == "None" || -z "$VPC_ID" ]]; then
    echo "[🏗️] Creating new VPC for Mac builds..."
    
    # Create VPC
    VPC_ID=$(aws ec2 create-vpc \
        --region "$REGION" \
        --cidr-block "10.0.0.0/16" \
        --query 'Vpc.VpcId' \
        --output text)
    
    # Tag the VPC
    aws ec2 create-tags \
        --region "$REGION" \
        --resources "$VPC_ID" \
        --tags "Key=Name,Value=mac-rdst-vpc" "Key=Purpose,Value=RDST-Mac-Builds"
    
    # Enable DNS hostnames
    aws ec2 modify-vpc-attribute \
        --region "$REGION" \
        --vpc-id "$VPC_ID" \
        --enable-dns-hostnames
    
    echo "[✅] Created VPC: $VPC_ID"
else
    echo "[✅] Using existing VPC: $VPC_ID"
fi

# Hardcode to use us-east-2b subnet for the allocated Dedicated Host
SUBNET_ID=$(aws ec2 describe-subnets \
    --region "$REGION" \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=availability-zone,Values=us-east-2b" "Name=tag:Name,Values=mac-rdst-subnet-2b" \
    --query 'Subnets[0].SubnetId' \
    --output text 2>/dev/null || echo "None")

if [[ "$SUBNET_ID" == "None" || -z "$SUBNET_ID" ]]; then
    echo "[🏗️] Creating subnet..."
    
    # Create subnet in us-east-2b to match the Dedicated Host
    echo "[🏗️] Creating subnet in us-east-2b for Dedicated Host..."
    
    SUBNET_ID=$(aws ec2 create-subnet \
        --region "$REGION" \
        --vpc-id "$VPC_ID" \
        --cidr-block "10.0.2.0/24" \
        --availability-zone "us-east-2b" \
        --query 'Subnet.SubnetId' \
        --output text)
    
    # Tag the subnet
    aws ec2 create-tags \
        --region "$REGION" \
        --resources "$SUBNET_ID" \
        --tags "Key=Name,Value=mac-rdst-subnet-2b" "Key=Purpose,Value=RDST-Mac-Builds"
    
    # Enable auto-assign public IP
    aws ec2 modify-subnet-attribute \
        --region "$REGION" \
        --subnet-id "$SUBNET_ID" \
        --map-public-ip-on-launch
    
    # Associate with the existing route table for internet access
    echo "[🔗] Associating subnet with route table for internet access..."
    aws ec2 associate-route-table \
        --region "$REGION" \
        --subnet-id "$SUBNET_ID" \
        --route-table-id "$ROUTE_TABLE_ID"
    
    echo "[✅] Route table associated with new subnet"
    
    echo "[✅] Created subnet: $SUBNET_ID"
else
    echo "[✅] Using existing subnet: $SUBNET_ID"
fi

# Check if Internet Gateway exists
IGW_ID=$(aws ec2 describe-internet-gateways \
    --region "$REGION" \
    --filters "Name=attachment.vpc-id,Values=$VPC_ID" \
    --query 'InternetGateways[0].InternetGatewayId' \
    --output text 2>/dev/null || echo "None")

if [[ "$IGW_ID" == "None" || -z "$IGW_ID" ]]; then
    echo "[🏗️] Creating Internet Gateway..."
    
    # Create Internet Gateway
    IGW_ID=$(aws ec2 create-internet-gateway \
        --region "$REGION" \
        --query 'InternetGateway.InternetGatewayId' \
        --output text)
    
    # Tag the IGW
    aws ec2 create-tags \
        --region "$REGION" \
        --resources "$IGW_ID" \
        --tags "Key=Name,Value=mac-rdst-igw" "Key=Purpose,Value=RDST-Mac-Builds"
    
    # Attach to VPC
    aws ec2 attach-internet-gateway \
        --region "$REGION" \
        --internet-gateway-id "$IGW_ID" \
        --vpc-id "$VPC_ID"
    
    echo "[✅] Created and attached IGW: $IGW_ID"
else
    echo "[✅] Using existing IGW: $IGW_ID"
fi

# Check if route table is configured
ROUTE_TABLE_ID=$(aws ec2 describe-route-tables \
    --region "$REGION" \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Name,Values=mac-rdst-rt" \
    --query 'RouteTables[0].RouteTableId' \
    --output text 2>/dev/null || echo "None")

if [[ "$ROUTE_TABLE_ID" == "None" || -z "$ROUTE_TABLE_ID" ]]; then
    echo "[🏗️] Creating route table..."
    
    # Create route table
    ROUTE_TABLE_ID=$(aws ec2 create-route-table \
        --region "$REGION" \
        --vpc-id "$VPC_ID" \
        --query 'RouteTable.RouteTableId' \
        --output text)
    
    # Tag the route table
    aws ec2 create-tags \
        --region "$REGION" \
        --resources "$ROUTE_TABLE_ID" \
        --tags "Key=Name,Value=mac-rdst-rt" "Key=Purpose,Value=RDST-Mac-Builds"
    
    # Add route to Internet Gateway
    aws ec2 create-route \
        --region "$REGION" \
        --route-table-id "$ROUTE_TABLE_ID" \
        --destination-cidr-block "0.0.0.0/0" \
        --gateway-id "$IGW_ID"
    
    # Associate with subnet
    aws ec2 associate-route-table \
        --region "$REGION" \
        --subnet-id "$SUBNET_ID" \
        --route-table-id "$ROUTE_TABLE_ID"
    
    echo "[✅] Created and configured route table: $ROUTE_TABLE_ID"
else
    echo "[✅] Using existing route table: $ROUTE_TABLE_ID"
fi

# Check if security group exists
SG_ID=$(aws ec2 describe-security-groups \
    --region "$REGION" \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=mac-rdst-sg" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null || echo "None")

if [[ "$SG_ID" == "None" || -z "$SG_ID" ]]; then
    echo "[🏗️] Creating security group..."
    
    # Create security group
    SG_ID=$(aws ec2 create-security-group \
        --region "$REGION" \
        --group-name "mac-rdst-sg" \
        --description "Security group for RDST Mac builds" \
        --vpc-id "$VPC_ID" \
        --query 'GroupId' \
        --output text)
    
    # Tag the security group
    aws ec2 create-tags \
        --region "$REGION" \
        --resources "$SG_ID" \
        --tags "Key=Name,Value=mac-rdst-sg" "Key=Purpose,Value=RDST-Mac-Builds"
    
    # Add outbound rule for HTTPS (SSM needs this)
    aws ec2 authorize-security-group-egress \
        --region "$REGION" \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 443 \
        --cidr 0.0.0.0/0 2>/dev/null || true
    
    # Add outbound rule for HTTP (for downloads)
    aws ec2 authorize-security-group-egress \
        --region "$REGION" \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 80 \
        --cidr 0.0.0.0/0 2>/dev/null || true
    
    echo "[✅] Created security group: $SG_ID"
else
    echo "[✅] Using existing security group: $SG_ID"
fi

echo "[🌐] VPC infrastructure ready!"

# Create User Data script that will bootstrap the build
USER_DATA=$(cat << 'EOF'
#!/bin/bash
exec > /var/log/rdst_build.log 2>&1

echo "🚀 Starting RDST Mac build bootstrap at $(date)"

# Install required tools
echo "📦 Installing build dependencies..."

# Install Homebrew if not present
if ! command -v brew >/dev/null 2>&1; then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# Add Homebrew to PATH
export PATH="/opt/homebrew/bin:$PATH"

# Install Python and pip - explicitly prefer 3.12 or 3.11 due to randbits issues in 3.13
echo "Installing Python (preferring 3.12/3.11, avoiding 3.13)..."
# Try to install specific versions in order of preference
if brew install python@3.12; then
    echo "✅ Installed Python 3.12"
    # Ensure python3.12 is available
    brew link python@3.12 --force || true
elif brew install python@3.11; then
    echo "✅ Installed Python 3.11" 
    # Ensure python3.11 is available
    brew link python@3.11 --force || true
else
    echo "⚠️ Fallback to default python3 - may have compatibility issues"
    brew install python3
fi

# Verify Python version and show warning if 3.13+
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "unknown")
echo "📋 Installed Python version: $PYTHON_VERSION"
if [[ "$PYTHON_VERSION" > "3.12" ]]; then
    echo "⚠️ WARNING: Python $PYTHON_VERSION may have numpy randbits compatibility issues"
fi

# Install Nuitka
echo "Installing Nuitka..."
python3 -m pip install nuitka

# Install AWS CLI for S3 access
echo "Installing AWS CLI..."
curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "AWSCLIV2.pkg"
sudo installer -pkg AWSCLIV2.pkg -target /

echo "✅ Build dependencies installed at $(date)"
echo "📋 Ready for build commands via SSM"

# Keep instance running and log that we're ready
echo "🎯 Instance ready for SSM commands"
EOF
)

# Check if Dedicated Hosts are available for Mac instances
echo "[🔍] Checking for available Mac Dedicated Hosts..."
AVAILABLE_HOSTS=$(aws ec2 describe-hosts \
    --region "$REGION" \
    --filter "Name=instance-type,Values=$INSTANCE_TYPE" "Name=state,Values=available" \
    --query 'length(Hosts)' \
    --output text 2>/dev/null || echo "0")

if [[ "$AVAILABLE_HOSTS" == "0" ]]; then
    echo "[🏗️] No available Dedicated Hosts found. Allocating new host for $INSTANCE_TYPE..."
    
    # Allocate a Dedicated Host with timeout - try multiple AZs
    echo "[⏳] Allocating Dedicated Host (this may take 2-3 minutes)..."
    HOST_ID=""
    for AZ in "${REGION}b" "${REGION}c" "${REGION}a"; do
        echo "[🔍] Trying availability zone: $AZ"
        set -x
        HOST_ID=$(timeout 300s aws ec2 allocate-hosts \
            --region "$REGION" \
            --instance-type "$INSTANCE_TYPE" \
            --availability-zone "$AZ" \
            --quantity 1 \
            --query 'HostIds[0]' \
            --output text 2>&1)
        EXIT_CODE=$?
        set +x
        
        if [[ $EXIT_CODE -eq 0 ]] && [[ ! "$HOST_ID" =~ "error" ]] && [[ -n "$HOST_ID" ]] && [[ "$HOST_ID" != "None" ]]; then
            echo "[✅] Successfully allocated host in $AZ: $HOST_ID"
            CHOSEN_AZ="$AZ"
            break
        else
            echo "[❌] Failed in $AZ: $HOST_ID"
            HOST_ID=""
        fi
        sleep 5
    done
    
    if [[ $EXIT_CODE -ne 0 ]] || [[ "$HOST_ID" =~ "error" ]] || [[ -z "$HOST_ID" ]] || [[ "$HOST_ID" == "None" ]]; then
        echo "❌ Failed to allocate Dedicated Host (exit code: $EXIT_CODE)"
        echo "Output: $HOST_ID"
        echo "💡 Possible issues:"
        echo "   1. No quota for Mac Dedicated Hosts (check Service Quotas)"
        echo "   2. Region doesn't support mac2 instances" 
        echo "   3. Temporary AWS service issue"
        echo "   4. Account doesn't have Mac instance permissions"
        exit 1
    fi
    
    echo "[✅] Allocated Dedicated Host: $HOST_ID"
    
    # Wait for host to be available - no built-in waiter, so poll manually
    echo "[⏳] Waiting for Dedicated Host to be ready (up to 5 minutes)..."
    for i in {1..30}; do
        HOST_STATE=$(aws ec2 describe-hosts \
            --region "$REGION" \
            --host-ids "$HOST_ID" \
            --query 'Hosts[0].State' \
            --output text 2>/dev/null || echo "pending")
        
        echo "[⏳] Host state: $HOST_STATE (check $i/30)"
        
        if [[ "$HOST_STATE" == "available" ]]; then
            echo "[✅] Dedicated Host is ready!"
            break
        elif [[ "$HOST_STATE" == "failed" ]]; then
            echo "[❌] Dedicated Host failed to allocate"
            exit 1
        fi
        
        sleep 10
    done
    
    if [[ "$HOST_STATE" != "available" ]]; then
        echo "[❌] Timeout waiting for Dedicated Host to be available"
        exit 1
    fi

    # Tag the newly allocated host for tracking and cleanup
    echo "[🏷️] Tagging Dedicated Host $HOST_ID..."
    if aws ec2 create-tags \
        --region "$REGION" \
        --resources "$HOST_ID" \
        --tags \
            "Key=Name,Value=RDST-Mac-Host-${TENANT}" \
            "Key=Purpose,Value=RDST-Build" \
            "Key=Tenant,Value=${TENANT}" \
            "Key=ManagedBy,Value=RDST" 2>/dev/null; then
        echo "[✅] Host tagged successfully"
    else
        echo "[⚠️] Warning: Failed to tag host (non-fatal, continuing)"
    fi
else
    # Find an existing available host and get its AZ
    HOST_INFO=$(aws ec2 describe-hosts \
        --region "$REGION" \
        --filter "Name=instance-type,Values=$INSTANCE_TYPE" "Name=state,Values=available" \
        --query 'Hosts[0].{HostId:HostId,AvailabilityZone:AvailabilityZone}' \
        --output json)
    
    HOST_ID=$(echo "$HOST_INFO" | jq -r '.HostId')
    CHOSEN_AZ=$(echo "$HOST_INFO" | jq -r '.AvailabilityZone')

    echo "[✅] Using existing Dedicated Host: $HOST_ID (AZ: $CHOSEN_AZ)"

    # Ensure existing host is tagged (idempotent operation)
    echo "[🏷️] Ensuring Dedicated Host $HOST_ID is properly tagged..."
    if aws ec2 create-tags \
        --region "$REGION" \
        --resources "$HOST_ID" \
        --tags \
            "Key=Name,Value=RDST-Mac-Host-${TENANT}" \
            "Key=Purpose,Value=RDST-Build" \
            "Key=Tenant,Value=${TENANT}" \
            "Key=ManagedBy,Value=RDST" 2>/dev/null; then
        echo "[✅] Host tags verified/updated"
    else
        echo "[⚠️] Warning: Failed to tag host (non-fatal, continuing)"
    fi
fi

# Launch EC2 instance on Dedicated Host
echo "[🚀] Launching Mac build instance on Dedicated Host $HOST_ID..."

set -x  # Show the actual command being run
LAUNCH_OUTPUT=$(timeout 300s aws ec2 run-instances \
    --region "$REGION" \
    --image-id "$MAC_AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --iam-instance-profile "Name=$IAM_INSTANCE_PROFILE" \
    --subnet-id "$SUBNET_ID" \
    --security-group-ids "$SG_ID" \
    --placement "Tenancy=host,HostId=$HOST_ID" \
    --user-data "$USER_DATA" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=RDST-Mac-Builder-${TENANT}},{Key=Purpose,Value=RDST-Build},{Key=Tenant,Value=${TENANT}}]" \
    --query 'Instances[0].InstanceId' \
    --output text 2>&1)
LAUNCH_EXIT_CODE=$?
set +x

# Extract instance ID if successful
if [[ $LAUNCH_EXIT_CODE -eq 0 && ! "$LAUNCH_OUTPUT" =~ "error" ]]; then
    INSTANCE_ID="$LAUNCH_OUTPUT"
else
    INSTANCE_ID="$LAUNCH_OUTPUT"  # Contains error message
fi

# Check if instance launch succeeded
if [[ $LAUNCH_EXIT_CODE -ne 0 ]] || [[ "$INSTANCE_ID" =~ "error" ]] || [[ -z "$INSTANCE_ID" ]] || [[ "$INSTANCE_ID" == "None" ]]; then
    
    # Check if it's insufficient capacity on existing host
    if [[ "$INSTANCE_ID" =~ "InsufficientCapacityOnHost" ]]; then
        echo "[⚠️] Existing Dedicated Host $HOST_ID has insufficient capacity"
        echo "[💰] Allocating new Dedicated Host (new 24h billing cycle: ~$25.60)"
        
        # Allocate a new Dedicated Host
        HOST_ID=""
        for AZ in "${REGION}b" "${REGION}c" "${REGION}a"; do
            echo "[🔍] Trying to allocate new host in AZ: $AZ"
            set -x
            HOST_ID=$(timeout 300s aws ec2 allocate-hosts \
                --region "$REGION" \
                --instance-type "$INSTANCE_TYPE" \
                --availability-zone "$AZ" \
                --quantity 1 \
                --query 'HostIds[0]' \
                --output text 2>&1)
            set +x
            
            if [[ $? -eq 0 && "$HOST_ID" != "None" && ! "$HOST_ID" =~ "error" ]]; then
                echo "[✅] Allocated new Dedicated Host: $HOST_ID in $AZ"
                CHOSEN_AZ="$AZ"
                
                # Update subnet if needed for new AZ
                SUBNET_ID=$(aws ec2 describe-subnets \
                    --region "$REGION" \
                    --filters "Name=vpc-id,Values=$VPC_ID" "Name=availability-zone,Values=$CHOSEN_AZ" "Name=tag:Name,Values=mac-rdst-subnet*" \
                    --query 'Subnets[0].SubnetId' \
                    --output text 2>/dev/null)
                
                if [[ "$SUBNET_ID" == "None" || -z "$SUBNET_ID" ]]; then
                    # Create subnet in new AZ if needed
                    SUBNET_CIDR="10.0.2.0/24"
                    SUBNET_ID=$(aws ec2 create-subnet \
                        --region "$REGION" \
                        --vpc-id "$VPC_ID" \
                        --cidr-block "$SUBNET_CIDR" \
                        --availability-zone "$CHOSEN_AZ" \
                        --query 'Subnet.SubnetId' \
                        --output text)
                    
                    aws ec2 create-tags \
                        --region "$REGION" \
                        --resources "$SUBNET_ID" \
                        --tags "Key=Name,Value=mac-rdst-subnet-$CHOSEN_AZ" "Key=Purpose,Value=RDST-Mac-Builds"
                    
                    echo "[✅] Created new subnet: $SUBNET_ID in $CHOSEN_AZ"
                fi
                
                # Retry instance launch with new host
                echo "[🚀] Retrying instance launch on new Dedicated Host..."
                set -x
                INSTANCE_ID=$(timeout 300s aws ec2 run-instances \
                    --region "$REGION" \
                    --image-id "$MAC_AMI_ID" \
                    --instance-type "$INSTANCE_TYPE" \
                    --iam-instance-profile "Name=$IAM_INSTANCE_PROFILE" \
                    --subnet-id "$SUBNET_ID" \
                    --security-group-ids "$SG_ID" \
                    --placement "Tenancy=host,HostId=$HOST_ID" \
                    --user-data "$USER_DATA" \
                    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=RDST-Mac-Builder-${TENANT}},{Key=Purpose,Value=RDST-Build},{Key=Tenant,Value=${TENANT}}]" \
                    --query 'Instances[0].InstanceId' \
                    --output text 2>&1)
                set +x
                
                if [[ $? -eq 0 && ! "$INSTANCE_ID" =~ "error" && -n "$INSTANCE_ID" && "$INSTANCE_ID" != "None" ]]; then
                    echo "[✅] Successfully launched instance on new host: $INSTANCE_ID"
                    break
                fi
            fi
            echo "[❌] Failed to allocate host in $AZ: $HOST_ID"
        done
        
        if [[ -z "$HOST_ID" ]] || [[ "$HOST_ID" =~ "error" ]]; then
            echo "❌ Failed to allocate new Dedicated Host in any AZ"
            exit 1
        fi
    fi
    
    # If still failed, show generic error
    if [[ "$INSTANCE_ID" =~ "error" ]] || [[ -z "$INSTANCE_ID" ]] || [[ "$INSTANCE_ID" == "None" ]]; then
        echo "❌ Failed to launch Mac instance: $INSTANCE_ID"
        echo "💡 Common issues:"
        echo "   - Mac instance quota exceeded"
        echo "   - AMI not compatible with region"
        echo "   - Dedicated Host allocation failed"
        exit 1
    fi
fi

echo "[📋] Instance ID: $INSTANCE_ID"

# Wait for instance to be running
echo "[⏳] Waiting for instance to be running..."
aws ec2 wait instance-running \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID"

echo "[✅] Instance is running!"

# Skip SSM wait for Mac instances - they're too slow and unreliable
echo "[💡] Skipping SSM agent wait for Mac instances (too slow/unreliable)"
echo "[📋] Instance should be bootstrapping in background"
sleep 60  # Give it a minute to start bootstrap

# Export instance info for other scripts
export MAC_INSTANCE_ID="$INSTANCE_ID"
echo "[🎯] Mac build instance ready:"
echo "    Instance ID: $INSTANCE_ID"
echo "    Region: $REGION"
echo "    AMI: $MAC_AMI_ID"

# Return instance ID
echo "$INSTANCE_ID"
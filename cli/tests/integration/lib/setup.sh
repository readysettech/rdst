#!/usr/bin/env bash

# =============================================================================
# ENVIRONMENT AND CONFIGURATION
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$ROOT_DIR"

# Python binary
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Install Python packages needed for workload scripts
echo "Installing Python packages for workload scripts..."
"$PYTHON_BIN" -m pip install --user --quiet mysql-connector-python psycopg2-binary || {
  echo "Warning: Failed to install Python packages. Workload-dependent tests may fail."
}

# Target names for RDST configuration
PG_TARGET_NAME="${PG_TARGET_NAME:-test-db-pg}"
MYSQL_TARGET_NAME="${MYSQL_TARGET_NAME:-test-db-mysql}"

# Database configuration (set by setup_upstream_databases)
PG_HOST=""
PG_PORT=""
PG_DB_NAME=""
PG_USER=""
PG_PASSWORD=""

MYSQL_HOST=""
MYSQL_PORT=""
MYSQL_DB_NAME=""
MYSQL_USER=""
MYSQL_PASSWORD=""

# Test database selection
TEST_POSTGRESQL="${TEST_POSTGRESQL:-true}"
TEST_MYSQL="${TEST_MYSQL:-true}"

if [[ -z "${API_BASE_URL:-}" ]]; then
  echo "ERROR: API_BASE_URL environment variable must be set" >&2
  exit 1
fi

# Container tracking for cleanup
CREATED_PSQL_CONTAINER_ID=""
CREATED_MYSQL_CONTAINER_ID=""

# Connection strings (if provided, skip container creation)
PSQL_CONNECTION_STRING="${PSQL_CONNECTION_STRING:-}"
MYSQL_CONNECTION_STRING="${MYSQL_CONNECTION_STRING:-}"

# Current test context (set by set_db_context)
TARGET_NAME=""
DB_ENGINE=""
DB_HOST=""
DB_PORT=""
DB_NAME=""
DB_USER=""
DB_PASSWORD=""

# Test state variables
PRIMARY_HASH=""
PRIMARY_TAG="film-popularity"
STRUCTURE_HASH=""
LIST_HASH=""

# LLM and environment configuration
# Fetch ANTHROPIC_API_KEY from AWS Secrets Manager when running in Buildkite
if [[ -z "${ANTHROPIC_API_KEY:-}" && -n "${BUILDKITE:-}" ]]; then
  if command -v aws >/dev/null 2>&1; then
    echo "Fetching ANTHROPIC_API_KEY from AWS Secrets Manager..."
    # Secret is stored as JSON: {"ANTHROPIC_API_KEY": "sk-ant-..."}
    SECRET_JSON=$(aws secretsmanager get-secret-value \
      --secret-id ANTHROPIC_API_KEY \
      --region us-east-2 \
      --query SecretString \
      --output text 2>/dev/null || echo "")
    if [[ -n "$SECRET_JSON" ]]; then
      ANTHROPIC_API_KEY=$(echo "$SECRET_JSON" | "$PYTHON_BIN" -c "import sys,json; print(json.load(sys.stdin).get('ANTHROPIC_API_KEY',''))" 2>/dev/null || echo "")
      if [[ -n "$ANTHROPIC_API_KEY" ]]; then
        export ANTHROPIC_API_KEY
        echo "✓ ANTHROPIC_API_KEY loaded from Secrets Manager"
      else
        echo "Warning: Failed to parse ANTHROPIC_API_KEY from secret JSON"
      fi
    else
      echo "Warning: Failed to fetch ANTHROPIC_API_KEY from Secrets Manager"
    fi
  else
    echo "Warning: AWS CLI not available, ANTHROPIC_API_KEY not set"
  fi
fi

# Disable telemetry during tests
export RDST_TESTING=true

export RICH_NO_COLOR=1
export TERM=dumb

# Capture Python site-packages paths BEFORE changing HOME
# This is critical because changing HOME will break site.getusersitepackages()
ORIGINAL_SITE_PATHS="$("$PYTHON_BIN" - <<'PY'
import site
paths = []
try:
    paths.extend(site.getsitepackages())
except Exception:
    pass
try:
    user_site = site.getusersitepackages()
    if user_site:
        paths.append(user_site)
except Exception:
    pass
seen = []
for path in paths:
    if path and path not in seen:
        seen.append(path)
print(":".join(seen))
PY
)"

# Temporary directories
TMP_HOME="$(mktemp -d)"
TMP_RUN="$(mktemp -d)"
export HOME="$TMP_HOME"

# ReadySet container configuration
READYSET_CONTAINER_NAME="rdst-integration-test-readyset"
READYSET_PORT="${READYSET_PORT:-5433}"

# =============================================================================
# CLEANUP
# =============================================================================

cleanup() {
  echo "Cleaning up test environment..."

  # Delete created database containers via admin API
  if [[ -n "$CREATED_PSQL_CONTAINER_ID" ]]; then
    echo "Deleting PostgreSQL container: $CREATED_PSQL_CONTAINER_ID"
    delete_upstream_container "$CREATED_PSQL_CONTAINER_ID" || true
  fi

  if [[ -n "$CREATED_MYSQL_CONTAINER_ID" ]]; then
    echo "Deleting MySQL container: $CREATED_MYSQL_CONTAINER_ID"
    delete_upstream_container "$CREATED_MYSQL_CONTAINER_ID" || true
  fi

  # Clean up cache command's test containers (created by rdst cache)
  if [[ "$TEST_POSTGRESQL" == "true" ]]; then
    docker rm -f "rdst-readyset-${PG_TARGET_NAME}" >/dev/null 2>&1 || true
    docker rm -f "rdst-test-psql-${PG_TARGET_NAME}" >/dev/null 2>&1 || true
  fi

  if [[ "$TEST_MYSQL" == "true" ]]; then
    docker rm -f "rdst-readyset-${MYSQL_TARGET_NAME}" >/dev/null 2>&1 || true
    docker rm -f "rdst-test-mysql-${MYSQL_TARGET_NAME}" >/dev/null 2>&1 || true
  fi

  # Always attempt to remove integration test containers
  docker rm -f "$READYSET_CONTAINER_NAME" >/dev/null 2>&1 || true
  rm -rf "$TMP_HOME" "$TMP_RUN"
}
trap cleanup EXIT

# =============================================================================
# INITIALIZATION
# =============================================================================

# Clean up leftover containers from previous runs
echo "Checking for leftover test containers from previous runs..."
if docker ps -a --filter "name=${READYSET_CONTAINER_NAME}" --format '{{.Names}}' | grep -q "^${READYSET_CONTAINER_NAME}$"; then
  echo "Removing leftover container: ${READYSET_CONTAINER_NAME}"
  docker rm -f "$READYSET_CONTAINER_NAME" >/dev/null 2>&1 || true
fi

for container in "rdst-readyset-${PG_TARGET_NAME}" "rdst-test-psql-${PG_TARGET_NAME}" \
                 "rdst-readyset-${MYSQL_TARGET_NAME}" "rdst-test-mysql-${MYSQL_TARGET_NAME}"; do
  if docker ps -a --filter "name=${container}" --format '{{.Names}}' | grep -q "^${container}$"; then
    echo "Removing leftover cache container: ${container}"
    docker rm -f "$container" >/dev/null 2>&1 || true
  fi
done

# Check for port conflicts
CONTAINERS_ON_PORT=$(docker ps --format '{{.Names}}' --filter "publish=${READYSET_PORT}" 2>/dev/null || true)
if [[ -n "$CONTAINERS_ON_PORT" ]]; then
  echo "WARNING: Found containers using port ${READYSET_PORT}: ${CONTAINERS_ON_PORT}"
  echo "These may interfere with tests. Consider stopping them with: docker stop ${CONTAINERS_ON_PORT}"
fi

# Configure Python environment using the site paths captured before HOME was changed
if [[ -n "$ORIGINAL_SITE_PATHS" ]]; then
  if [[ -n "${PYTHONPATH:-}" ]]; then
    export PYTHONPATH="${ORIGINAL_SITE_PATHS}:${PYTHONPATH}"
  else
    export PYTHONPATH="${ORIGINAL_SITE_PATHS}"
  fi
fi
if [[ -n "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="${ROOT_DIR}:${PYTHONPATH}"
else
  export PYTHONPATH="${ROOT_DIR}"
fi

# Set up output directory
OUTPUT_DIR="$TMP_RUN/output"
mkdir -p "$OUTPUT_DIR"

# RDST command array
if [[ -n "${RDST_BINARY:-}" ]]; then
  # Use pre-built binary if specified (for testing compiled AL23/RPM/DEB builds)
  RDST_CMD=("$RDST_BINARY")
  echo "Using RDST binary: $RDST_BINARY"
else
  # Default: use Python source (for development)
  RDST_CMD=("$PYTHON_BIN" "rdst.py")
  echo "Using RDST Python source: rdst.py"
fi

# Change to cloud_agent directory so relative path works
cd "$ROOT_DIR"

# =============================================================================
# CONTAINER MANAGEMENT
# =============================================================================

parse_connection_string() {
  local conn_str="$1"
  local var_prefix="$2"

  # Remove query parameters if present (e.g., ?sslmode=disable)
  local clean_str="${conn_str%%\?*}"

  # Extract components using regex
  # Format: protocol://user:password@host:port/database
  if [[ "$clean_str" =~ ^([^:]+)://([^:]+):([^@]+)@([^:]+):([^/]+)/(.+)$ ]]; then
    eval "${var_prefix}_USER=\"${BASH_REMATCH[2]}\""
    eval "${var_prefix}_PASSWORD=\"${BASH_REMATCH[3]}\""
    eval "${var_prefix}_HOST=\"${BASH_REMATCH[4]}\""
    eval "${var_prefix}_PORT=\"${BASH_REMATCH[5]}\""
    eval "${var_prefix}_DB_NAME=\"${BASH_REMATCH[6]}\""
    return 0
  else
    echo "ERROR: Failed to parse connection string: $conn_str" >&2
    echo "Expected format: protocol://user:password@host:port/database" >&2
    return 1
  fi
}

find_existing_container_by_tag() {
  local tag="$1"
  local db_type="$2"

  # Check if ADMIN_API_TOKEN is set
  if [[ -z "${ADMIN_API_TOKEN:-}" ]]; then
    echo ""
    return 0
  fi

  echo "Checking for existing container with tag: $tag" >&2

  # Use the new dedicated endpoint to get container by tag
  local response
  local url="${API_BASE_URL}/admin/psql_container_by_tag?tag=${tag}"
  if [[ -n "$db_type" ]]; then
    url="${url}&db_type=${db_type}"
  fi

  response=$(curl -s -w "\n%{http_code}" "${url}" \
    -H "Authorization: Bearer ${ADMIN_API_TOKEN}")

  # Extract HTTP status code (last line)
  local http_code=$(echo "$response" | tail -n1)
  local body=$(echo "$response" | sed '$d')

  # If 404, container not found - return empty
  if [[ "$http_code" == "404" ]]; then
    echo ""
    return 0
  fi

  # If not 200, there was an error
  if [[ "$http_code" != "200" ]]; then
    echo "Warning: Failed to check for existing container (HTTP $http_code)" >&2
    echo ""
    return 0
  fi

  # Parse the container ID from the response
  local container_id
  container_id=$(echo "$body" | "$PYTHON_BIN" -c "import sys, json; data=json.load(sys.stdin); print(data.get('id', ''))" 2>/dev/null || echo "")

  if [[ -n "$container_id" ]]; then
    echo "✓ Found existing running container: $container_id" >&2
  fi

  echo "$container_id"
}

create_upstream_container() {
  local db_type="$1"  # "psql" or "mysql"
  local tag="${2:-${db_type}-rdst-integration-test}"

  # Check if ADMIN_API_TOKEN is set
  if [[ -z "${ADMIN_API_TOKEN:-}" ]]; then
    echo "ERROR: ADMIN_API_TOKEN environment variable is not set" >&2
    echo "Please export ADMIN_API_TOKEN before running this script" >&2
    return 1
  fi

  # Check if a container with this tag already exists
  local existing_container_id
  existing_container_id=$(find_existing_container_by_tag "$tag" "$db_type")

  if [[ -n "$existing_container_id" ]]; then
    echo "✓ Reusing existing $db_type container: $existing_container_id" >&2
    # Return with REUSED prefix so caller knows not to delete it
    echo "REUSED:$existing_container_id"
    return 0
  fi

  echo "Creating new $db_type upstream database container with tag: $tag" >&2

  local response
  response=$(curl -s -X POST "${API_BASE_URL}/admin/create_psql_container" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${ADMIN_API_TOKEN}" \
    -d "{\"num_containers\": 1, \"db_type\": \"$db_type\", \"tag\": \"{\\\"cluster_id\\\": \\\"$tag\\\"}\"}")

  local container_id
  container_id=$(echo "$response" | "$PYTHON_BIN" -c "import sys, json; data=json.load(sys.stdin); print(data[0]['id'] if data else '')" 2>/dev/null || echo "")

  if [[ -z "$container_id" ]]; then
    echo "ERROR: Failed to create $db_type container. Response: $response" >&2
    return 1
  fi

  echo "✓ Created new container: $container_id" >&2
  # Return with CREATED prefix so caller knows to delete it on cleanup
  echo "CREATED:$container_id"
}

test_database_connection() {
  local conn_str="$1"

  # Parse connection string to determine database type and credentials
  # Format: protocol://user:password@host:port/database
  if [[ "$conn_str" =~ ^(postgresql|mysql)://([^:]+):([^@]+)@([^:]+):([^/]+)/(.+)$ ]]; then
    local protocol="${BASH_REMATCH[1]}"
    local user="${BASH_REMATCH[2]}"
    local password="${BASH_REMATCH[3]}"
    local host="${BASH_REMATCH[4]}"
    local port="${BASH_REMATCH[5]}"
    local database="${BASH_REMATCH[6]}"

    if [[ "$protocol" == "postgresql" ]]; then
      # Try psql CLI first, fallback to Python with psycopg2
      if command -v psql >/dev/null 2>&1; then
        PGPASSWORD="$password" PGCONNECT_TIMEOUT=10 psql -h "$host" -p "$port" -U "$user" -d "$database" -t -A -c "SELECT 1;" >/dev/null 2>&1
        local result=$?
        return $result
      else
        # Fallback to Python with psycopg2
        python3 -c "
import sys
try:
    import psycopg2
    conn = psycopg2.connect(
        host='$host',
        port=$port,
        user='$user',
        password='$password',
        database='$database',
        connect_timeout=10
    )
    conn.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null
        return $?
      fi
    elif [[ "$protocol" == "mysql" ]]; then
      # Try mysql CLI first, fallback to Python with pymysql
      if command -v mysql >/dev/null 2>&1; then
        MYSQL_PWD="$password" mysql --protocol=TCP --host="$host" --port="$port" --user="$user" --database="$database" --connect-timeout=10 -s -N -e "SELECT 1;" >/dev/null 2>&1
        local result=$?
        return $result
      else
        # Fallback to Python with pymysql
        python3 -c "
import sys
try:
    import pymysql
    conn = pymysql.connect(
        host='$host',
        port=$port,
        user='$user',
        password='$password',
        database='$database',
        connect_timeout=10
    )
    conn.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null
        return $?
      fi
    fi
  fi

  return 1
}

poll_container_ready() {
  local container_id="$1"
  local max_wait="${2:-120}"  # Default 2 minute

  echo "Polling container $container_id until ready..." >&2
  local elapsed=0
  local check_interval=5
  local conn_str=""
  local container_running=false

  while (( elapsed < max_wait )); do
    local response
    response=$(curl -s "${API_BASE_URL}/admin/psql_container/${container_id}" \
      -H "Authorization: Bearer ${ADMIN_API_TOKEN}")

    local status
    status=$(echo "$response" | "$PYTHON_BIN" -c "import sys, json; data=json.load(sys.stdin); print(data.get('status', ''))" 2>/dev/null || echo "")

    if [[ "$status" == "running" ]]; then
      if [[ "$container_running" == "false" ]]; then
        container_running=true
        conn_str=$(echo "$response" | "$PYTHON_BIN" -c "import sys, json; data=json.load(sys.stdin); print(data.get('connection_string', ''))" 2>/dev/null || echo "")

        if [[ -z "$conn_str" ]]; then
          echo "ERROR: Container running but no connection string available" >&2
          return 1
        fi

        echo "  Container status: running, testing database connectivity..." >&2
      fi

      # Test if database is actually accepting connections
      if test_database_connection "$conn_str"; then
        echo "✓ Container is ready and database is accepting connections!" >&2
        echo "$conn_str"
        return 0
      else
        echo "  Database not ready yet, waiting... (${elapsed}s / ${max_wait}s)" >&2
      fi
    else
      echo "  Waiting... (${elapsed}s / ${max_wait}s) - Status: $status" >&2
    fi

    sleep "$check_interval"
    elapsed=$((elapsed + check_interval))
  done

  echo "ERROR: Container did not become ready within ${max_wait}s" >&2
  return 1
}

delete_upstream_container() {
  local container_id="$1"

  echo "Deleting upstream container: $container_id"

  # Skip if ADMIN_API_TOKEN is not set (cleanup during error conditions)
  if [[ -z "${ADMIN_API_TOKEN:-}" ]]; then
    echo "Warning: ADMIN_API_TOKEN not set, skipping container deletion"
    return 0
  fi

  local response
  response=$(curl -s -X DELETE "${API_BASE_URL}/admin/delete_psql_container" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${ADMIN_API_TOKEN}" \
    -d "{\"container_ids\": [\"$container_id\"]}")

  echo "Delete response: $response"
}

setup_upstream_databases() {
  echo
  echo "======================================================================"
  echo "Setting up Upstream Databases"
  echo "======================================================================"

  # Setup PostgreSQL if needed
  if [[ "$TEST_POSTGRESQL" == "true" ]]; then
    if [[ -n "$PSQL_CONNECTION_STRING" ]]; then
      echo "Using provided PostgreSQL connection string"
      parse_connection_string "$PSQL_CONNECTION_STRING" "PG" || fail "Failed to parse PSQL_CONNECTION_STRING"
    else
      echo "Creating PostgreSQL container via admin API..."
      local container_result
      container_result=$(create_upstream_container "psql")

      if [[ -z "$container_result" ]]; then
        fail "Failed to create PostgreSQL container"
      fi

      # Parse result to determine if created or reused
      if [[ "$container_result" == CREATED:* ]]; then
        CREATED_PSQL_CONTAINER_ID="${container_result#CREATED:}"
      elif [[ "$container_result" == REUSED:* ]]; then
        # Don't set CREATED_*_ID for reused containers (won't be deleted in cleanup)
        CREATED_PSQL_CONTAINER_ID=""
        container_result="${container_result#REUSED:}"
      else
        # Fallback for backward compatibility
        CREATED_PSQL_CONTAINER_ID="$container_result"
      fi

      local container_id="${container_result#*:}"
      PSQL_CONNECTION_STRING=$(poll_container_ready "$container_id")
      if [[ -z "$PSQL_CONNECTION_STRING" ]]; then
        fail "Failed to get PostgreSQL connection string"
      fi

      parse_connection_string "$PSQL_CONNECTION_STRING" "PG" || fail "Failed to parse PostgreSQL connection string"
    fi

    # Export for password environment variable
    export POSTGRESQL_PASSWORD="$PG_PASSWORD"
    export DB_PASSWORD="$PG_PASSWORD"

    echo "✓ PostgreSQL configuration:"
    echo "  Host: $PG_HOST"
    echo "  Port: $PG_PORT"
    echo "  Database: $PG_DB_NAME"
    echo "  User: $PG_USER"
  fi

  # Setup MySQL if needed
  if [[ "$TEST_MYSQL" == "true" ]]; then
    if [[ -n "$MYSQL_CONNECTION_STRING" ]]; then
      echo "Using provided MySQL connection string"
      parse_connection_string "$MYSQL_CONNECTION_STRING" "MYSQL" || fail "Failed to parse MYSQL_CONNECTION_STRING"
    else
      echo "Creating MySQL container via admin API..."
      local container_result
      container_result=$(create_upstream_container "mysql")

      if [[ -z "$container_result" ]]; then
        fail "Failed to create MySQL container"
      fi

      # Parse result to determine if created or reused
      if [[ "$container_result" == CREATED:* ]]; then
        CREATED_MYSQL_CONTAINER_ID="${container_result#CREATED:}"
      elif [[ "$container_result" == REUSED:* ]]; then
        # Don't set CREATED_*_ID for reused containers (won't be deleted in cleanup)
        CREATED_MYSQL_CONTAINER_ID=""
        container_result="${container_result#REUSED:}"
      else
        # Fallback for backward compatibility
        CREATED_MYSQL_CONTAINER_ID="$container_result"
      fi

      local container_id="${container_result#*:}"
      MYSQL_CONNECTION_STRING=$(poll_container_ready "$container_id")
      if [[ -z "$MYSQL_CONNECTION_STRING" ]]; then
        fail "Failed to get MySQL connection string"
      fi

      parse_connection_string "$MYSQL_CONNECTION_STRING" "MYSQL" || fail "Failed to parse MySQL connection string"
    fi

    # Export for password environment variable
    export MYSQL_PASSWORD="$MYSQL_PASSWORD"
    export DB_PASSWORD="$MYSQL_PASSWORD"

    echo "✓ MySQL configuration:"
    echo "  Host: $MYSQL_HOST"
    echo "  Port: $MYSQL_PORT"
    echo "  Database: $MYSQL_DB_NAME"
    echo "  User: $MYSQL_USER"
  fi
}

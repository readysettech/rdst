#!/usr/bin/env bash

test_config_commands() {
  log_section "1. Configuration Commands (${DB_ENGINE})"

  # Remove any existing configuration for this target
  "${RDST_CMD[@]}" configure remove "$TARGET_NAME" --confirm >/dev/null 2>&1 || true

  # Determine password environment variable name
  local password_env
  if [[ "$DB_ENGINE" == "postgresql" ]]; then
    password_env="POSTGRESQL_PASSWORD"
  else
    password_env="MYSQL_PASSWORD"
  fi

  # Add a new target with all options
  run_cmd "Configure add target" \
    "${RDST_CMD[@]}" configure add \
    --target "$TARGET_NAME" \
    --engine "$DB_ENGINE" \
    --host "$DB_HOST" \
    --port "$DB_PORT" \
    --user "$DB_USER" \
    --database "$DB_NAME" \
    --password-env "$password_env" \
    --default
  assert_contains "Target '$TARGET_NAME'" "configure add output"

  # List targets
  run_cmd "Configure list" \
    "${RDST_CMD[@]}" configure list
  assert_contains "$TARGET_NAME" "list should show new target"
  assert_contains "$DB_HOST" "list should show host"
  assert_contains "$DB_PORT" "list should show port"

  # Verify default was set in config file
  local config_file="$HOME/.rdst/config.toml"
  if [[ -f "$config_file" ]]; then
    if grep -q "default.*=.*\"${TARGET_NAME}\"" "$config_file"; then
      echo "✓ Target '${TARGET_NAME}' is set as default in config"
    else
      echo "⚠ Warning: Target may not be set as default in config"
    fi
  fi
}

test_config_connection_string() {
  log_section "2. Connection String Support (${DB_ENGINE})"

  local test_target="${TARGET_NAME}-connstr"

  # Remove any existing configuration for this target
  "${RDST_CMD[@]}" configure remove "$test_target" --confirm >/dev/null 2>&1 || true

  # Build connection string based on engine
  local conn_str
  if [[ "$DB_ENGINE" == "postgresql" ]]; then
    conn_str="postgresql://${DB_USER}:testpass@${DB_HOST}:${DB_PORT}/${DB_NAME}?sslmode=require"
  else
    conn_str="mysql://${DB_USER}:testpass@${DB_HOST}:${DB_PORT}/${DB_NAME}?ssl=true"
  fi

  # Add target using connection string
  run_cmd "Configure add with connection string" \
    "${RDST_CMD[@]}" configure add \
    --target "$test_target" \
    --connection-string "$conn_str" \
    --skip-verify
  assert_contains "Target '$test_target'" "configure add output"

  # Verify configuration was parsed correctly
  local config_file="$HOME/.rdst/config.toml"
  if [[ -f "$config_file" ]]; then
    # Check that engine was detected
    if grep -A 10 "\[targets.${test_target}\]" "$config_file" | grep -q "engine.*=.*\"${DB_ENGINE}\""; then
      echo "✓ Engine correctly detected from connection string"
    else
      echo "✗ Failed to detect engine from connection string"
      return 1
    fi

    # Check that host was parsed
    if grep -A 10 "\[targets.${test_target}\]" "$config_file" | grep -q "host.*=.*\"${DB_HOST}\""; then
      echo "✓ Host correctly parsed from connection string"
    else
      echo "✗ Failed to parse host from connection string"
      return 1
    fi

    # Check that port was parsed
    if grep -A 10 "\[targets.${test_target}\]" "$config_file" | grep -q "port.*=.*${DB_PORT}"; then
      echo "✓ Port correctly parsed from connection string"
    else
      echo "✗ Failed to parse port from connection string"
      return 1
    fi

    # Check that database was parsed
    if grep -A 10 "\[targets.${test_target}\]" "$config_file" | grep -q "database.*=.*\"${DB_NAME}\""; then
      echo "✓ Database correctly parsed from connection string"
    else
      echo "✗ Failed to parse database from connection string"
      return 1
    fi

    # Check that TLS was enabled from SSL parameters
    if grep -A 10 "\[targets.${test_target}\]" "$config_file" | grep -q "tls.*=.*true"; then
      echo "✓ TLS correctly enabled from SSL parameters"
    else
      echo "✗ Failed to enable TLS from SSL parameters"
      return 1
    fi
  fi

  # Clean up
  "${RDST_CMD[@]}" configure remove "$test_target" --confirm >/dev/null 2>&1 || true
}

test_config_connection_string_override() {
  log_section "3. Connection String with Flag Overrides (${DB_ENGINE})"

  local test_target="${TARGET_NAME}-override"

  # Remove any existing configuration for this target
  "${RDST_CMD[@]}" configure remove "$test_target" --confirm >/dev/null 2>&1 || true

  # Build connection string with values that will be overridden
  local conn_str
  local override_port
  if [[ "$DB_ENGINE" == "postgresql" ]]; then
    conn_str="postgresql://${DB_USER}:testpass@original-host:5432/original-db"
    override_port=15432
  else
    conn_str="mysql://${DB_USER}:testpass@original-host:3306/original-db"
    override_port=13306
  fi

  # Add target using connection string but override some values
  run_cmd "Configure add with overrides" \
    "${RDST_CMD[@]}" configure add \
    --target "$test_target" \
    --connection-string "$conn_str" \
    --host "$DB_HOST" \
    --port "$override_port" \
    --database "$DB_NAME" \
    --no-tls \
    --skip-verify
  assert_contains "Target '$test_target'" "configure add output"

  # Verify overrides took effect
  local config_file="$HOME/.rdst/config.toml"
  if [[ -f "$config_file" ]]; then
    # Check that overridden host was used
    if grep -A 10 "\[targets.${test_target}\]" "$config_file" | grep -q "host.*=.*\"${DB_HOST}\""; then
      echo "✓ Host override applied correctly"
    else
      echo "✗ Host override failed"
      return 1
    fi

    # Check that overridden port was used
    if grep -A 10 "\[targets.${test_target}\]" "$config_file" | grep -q "port.*=.*${override_port}"; then
      echo "✓ Port override applied correctly"
    else
      echo "✗ Port override failed"
      return 1
    fi

    # Check that overridden database was used
    if grep -A 10 "\[targets.${test_target}\]" "$config_file" | grep -q "database.*=.*\"${DB_NAME}\""; then
      echo "✓ Database override applied correctly"
    else
      echo "✗ Database override failed"
      return 1
    fi

    # Check that TLS was disabled via --no-tls flag
    if grep -A 10 "\[targets.${test_target}\]" "$config_file" | grep -q "tls.*=.*false"; then
      echo "✓ TLS override (--no-tls) applied correctly"
    else
      echo "✗ TLS override failed"
      return 1
    fi
  fi

  # Clean up
  "${RDST_CMD[@]}" configure remove "$test_target" --confirm >/dev/null 2>&1 || true
}

test_config_connection_string_no_password() {
  log_section "4. Connection String without Password (${DB_ENGINE})"

  local test_target="${TARGET_NAME}-nopass"

  # Remove any existing configuration for this target
  "${RDST_CMD[@]}" configure remove "$test_target" --confirm >/dev/null 2>&1 || true

  # Build connection string without password
  local conn_str
  if [[ "$DB_ENGINE" == "postgresql" ]]; then
    conn_str="postgresql://${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
  else
    conn_str="mysql://${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
  fi

  # Add target using connection string without password
  run_cmd "Configure add without password" \
    "${RDST_CMD[@]}" configure add \
    --target "$test_target" \
    --connection-string "$conn_str" \
    --password-env "TEST_PASSWORD" \
    --skip-verify
  assert_contains "Target '$test_target'" "configure add output"

  # Verify password_env was set
  local config_file="$HOME/.rdst/config.toml"
  if [[ -f "$config_file" ]]; then
    if grep -A 10 "\[targets.${test_target}\]" "$config_file" | grep -q "password_env.*=.*\"TEST_PASSWORD\""; then
      echo "✓ Password environment variable correctly set"
    else
      echo "✗ Failed to set password environment variable"
      return 1
    fi
  fi

  # Clean up
  "${RDST_CMD[@]}" configure remove "$test_target" --confirm >/dev/null 2>&1 || true
}

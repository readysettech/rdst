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

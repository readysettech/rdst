#!/usr/bin/env python3
"""
Deploy Script Generation Tests

CI-safe tests that verify deploy script generation without executing
any actual deployments. No Docker, SSH, kubectl, or systemd required.

These tests validate:
1. Script templates render correctly with variables
2. All deployment modes produce valid output
3. Error handling for missing targets, bad configs
4. Management subcommands exist in generated scripts
5. K8s manifests contain correct YAML structure
"""

import os
import sys
import json
import pytest

# Add rdst root to path
script_dir = os.path.dirname(os.path.abspath(__file__))
rdst_dir = os.path.join(script_dir, "..", "..", "..")
sys.path.insert(0, rdst_dir)

from lib.deploy.script_generator import (
    generate_script,
    build_variables,
    generate_k8s_apply_manifests,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mysql_target_config():
    """Minimal MySQL target config for testing."""
    return {
        "engine": "mysql",
        "host": "db.example.com",
        "port": 3306,
        "user": "admin",
        "database": "myapp",
    }


@pytest.fixture
def postgres_target_config():
    """Minimal PostgreSQL target config for testing."""
    return {
        "engine": "postgresql",
        "host": "pg.example.com",
        "port": 5432,
        "user": "pgadmin",
        "database": "webapp",
    }


@pytest.fixture
def mysql_variables(mysql_target_config):
    """Build variables from MySQL target config."""
    return build_variables(
        target_name="testdb",
        target_config=mysql_target_config,
        password="secret123",
        port=None,
        deploy_config="readyset",
        namespace="readyset",
    )


@pytest.fixture
def postgres_variables(postgres_target_config):
    """Build variables from PostgreSQL target config."""
    return build_variables(
        target_name="pgtest",
        target_config=postgres_target_config,
        password="pgpass",
        port=5433,
        deploy_config="readyset",
        namespace="myns",
    )


# ---------------------------------------------------------------------------
# build_variables tests
# ---------------------------------------------------------------------------

class TestBuildVariables:
    """Tests for variable building from target config."""

    def test_mysql_defaults(self, mysql_target_config):
        variables = build_variables(
            target_name="mydb",
            target_config=mysql_target_config,
            password="pass",
            port=None,
            deploy_config="readyset",
            namespace="readyset",
        )
        assert variables["target_name"] == "mydb"
        assert variables["db_engine"] == "mysql"
        assert variables["db_host"] == "db.example.com"
        assert variables["db_port"] == "3306"
        assert variables["db_user"] == "admin"
        assert variables["db_name"] == "myapp"
        assert variables["container_name"] == "rdst-readyset-mydb"
        assert variables["deploy_config"] == "readyset"
        assert variables["namespace"] == "readyset"
        # Default MySQL readyset port
        assert variables["readyset_port"] == "3307"

    def test_postgres_defaults(self, postgres_target_config):
        variables = build_variables(
            target_name="pgdb",
            target_config=postgres_target_config,
            password="pass",
            port=None,
            deploy_config="readyset",
            namespace="readyset",
        )
        assert variables["db_engine"] == "postgresql"
        # Default PostgreSQL readyset port
        assert variables["readyset_port"] == "5433"

    def test_custom_port(self, mysql_target_config):
        variables = build_variables(
            target_name="mydb",
            target_config=mysql_target_config,
            password="pass",
            port=9999,
            deploy_config="readyset",
            namespace="readyset",
        )
        assert variables["readyset_port"] == "9999"

    def test_readyset_image_present(self, mysql_variables):
        assert "readyset_image" in mysql_variables
        assert "readyset" in mysql_variables["readyset_image"].lower()


# ---------------------------------------------------------------------------
# Docker script generation tests
# ---------------------------------------------------------------------------

class TestDockerScript:
    """Tests for Docker deployment script generation."""

    def test_generates_valid_bash(self, mysql_variables):
        script = generate_script("docker", mysql_variables)
        assert script.startswith("#!/usr/bin/env bash")
        assert "set -euo pipefail" in script

    def test_variables_injected(self, mysql_variables):
        script = generate_script("docker", mysql_variables)
        assert 'CONTAINER_NAME="rdst-readyset-testdb"' in script
        assert 'DB_ENGINE="mysql"' in script
        assert 'DB_HOST="db.example.com"' in script
        assert 'DB_PORT="3306"' in script
        assert 'DB_USER="admin"' in script
        assert 'DB_NAME="myapp"' in script
        assert 'READYSET_PORT="3307"' in script

    def test_no_unresolved_placeholders(self, mysql_variables):
        script = generate_script("docker", mysql_variables)
        # Check that no {variable} placeholders remain (except ${...} bash vars)
        import re
        unresolved = re.findall(r'(?<!\$)\{[a-z_]+\}', script)
        # Filter out known patterns: bash ${1:-}, and {placeholder} in comments
        known = {"{placeholder}"}
        real_unresolved = [u for u in unresolved if u not in known]
        assert real_unresolved == [], f"Unresolved placeholders: {real_unresolved}"

    def test_management_subcommands(self, mysql_variables):
        script = generate_script("docker", mysql_variables)
        for cmd in ["stop)", "logs)", "status)", "restart)", "uninstall)"]:
            assert cmd in script, f"Missing subcommand: {cmd}"

    def test_docker_check(self, mysql_variables):
        script = generate_script("docker", mysql_variables)
        assert "command -v docker" in script
        assert "docker info" in script

    def test_shallow_cache_mode(self, mysql_variables):
        script = generate_script("docker", mysql_variables)
        assert "CACHE_MODE" in script
        assert "shallow" in script.lower()

    def test_readyset_env_vars(self, mysql_variables):
        script = generate_script("docker", mysql_variables)
        assert "UPSTREAM_DB_URL" in script
        assert "DATABASE_TYPE" in script
        assert "LISTEN_ADDRESS" in script
        assert "DEPLOYMENT_MODE" in script
        assert "QUERY_CACHING" in script

    def test_postgres_engine(self, postgres_variables):
        script = generate_script("docker", postgres_variables)
        assert 'DB_ENGINE="postgresql"' in script
        assert "postgresql://" in script


# ---------------------------------------------------------------------------
# Systemd script generation tests
# ---------------------------------------------------------------------------

class TestSystemdScript:
    """Tests for systemd deployment script generation."""

    def test_generates_valid_bash(self, mysql_variables):
        script = generate_script("systemd", mysql_variables)
        assert script.startswith("#!/usr/bin/env bash")
        assert "set -euo pipefail" in script

    def test_variables_injected(self, mysql_variables):
        script = generate_script("systemd", mysql_variables)
        assert 'SERVICE_NAME="readyset-cache-testdb"' in script
        assert 'DB_ENGINE="mysql"' in script

    def test_management_subcommands(self, mysql_variables):
        script = generate_script("systemd", mysql_variables)
        for cmd in ["stop)", "logs)", "status)", "restart)", "uninstall)"]:
            assert cmd in script, f"Missing subcommand: {cmd}"

    def test_systemctl_check(self, mysql_variables):
        script = generate_script("systemd", mysql_variables)
        assert "command -v systemctl" in script

    def test_systemd_unit_file(self, mysql_variables):
        script = generate_script("systemd", mysql_variables)
        assert "[Unit]" in script
        assert "[Service]" in script
        assert "[Install]" in script
        assert "WantedBy=multi-user.target" in script

    def test_readyset_env_vars(self, mysql_variables):
        script = generate_script("systemd", mysql_variables)
        assert "UPSTREAM_DB_URL" in script
        assert "DEPLOYMENT_MODE" in script
        assert "CACHE_MODE=shallow" in script

    def test_binary_download(self, mysql_variables):
        script = generate_script("systemd", mysql_variables)
        assert "BINARY_PATH" in script


# ---------------------------------------------------------------------------
# Kubernetes manifest generation tests
# ---------------------------------------------------------------------------

class TestKubernetesManifests:
    """Tests for Kubernetes manifest generation."""

    def test_full_manifests_include_secret(self, mysql_variables):
        manifests = generate_script("kubernetes", mysql_variables)
        assert "kind: Secret" in manifests
        assert "kind: Deployment" in manifests
        assert "kind: Service" in manifests

    def test_apply_manifests_exclude_secret(self, mysql_variables):
        manifests = generate_k8s_apply_manifests(mysql_variables)
        assert "kind: Secret" not in manifests
        assert "kind: Deployment" in manifests
        assert "kind: Service" in manifests

    def test_deployment_config(self, mysql_variables):
        manifests = generate_script("kubernetes", mysql_variables)
        assert "readyset-cache-testdb" in manifests
        assert "namespace: readyset" in manifests
        assert "managed-by: rdst" in manifests

    def test_readyset_env_vars(self, mysql_variables):
        manifests = generate_script("kubernetes", mysql_variables)
        assert "UPSTREAM_DB_URL" in manifests
        assert "DATABASE_TYPE" in manifests
        assert "LISTEN_ADDRESS" in manifests
        assert "CACHE_MODE" in manifests
        assert "shallow" in manifests.lower()

    def test_secret_reference(self, mysql_variables):
        manifests = generate_script("kubernetes", mysql_variables)
        assert "secretKeyRef" in manifests
        assert "readyset-testdb" in manifests

    def test_service_port(self, mysql_variables):
        manifests = generate_script("kubernetes", mysql_variables)
        assert "port: 3307" in manifests
        assert "targetPort: 3307" in manifests

    def test_readiness_probe(self, mysql_variables):
        manifests = generate_script("kubernetes", mysql_variables)
        assert "readinessProbe" in manifests
        assert "livenessProbe" in manifests

    def test_custom_namespace(self, postgres_variables):
        manifests = generate_script("kubernetes", postgres_variables)
        assert "namespace: myns" in manifests

    def test_postgres_port(self, postgres_variables):
        manifests = generate_script("kubernetes", postgres_variables)
        assert "port: 5433" in manifests


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Tests for error scenarios."""

    def test_invalid_mode(self, mysql_variables):
        with pytest.raises(FileNotFoundError):
            generate_script("invalid_mode", mysql_variables)

    def test_deploy_command_missing_target(self):
        from lib.cli.cache_deploy import DeployCommand
        cmd = DeployCommand()
        result = cmd.execute(target=None)
        assert not result.ok
        assert "Target is required" in result.message

    def test_deploy_command_nonexistent_target(self):
        from lib.cli.cache_deploy import DeployCommand
        cmd = DeployCommand()
        result = cmd.execute(target="nonexistent_target_12345")
        assert not result.ok
        assert "not found" in result.message.lower()


# ---------------------------------------------------------------------------
# Deploy command endpoint building tests
# ---------------------------------------------------------------------------

class TestEndpointBuilding:
    """Tests for connection endpoint string generation."""

    def test_mysql_endpoint(self, mysql_variables):
        from lib.cli.cache_deploy import DeployCommand
        cmd = DeployCommand()
        endpoint = cmd._build_endpoint(mysql_variables)
        assert endpoint.startswith("mysql://")
        assert "admin" in endpoint
        assert "3307" in endpoint
        assert "myapp" in endpoint

    def test_postgres_endpoint(self, postgres_variables):
        from lib.cli.cache_deploy import DeployCommand
        cmd = DeployCommand()
        endpoint = cmd._build_endpoint(postgres_variables)
        assert endpoint.startswith("postgresql://")
        assert "5433" in endpoint

    def test_custom_host(self, mysql_variables):
        from lib.cli.cache_deploy import DeployCommand
        cmd = DeployCommand()
        endpoint = cmd._build_endpoint(mysql_variables, host="10.0.1.50")
        assert "10.0.1.50" in endpoint

    def test_k8s_service_host(self, mysql_variables):
        from lib.cli.cache_deploy import DeployCommand
        cmd = DeployCommand()
        endpoint = cmd._build_endpoint(
            mysql_variables,
            host="readyset-cache-testdb.readyset.svc.cluster.local",
        )
        assert "svc.cluster.local" in endpoint


# ---------------------------------------------------------------------------
# Password injection tests (for remote SSH deployment)
# ---------------------------------------------------------------------------

class TestPasswordInjection:
    """Tests for password injection in remote deployment scripts."""

    def test_inject_password_docker(self, mysql_variables):
        from lib.deploy.remote import _inject_password
        script = generate_script("docker", mysql_variables)
        injected = _inject_password(script, "mypassword")
        # shlex.quote wraps in single quotes for safety
        assert "DB_PASSWORD=" in injected
        assert "mypassword" in injected
        assert "read -s" not in injected

    def test_inject_password_systemd(self, mysql_variables):
        from lib.deploy.remote import _inject_password
        script = generate_script("systemd", mysql_variables)
        injected = _inject_password(script, "mypassword")
        assert "DB_PASSWORD=" in injected
        assert "mypassword" in injected
        assert "read -s" not in injected

    def test_inject_password_special_chars(self, mysql_variables):
        """Ensure shell metacharacters are safely escaped via shlex.quote."""
        import shlex
        from lib.deploy.remote import _inject_password
        script = generate_script("docker", mysql_variables)
        dangerous = 'pa$$word"; rm -rf / #'
        injected = _inject_password(script, dangerous)
        assert "DB_PASSWORD=" in injected
        assert "read -s" not in injected
        # shlex.quote wraps in single quotes, making metacharacters inert
        pw_line = [l for l in injected.split('\n') if 'DB_PASSWORD=' in l][0]
        expected = f"DB_PASSWORD={shlex.quote(dangerous)}"
        assert pw_line.strip() == expected

    def test_no_password_empty_string(self, mysql_variables):
        from lib.deploy.remote import _inject_password
        script = generate_script("docker", mysql_variables)
        injected = _inject_password(script, "")
        # Empty password should still replace the read block
        assert "DB_PASSWORD=" in injected
        assert "read -s" not in injected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

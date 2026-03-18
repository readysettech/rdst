"""Tests for AWS RDS discovery (mocked botocore)."""

from unittest.mock import MagicMock, patch

from lib.fleet.aws_discovery import _parse_instance, _ENGINE_MAP


class TestEngineMapping:
    def test_postgres_mapping(self):
        assert _ENGINE_MAP["postgres"] == "postgresql"
        assert _ENGINE_MAP["aurora-postgresql"] == "postgresql"

    def test_mysql_mapping(self):
        assert _ENGINE_MAP["mysql"] == "mysql"
        assert _ENGINE_MAP["aurora-mysql"] == "mysql"
        assert _ENGINE_MAP["aurora"] == "mysql"
        assert _ENGINE_MAP["mariadb"] == "mysql"


class TestParseInstance:
    def _make_instance(self, **overrides):
        base = {
            "DBInstanceIdentifier": "test-db",
            "Engine": "postgres",
            "DBInstanceStatus": "available",
            "Endpoint": {"Address": "test-db.abc.us-east-1.rds.amazonaws.com", "Port": 5432},
            "DBName": "mydb",
            "MasterUsername": "postgres",
            "DBInstanceClass": "db.r6g.xlarge",
            "TagList": [],
        }
        base.update(overrides)
        return base

    def test_basic_postgresql(self):
        instance = self._make_instance()
        member = _parse_instance(
            instance, region="us-east-1", engine_filter=None, name_pattern=None,
            password_env="PASS", default_user=None, default_group=None,
        )
        assert member is not None
        assert member.name == "test-db"
        assert member.engine == "postgresql"
        assert member.port == 5432
        assert member.instance_class == "db.r6g.xlarge"
        assert member.region == "us-east-1"
        assert member.group == "us-east-1"  # Auto-grouped by region

    def test_mysql_instance(self):
        instance = self._make_instance(Engine="mysql", Endpoint={"Address": "h1", "Port": 3306})
        member = _parse_instance(
            instance, region="us-west-2", engine_filter=None, name_pattern=None,
            password_env="PASS", default_user=None, default_group=None,
        )
        assert member is not None
        assert member.engine == "mysql"
        assert member.port == 3306

    def test_engine_filter(self):
        instance = self._make_instance(Engine="mysql")
        member = _parse_instance(
            instance, region="us-east-1", engine_filter="postgresql", name_pattern=None,
            password_env="PASS", default_user=None, default_group=None,
        )
        assert member is None  # Filtered out

    def test_name_pattern_match(self):
        instance = self._make_instance(DBInstanceIdentifier="prod-orders-db")
        member = _parse_instance(
            instance, region="us-east-1", engine_filter=None, name_pattern="prod-*",
            password_env="PASS", default_user=None, default_group=None,
        )
        assert member is not None
        assert member.name == "prod-orders-db"

    def test_name_pattern_no_match(self):
        instance = self._make_instance(DBInstanceIdentifier="staging-db")
        member = _parse_instance(
            instance, region="us-east-1", engine_filter=None, name_pattern="prod-*",
            password_env="PASS", default_user=None, default_group=None,
        )
        assert member is None

    def test_skip_unavailable(self):
        instance = self._make_instance(DBInstanceStatus="stopped")
        member = _parse_instance(
            instance, region="us-east-1", engine_filter=None, name_pattern=None,
            password_env="PASS", default_user=None, default_group=None,
        )
        assert member is None

    def test_skip_oracle(self):
        instance = self._make_instance(Engine="oracle-ee")
        member = _parse_instance(
            instance, region="us-east-1", engine_filter=None, name_pattern=None,
            password_env="PASS", default_user=None, default_group=None,
        )
        assert member is None

    def test_custom_user(self):
        instance = self._make_instance()
        member = _parse_instance(
            instance, region="us-east-1", engine_filter=None, name_pattern=None,
            password_env="PASS", default_user="monitoring", default_group=None,
        )
        assert member.user == "monitoring"

    def test_custom_group(self):
        instance = self._make_instance()
        member = _parse_instance(
            instance, region="us-east-1", engine_filter=None, name_pattern=None,
            password_env="PASS", default_user=None, default_group="production",
        )
        assert member.group == "production"

    def test_no_endpoint(self):
        instance = self._make_instance(Endpoint={})
        member = _parse_instance(
            instance, region="us-east-1", engine_filter=None, name_pattern=None,
            password_env="PASS", default_user=None, default_group=None,
        )
        assert member is None


class TestSecretsResolver:
    def test_extract_region_from_arn(self):
        from lib.fleet.secrets_resolver import _extract_region_from_arn

        assert _extract_region_from_arn(
            "arn:aws:secretsmanager:us-east-2:123456:secret:my-secret"
        ) == "us-east-2"
        assert _extract_region_from_arn("not-an-arn") is None

    def test_cache_behavior(self):
        from lib.fleet.secrets_resolver import _cache, clear_cache

        clear_cache()
        assert len(_cache) == 0

        # Simulate a cache entry
        import time
        _cache["test-arn"] = ("cached-value", time.time() + 3600)

        from lib.fleet.secrets_resolver import resolve_secret
        # Should return cached value without calling AWS
        result = resolve_secret("test-arn")
        assert result == "cached-value"

        clear_cache()


class TestAwsAuth:
    def test_detect_credentials_with_env(self):
        from lib.fleet.aws_auth import detect_aws_credentials
        import os

        old_key = os.environ.get("AWS_ACCESS_KEY_ID")
        old_secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
        try:
            os.environ["AWS_ACCESS_KEY_ID"] = "test"
            os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
            has_creds, msg = detect_aws_credentials()
            assert has_creds is True
            assert "environment" in msg.lower()
        finally:
            if old_key:
                os.environ["AWS_ACCESS_KEY_ID"] = old_key
            else:
                os.environ.pop("AWS_ACCESS_KEY_ID", None)
            if old_secret:
                os.environ["AWS_SECRET_ACCESS_KEY"] = old_secret
            else:
                os.environ.pop("AWS_SECRET_ACCESS_KEY", None)

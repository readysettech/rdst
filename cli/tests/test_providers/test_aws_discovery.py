"""Tests for AWS RDS discovery (mocked botocore)."""

from unittest.mock import MagicMock, patch

from features.providers.discovery import _parse_instance, _ENGINE_MAP, discover_aurora_cluster


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
        assert member.group is None  # Standalone: no fabricated region group

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


class TestAuroraClusterDiscovery:
    def _make_cluster(self, **overrides):
        base = {
            "DBClusterIdentifier": "prod-aurora",
            "Engine": "aurora-postgresql",
            "Status": "available",
            "Endpoint": "prod-aurora.cluster-abc.us-east-1.rds.amazonaws.com",
            "Port": 5432,
            "DatabaseName": "app",
            "MasterUsername": "postgres",
            "DBClusterMembers": [
                {"DBInstanceIdentifier": "prod-aurora-instance-1", "IsClusterWriter": True},
                {"DBInstanceIdentifier": "prod-aurora-reader-1", "IsClusterWriter": False},
                {"DBInstanceIdentifier": "prod-aurora-reader-2", "IsClusterWriter": False},
            ],
        }
        base.update(overrides)
        return base

    def _make_client(self):
        client = MagicMock()

        def describe_db_instances(Filters=None):
            reader_id = Filters[0]["Values"][0]
            return {
                "DBInstances": [
                    {
                        "DBInstanceIdentifier": reader_id,
                        "Endpoint": {
                            "Address": f"{reader_id}.abc.us-east-1.rds.amazonaws.com",
                            "Port": 5432,
                        },
                        "DBInstanceClass": "db.r6g.large",
                    }
                ]
            }

        client.describe_db_instances.side_effect = describe_db_instances
        return client

    def _discover(self, cluster=None, **kwargs):
        return discover_aurora_cluster(
            cluster=cluster or self._make_cluster(),
            client=self._make_client(),
            region="us-east-1",
            engine_filter=None,
            name_pattern=None,
            password_env="PASS",
            default_user=None,
            **kwargs,
        )

    def test_members_grouped_by_cluster_id_with_role_tags(self):
        members = self._discover()
        assert len(members) == 3  # 1 writer + 2 readers
        assert {m.group for m in members} == {"prod-aurora"}
        writer = next(m for m in members if m.name == "prod-aurora-writer")
        assert "role:writer" in writer.tags
        readers = [m for m in members if "role:reader" in m.tags]
        assert {m.name for m in readers} == {"prod-aurora-reader-1", "prod-aurora-reader-2"}

    def test_explicit_group_overrides_cluster_id(self):
        members = self._discover(default_group="production")
        assert {m.group for m in members} == {"production"}

    def test_standalone_instance_stays_ungrouped(self):
        instance = {
            "DBInstanceIdentifier": "standalone-db",
            "Engine": "postgres",
            "DBInstanceStatus": "available",
            "Endpoint": {"Address": "standalone-db.abc.us-east-1.rds.amazonaws.com", "Port": 5432},
            "DBName": "mydb",
            "MasterUsername": "postgres",
            "DBInstanceClass": "db.r6g.xlarge",
            "TagList": [],
        }
        member = _parse_instance(
            instance, region="us-east-1", engine_filter=None, name_pattern=None,
            password_env="PASS", default_user=None, default_group=None,
        )
        assert member.group is None

    def test_instance_with_cluster_identifier_groups_by_cluster(self):
        instance = {
            "DBInstanceIdentifier": "prod-aurora-instance-1",
            "Engine": "aurora-postgresql",
            "DBInstanceStatus": "available",
            "Endpoint": {
                "Address": "prod-aurora-instance-1.abc.us-east-1.rds.amazonaws.com",
                "Port": 5432,
            },
            "DBClusterIdentifier": "prod-aurora",
            "DBName": "app",
            "MasterUsername": "postgres",
            "DBInstanceClass": "db.r6g.large",
            "TagList": [],
        }
        member = _parse_instance(
            instance, region="us-east-1", engine_filter=None, name_pattern=None,
            password_env="PASS", default_user=None, default_group=None,
        )
        assert member.group == "prod-aurora"


class TestSecretsResolver:
    def test_extract_region_from_arn(self):
        from features.providers.secrets import _extract_region_from_arn

        assert _extract_region_from_arn(
            "arn:aws:secretsmanager:us-east-2:123456:secret:my-secret"
        ) == "us-east-2"
        assert _extract_region_from_arn("not-an-arn") is None

    def test_cache_behavior(self):
        from features.providers.secrets import _cache, clear_cache

        clear_cache()
        assert len(_cache) == 0

        # Simulate a cache entry
        import time
        _cache["test-arn"] = ("cached-value", time.time() + 3600)

        from features.providers.secrets import resolve_secret
        # Should return cached value without calling AWS
        result = resolve_secret("test-arn")
        assert result == "cached-value"

        clear_cache()


class TestAwsAuth:
    def test_detect_credentials_with_env(self):
        from features.providers.auth import detect_aws_credentials
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

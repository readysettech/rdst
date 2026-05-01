"""Tests for CacheService — TDD red-green cycles."""

import pytest
from unittest.mock import patch, MagicMock

from features.cache.events import (
    CacheAddEvent,
    CacheDeleteEvent,
    CacheDeployCompleteEvent,
    CacheDropAllEvent,
    CacheListEvent,
    CacheStatusEvent,
    CacheRunCompleteEvent,
)
from features.cache.models import CacheInput, CacheOptions
from shared.service_events import ErrorEvent, ProgressEvent


# ============================================================================
# Cycle 1: Types
# ============================================================================


class TestCacheTypes:
    def test_cache_input_required_fields(self):
        inp = CacheInput(target="mydb")
        assert inp.target == "mydb"
        assert inp.query is None
        assert inp.cache_id is None
        assert inp.tag is None

    def test_cache_input_all_fields(self):
        inp = CacheInput(target="mydb", query="SELECT 1", cache_id="q_abc", tag="slow")
        assert inp.query == "SELECT 1"
        assert inp.cache_id == "q_abc"
        assert inp.tag == "slow"

    def test_cache_options_defaults(self):
        opts = CacheOptions()
        assert opts.dry_run is False
        assert opts.mode == "docker"
        assert opts.port is None
        assert opts.deploy_config == "readyset"
        assert opts.json_output is False
        assert opts.yes is False

    def test_cache_status_event(self):
        evt = CacheStatusEvent(
            type="cache_status", deployed=True, running=True,
            endpoint="mysql://u@localhost:3307/db", cache_target="mydb-cache",
        )
        assert evt.type == "cache_status"
        assert evt.deployed is True
        assert evt.running is True
        assert evt.endpoint == "mysql://u@localhost:3307/db"

    def test_cache_status_event_not_deployed(self):
        evt = CacheStatusEvent(type="cache_status", deployed=False, running=False)
        assert evt.deployed is False
        assert evt.endpoint is None

    def test_cache_deploy_complete_event(self):
        evt = CacheDeployCompleteEvent(
            type="deploy_complete", success=True, deployed=True, running=True,
            endpoint="postgresql://u@localhost:5433/db", cache_target="mydb-cache",
            container_name="rdst-readyset-mydb",
        )
        assert evt.type == "deploy_complete"
        assert evt.success is True
        assert evt.cache_target == "mydb-cache"

    def test_cache_list_event(self):
        evt = CacheListEvent(
            type="cache_list", success=True,
            caches=[{"cache_id": "q1", "cache_name": "q_abc", "query": "SELECT 1"}],
            count=1,
        )
        assert evt.type == "cache_list"
        assert evt.count == 1
        assert evt.caches[0]["cache_name"] == "q_abc"

    def test_cache_add_event_supported(self):
        evt = CacheAddEvent(
            type="cache_add", success=True, supported=True,
            query="SELECT 1", query_hash="abc123def456",
        )
        assert evt.supported is True
        assert evt.query_hash == "abc123def456"

    def test_cache_add_event_not_supported(self):
        evt = CacheAddEvent(
            type="cache_add", success=True, supported=False,
            query="SELECT 1", detail="Unsupported: contains subquery",
        )
        assert evt.supported is False
        assert evt.detail is not None

    def test_cache_delete_event(self):
        evt = CacheDeleteEvent(type="cache_delete", success=True, cache_id="q_abc123")
        assert evt.type == "cache_delete"
        assert evt.cache_id == "q_abc123"

    def test_cache_drop_all_event(self):
        evt = CacheDropAllEvent(type="cache_drop_all", success=True, count=3)
        assert evt.type == "cache_drop_all"
        assert evt.count == 3


# ============================================================================
# Cycle 2: Target Resolution
# ============================================================================


class TestCacheTargetResolution:
    def test_resolve_direct_readyset_target(self):
        """If the target itself is already a readyset target, use it directly."""
        from features.cache.service import CacheService

        service = CacheService()
        with patch("features.cache.service.TargetsConfig") as MockConfig:
            mock_cfg = MockConfig.return_value
            # First call for "mydb-cache" itself returns a readyset config
            mock_cfg.get.return_value = {
                "target_type": "readyset",
                "upstream_target": "mydb",
                "host": "127.0.0.1",
                "port": 5433,
            }
            name, config = service._resolve_cache_target("mydb-cache")
            assert name == "mydb-cache"
            assert config["target_type"] == "readyset"

    def test_resolve_by_naming_convention(self):
        from features.cache.service import CacheService

        service = CacheService()
        cache_config = {
            "target_type": "readyset",
            "upstream_target": "mydb",
            "host": "127.0.0.1",
            "port": 5433,
        }
        with patch("features.cache.service.TargetsConfig") as MockConfig:
            mock_cfg = MockConfig.return_value
            # "mydb" is a database target, "mydb-cache" is the readyset target
            mock_cfg.get.side_effect = lambda name: cache_config if name == "mydb-cache" else None
            name, config = service._resolve_cache_target("mydb")
            assert name == "mydb-cache"
            assert config["target_type"] == "readyset"

    def test_resolve_not_found(self):
        from features.cache.service import CacheService

        service = CacheService()
        with patch("features.cache.service.TargetsConfig") as MockConfig:
            mock_cfg = MockConfig.return_value
            mock_cfg.get.return_value = None
            mock_cfg.list_targets.return_value = []
            result = service._resolve_cache_target("mydb")
            assert result is None

    def test_resolve_by_upstream_search(self):
        from features.cache.service import CacheService

        service = CacheService()
        with patch("features.cache.service.TargetsConfig") as MockConfig:
            mock_cfg = MockConfig.return_value
            # Convention name doesn't match
            mock_cfg.get.side_effect = lambda name: (
                {"target_type": "readyset", "upstream_target": "mydb", "port": 5433}
                if name == "prod-rs"
                else None
            )
            mock_cfg.list_targets.return_value = ["mydb", "prod-rs"]
            name, config = service._resolve_cache_target("mydb")
            assert name == "prod-rs"
            assert config["upstream_target"] == "mydb"

    def test_resolve_skips_non_readyset(self):
        from features.cache.service import CacheService

        service = CacheService()
        with patch("features.cache.service.TargetsConfig") as MockConfig:
            mock_cfg = MockConfig.return_value
            mock_cfg.get.return_value = None
            mock_cfg.list_targets.return_value = ["mydb", "other"]
            # "other" is a database target, not readyset
            def get_target(name):
                if name == "other":
                    return {"target_type": "database", "upstream_target": "mydb"}
                return None
            mock_cfg.get.side_effect = get_target
            result = service._resolve_cache_target("mydb")
            assert result is None


# ============================================================================
# Cycle 3: Status
# ============================================================================


class TestCacheStatus:
    @pytest.mark.asyncio
    async def test_status_not_deployed(self):
        from features.cache.service import CacheService

        service = CacheService()
        with patch.object(service, "_resolve_cache_target", return_value=None):
            events = [e async for e in service.get_status(CacheInput(target="mydb"))]
        assert len(events) == 1
        assert events[0].type == "cache_status"
        assert events[0].deployed is False
        assert events[0].running is False

    @pytest.mark.asyncio
    async def test_status_deployed_running(self):
        from features.cache.service import CacheService

        service = CacheService()
        cache_config = {
            "target_type": "readyset", "engine": "postgresql",
            "host": "127.0.0.1", "port": 5433, "user": "admin",
            "database": "myapp", "container_name": "rdst-readyset-mydb",
        }
        with patch.object(service, "_resolve_cache_target", return_value=("mydb-cache", cache_config)):
            with patch.object(service, "_check_cache_reachable", return_value=True):
                events = [e async for e in service.get_status(CacheInput(target="mydb"))]
        evt = events[-1]
        assert evt.type == "cache_status"
        assert evt.deployed is True
        assert evt.running is True
        assert evt.endpoint is not None
        assert "5433" in evt.endpoint
        assert evt.cache_target == "mydb-cache"

    @pytest.mark.asyncio
    async def test_status_deployed_stopped(self):
        from features.cache.service import CacheService

        service = CacheService()
        cache_config = {
            "target_type": "readyset", "engine": "mysql",
            "host": "127.0.0.1", "port": 3307, "user": "root",
            "database": "mydb", "container_name": "rdst-readyset-mydb",
        }
        with patch.object(service, "_resolve_cache_target", return_value=("mydb-cache", cache_config)):
            with patch.object(service, "_check_cache_reachable", return_value=False):
                events = [e async for e in service.get_status(CacheInput(target="mydb"))]
        evt = events[-1]
        assert evt.deployed is True
        assert evt.running is False


# ============================================================================
# Cycle 4: List Caches
# ============================================================================


class TestCacheList:
    @pytest.mark.asyncio
    async def test_list_no_cache_target(self):
        from features.cache.service import CacheService

        service = CacheService()
        with patch.object(service, "_resolve_cache_target", return_value=None):
            events = [e async for e in service.list_caches(CacheInput(target="mydb"))]
        assert events[-1].type == "error"

    @pytest.mark.asyncio
    async def test_list_empty(self):
        from features.cache.service import CacheService

        service = CacheService()
        cache_config = {
            "target_type": "readyset", "engine": "postgresql",
            "host": "127.0.0.1", "port": 5433, "user": "admin",
            "database": "myapp",
        }
        with patch.object(service, "_resolve_cache_target", return_value=("mydb-cache", cache_config)):
            with patch.object(service, "_run_readyset_sql", return_value={"success": True, "output": ""}):
                events = [e async for e in service.list_caches(CacheInput(target="mydb"))]
        evt = events[-1]
        assert evt.type == "cache_list"
        assert evt.count == 0
        assert evt.caches == []

    @pytest.mark.asyncio
    async def test_list_with_entries(self):
        from features.cache.service import CacheService

        service = CacheService()
        cache_config = {
            "target_type": "readyset", "engine": "postgresql",
            "host": "127.0.0.1", "port": 5433, "user": "admin",
            "database": "myapp",
        }
        show_output = (
            "q_id1\tq_abc123\tSELECT * FROM users\tshallow, ttl 10000 ms\t5\n"
            "q_id2\tq_def456\tSELECT id FROM posts\tshallow, ttl 5000 ms\t2"
        )
        with patch.object(service, "_resolve_cache_target", return_value=("mydb-cache", cache_config)):
            with patch.object(service, "_run_readyset_sql", return_value={"success": True, "output": show_output}):
                events = [e async for e in service.list_caches(CacheInput(target="mydb"))]
        evt = events[-1]
        assert evt.type == "cache_list"
        assert evt.count == 2
        assert evt.caches[0]["cache_name"] == "q_abc123"
        assert evt.caches[1]["cache_name"] == "q_def456"


# ============================================================================
# Cycle 5: Add Cache
# ============================================================================


class TestCacheAdd:
    @pytest.mark.asyncio
    async def test_add_dry_run_supported(self):
        from features.cache.service import CacheService

        service = CacheService()
        cache_config = {
            "target_type": "readyset", "engine": "postgresql",
            "host": "127.0.0.1", "port": 5433, "user": "admin",
            "database": "myapp",
        }
        with patch.object(service, "_resolve_cache_target", return_value=("mydb-cache", cache_config)):
            with patch("features.cache.readyset_cacheability.check_readyset_cacheability", return_value={"cacheable": True}):
                with patch.object(service, "_run_readyset_sql", return_value={"success": True, "output": "yes, supported"}):
                    events = [e async for e in service.add_cache(
                        CacheInput(target="mydb", query="SELECT 1"),
                        CacheOptions(dry_run=True),
                    )]
        add_evts = [e for e in events if hasattr(e, "type") and e.type == "cache_add"]
        assert len(add_evts) == 1
        assert add_evts[0].supported is True

    @pytest.mark.asyncio
    async def test_add_dry_run_not_supported(self):
        from features.cache.service import CacheService

        service = CacheService()
        cache_config = {
            "target_type": "readyset", "engine": "postgresql",
            "host": "127.0.0.1", "port": 5433, "user": "admin",
            "database": "myapp",
        }
        with patch.object(service, "_resolve_cache_target", return_value=("mydb-cache", cache_config)):
            with patch("features.cache.readyset_cacheability.check_readyset_cacheability", return_value={"cacheable": True}):
                with patch.object(service, "_run_readyset_sql", return_value={"success": True, "output": "unsupported: subquery"}):
                    events = [e async for e in service.add_cache(
                        CacheInput(target="mydb", query="SELECT (SELECT 1)"),
                        CacheOptions(dry_run=True),
                    )]
        add_evts = [e for e in events if hasattr(e, "type") and e.type == "cache_add"]
        assert len(add_evts) == 1
        assert add_evts[0].supported is False

    @pytest.mark.asyncio
    async def test_add_creates_cache(self):
        from features.cache.service import CacheService

        service = CacheService()
        cache_config = {
            "target_type": "readyset", "engine": "postgresql",
            "host": "127.0.0.1", "port": 5433, "user": "admin",
            "database": "myapp",
        }
        with patch.object(service, "_resolve_cache_target", return_value=("mydb-cache", cache_config)):
            with patch("features.cache.readyset_cacheability.check_readyset_cacheability", return_value={"cacheable": True}):
                with patch.object(service, "_run_readyset_sql", return_value={"success": True, "output": "yes"}):
                    with patch.object(service, "_save_to_registry", return_value="abc123def456"):
                        events = [e async for e in service.add_cache(
                            CacheInput(target="mydb", query="SELECT 1"),
                            CacheOptions(dry_run=False),
                        )]
        add_evts = [e for e in events if hasattr(e, "type") and e.type == "cache_add"]
        assert len(add_evts) == 1
        assert add_evts[0].success is True
        assert add_evts[0].query_hash == "abc123def456"

    @pytest.mark.asyncio
    async def test_add_static_check_fails(self):
        from features.cache.service import CacheService

        service = CacheService()
        cache_config = {
            "target_type": "readyset", "engine": "postgresql",
            "host": "127.0.0.1", "port": 5433, "user": "admin",
            "database": "myapp",
        }
        with patch.object(service, "_resolve_cache_target", return_value=("mydb-cache", cache_config)):
            with patch("features.cache.readyset_cacheability.check_readyset_cacheability", return_value={"cacheable": False, "issues": ["Uses window function"]}):
                events = [e async for e in service.add_cache(
                    CacheInput(target="mydb", query="SELECT ROW_NUMBER() OVER()"),
                    CacheOptions(dry_run=True),
                )]
        error_evts = [e for e in events if hasattr(e, "type") and e.type == "error"]
        assert len(error_evts) == 1
        assert "window function" in error_evts[0].message.lower()

    @pytest.mark.asyncio
    async def test_add_no_cache_target(self):
        from features.cache.service import CacheService

        service = CacheService()
        with patch.object(service, "_resolve_cache_target", return_value=None):
            events = [e async for e in service.add_cache(
                CacheInput(target="mydb", query="SELECT 1"),
                CacheOptions(dry_run=True),
            )]
        assert events[-1].type == "error"


# ============================================================================
# Cycle 6: Delete / Drop All
# ============================================================================


class TestCacheDelete:
    @pytest.mark.asyncio
    async def test_delete_success(self):
        from features.cache.service import CacheService

        service = CacheService()
        cache_config = {
            "target_type": "readyset", "engine": "postgresql",
            "host": "127.0.0.1", "port": 5433, "user": "admin",
            "database": "myapp",
        }
        with patch.object(service, "_resolve_cache_target", return_value=("mydb-cache", cache_config)):
            with patch.object(service, "_run_readyset_sql", return_value={"success": True, "output": ""}):
                events = [e async for e in service.delete_cache(
                    CacheInput(target="mydb", cache_id="q_abc123"),
                )]
        assert events[-1].type == "cache_delete"
        assert events[-1].success is True
        assert events[-1].cache_id == "q_abc123"

    @pytest.mark.asyncio
    async def test_delete_invalid_id(self):
        from features.cache.service import CacheService

        service = CacheService()
        cache_config = {
            "target_type": "readyset", "engine": "postgresql",
            "host": "127.0.0.1", "port": 5433, "user": "admin",
            "database": "myapp",
        }
        with patch.object(service, "_resolve_cache_target", return_value=("mydb-cache", cache_config)):
            events = [e async for e in service.delete_cache(
                CacheInput(target="mydb", cache_id="'; DROP TABLE users; --"),
            )]
        assert events[-1].type == "error"


class TestCacheDropAll:
    @pytest.mark.asyncio
    async def test_drop_all_success(self):
        from features.cache.service import CacheService

        service = CacheService()
        cache_config = {
            "target_type": "readyset", "engine": "postgresql",
            "host": "127.0.0.1", "port": 5433, "user": "admin",
            "database": "myapp",
        }
        show_output = "q_id1\tq_abc\tSELECT 1\tshallow\t5"
        with patch.object(service, "_resolve_cache_target", return_value=("mydb-cache", cache_config)):
            with patch.object(service, "_run_readyset_sql") as mock_sql:
                mock_sql.side_effect = [
                    {"success": True, "output": show_output},  # SHOW CACHES
                    {"success": True, "output": ""},            # DROP ALL CACHES
                ]
                events = [e async for e in service.drop_all(CacheInput(target="mydb"))]
        assert events[-1].type == "cache_drop_all"
        assert events[-1].success is True
        assert events[-1].count == 1

    @pytest.mark.asyncio
    async def test_drop_all_empty(self):
        from features.cache.service import CacheService

        service = CacheService()
        cache_config = {
            "target_type": "readyset", "engine": "postgresql",
            "host": "127.0.0.1", "port": 5433, "user": "admin",
            "database": "myapp",
        }
        with patch.object(service, "_resolve_cache_target", return_value=("mydb-cache", cache_config)):
            with patch.object(service, "_run_readyset_sql", return_value={"success": True, "output": ""}):
                events = [e async for e in service.drop_all(CacheInput(target="mydb"))]
        assert events[-1].type == "cache_drop_all"
        assert events[-1].count == 0


# ============================================================================
# Cycle 8: run_comparison — origin vs cache performance
# ============================================================================


class TestCacheRunComparison:

    @pytest.mark.asyncio
    async def test_run_comparison_success(self):
        from features.cache.service import CacheService

        service = CacheService()
        origin_config = {
            "engine": "postgresql", "host": "db.host", "port": 5432,
            "user": "admin", "database": "myapp",
        }
        cache_config = {
            "target_type": "readyset", "engine": "postgresql",
            "host": "127.0.0.1", "port": 5433, "user": "admin",
            "database": "myapp",
        }
        comparison_result = {
            "success": True,
            "query": "SELECT 1",
            "iterations": 5,
            "original": {
                "host": "db.host", "port": 5432,
                "stats": {"mean": 20.0, "median": 19.0, "min": 15.0, "max": 30.0, "p50": 19.0, "p95": 28.0, "p99": 30.0, "stddev": 5.0},
                "times": [15.0, 18.0, 19.0, 20.0, 30.0],
            },
            "readyset": {
                "host": "127.0.0.1", "port": 5433,
                "stats": {"mean": 1.0, "median": 0.9, "min": 0.5, "max": 2.0, "p50": 0.9, "p95": 1.8, "p99": 2.0, "stddev": 0.5},
                "times": [0.5, 0.8, 0.9, 1.0, 2.0],
            },
            "speedup": {"mean": 20.0, "median": 21.1, "improvement_pct": 1900.0},
            "winner": "readyset",
        }

        with patch("features.cache.service.TargetsConfig") as MockConfig:
            MockConfig.return_value.load.return_value = None
            MockConfig.return_value.get.return_value = origin_config
            with patch.object(service, "_resolve_cache_target", return_value=("mydb-cache", cache_config)):
                with patch("features.cache.service.resolve_password_value", return_value="secret"):
                    with patch("features.cache.performance_comparison.run_comparison", return_value=comparison_result):
                        events = [e async for e in service.run_comparison(
                            CacheInput(target="mydb", query="SELECT 1"), iterations=5, warmup=2,
                        )]

        # Should have progress events then a complete event
        assert any(isinstance(e, ProgressEvent) for e in events)
        final = events[-1]
        assert isinstance(final, CacheRunCompleteEvent)
        assert final.success is True
        assert final.speedup_mean == 20.0
        assert final.winner == "readyset"
        assert final.origin_stats["mean"] == 20.0
        assert final.cache_stats["mean"] == 1.0

    @pytest.mark.asyncio
    async def test_run_comparison_no_cache_target(self):
        from features.cache.service import CacheService

        service = CacheService()
        origin_config = {
            "engine": "postgresql", "host": "db.host", "port": 5432,
            "user": "admin", "database": "myapp",
        }

        with patch("features.cache.service.TargetsConfig") as MockConfig:
            MockConfig.return_value.load.return_value = None
            MockConfig.return_value.get.return_value = origin_config
            with patch.object(service, "_resolve_cache_target", return_value=None):
                events = [e async for e in service.run_comparison(
                    CacheInput(target="mydb", query="SELECT 1"),
                )]

        final = events[-1]
        assert isinstance(final, ErrorEvent)
        assert "No cache target" in final.message

    @pytest.mark.asyncio
    async def test_run_comparison_no_query(self):
        from features.cache.service import CacheService

        service = CacheService()
        events = [e async for e in service.run_comparison(
            CacheInput(target="mydb"),
        )]

        final = events[-1]
        assert isinstance(final, ErrorEvent)
        assert "Query is required" in final.message

    @pytest.mark.asyncio
    async def test_run_comparison_failure(self):
        from features.cache.service import CacheService

        service = CacheService()
        origin_config = {
            "engine": "postgresql", "host": "db.host", "port": 5432,
            "user": "admin", "database": "myapp",
        }
        cache_config = {
            "target_type": "readyset", "engine": "postgresql",
            "host": "127.0.0.1", "port": 5433, "user": "admin",
            "database": "myapp",
        }
        failed_result = {"success": False, "error": "All original database queries failed"}

        with patch("features.cache.service.TargetsConfig") as MockConfig:
            MockConfig.return_value.load.return_value = None
            MockConfig.return_value.get.return_value = origin_config
            with patch.object(service, "_resolve_cache_target", return_value=("mydb-cache", cache_config)):
                with patch("features.cache.service.resolve_password_value", return_value="secret"):
                    with patch("features.cache.performance_comparison.run_comparison", return_value=failed_result):
                        events = [e async for e in service.run_comparison(
                            CacheInput(target="mydb", query="SELECT 1"),
                        )]

        final = events[-1]
        assert isinstance(final, ErrorEvent)
        assert "All original database queries failed" in final.message


# ============================================================================
# Cycle 9: deploy — mode routing and target registration
# ============================================================================


class TestDeployNonDocker:

    @pytest.mark.asyncio
    async def test_deploy_kubernetes_registers_with_cluster_host(self):
        """K8s deploy auto-registers with in-cluster DNS host."""
        from features.cache.service import CacheService

        service = CacheService()
        target_config = {
            "engine": "postgresql", "host": "db.host", "port": 5432,
            "user": "admin", "database": "myapp", "password_env": "DB_PASS",
        }
        k8s_result = {
            "success": True, "namespace": "readyset",
            "deployment": "readyset-cache-mydb", "service": "readyset-cache-mydb",
            "port": "5433", "rollout_ready": True,
        }

        with patch("features.cache.service.TargetsConfig") as MockConfig:
            MockConfig.return_value.load.return_value = None
            MockConfig.return_value.get.return_value = target_config
            with patch("features.cache.service.resolve_password_value", return_value="secret"):
                with patch("shared.deploy.script_generator.build_variables", return_value={"db_engine": "postgresql", "readyset_port": 5433, "db_user": "admin", "db_name": "myapp", "container_name": "rs-mydb"}):
                    with patch("shared.deploy.kubernetes.deploy_kubernetes", return_value=k8s_result):
                        with patch.object(service, "_register_cache_target", return_value="mydb-cache") as mock_register:
                            events = [e async for e in service.deploy(
                                CacheInput(target="mydb"),
                                CacheOptions(mode="kubernetes", namespace="readyset"),
                            )]

        final = events[-1]
        assert isinstance(final, CacheDeployCompleteEvent)
        assert final.success is True
        assert final.cache_target == "mydb-cache"
        assert final.endpoint is None  # Non-local: user must provide endpoint
        mock_register.assert_called_once()

    @pytest.mark.asyncio
    async def test_deploy_systemd_registers_local_target(self):
        from features.cache.service import CacheService

        service = CacheService()
        target_config = {
            "engine": "postgresql", "host": "db.host", "port": 5432,
            "user": "admin", "database": "myapp", "password_env": "DB_PASS",
        }
        systemd_result = {"success": True, "service_name": "readyset-cache-mydb"}

        with patch("features.cache.service.TargetsConfig") as MockConfig:
            MockConfig.return_value.load.return_value = None
            MockConfig.return_value.get.return_value = target_config
            with patch("features.cache.service.resolve_password_value", return_value="secret"):
                with patch("shared.deploy.script_generator.build_variables", return_value={"db_engine": "postgresql", "readyset_port": 5433, "db_user": "admin", "db_name": "myapp", "container_name": "rs-mydb"}):
                    with patch("shared.deploy.local_systemd.deploy_local_systemd", return_value=systemd_result):
                        with patch.object(service, "_register_cache_target", return_value="mydb-cache") as mock_register:
                            events = [e async for e in service.deploy(
                                CacheInput(target="mydb"),
                                CacheOptions(mode="systemd"),
                            )]

        final = events[-1]
        assert isinstance(final, CacheDeployCompleteEvent)
        assert final.success is True
        assert final.cache_target == "mydb-cache"
        assert final.endpoint == "postgresql://admin@127.0.0.1:5433/myapp"
        mock_register.assert_called_once_with(
            "mydb", target_config,
            {"db_engine": "postgresql", "readyset_port": 5433, "db_user": "admin", "db_name": "myapp", "container_name": "rs-mydb"},
            "127.0.0.1",
        )

    @pytest.mark.asyncio
    async def test_deploy_remote_registers_with_ssh_host(self):
        """Remote/SSH deploy auto-registers with the SSH host."""
        from features.cache.service import CacheService

        service = CacheService()
        target_config = {
            "engine": "postgresql", "host": "db.host", "port": 5432,
            "user": "admin", "database": "myapp", "password_env": "DB_PASS",
        }
        remote_result = {"success": True, "returncode": 0, "output": "Deployed OK"}

        with patch("features.cache.service.TargetsConfig") as MockConfig:
            MockConfig.return_value.load.return_value = None
            MockConfig.return_value.get.return_value = target_config
            with patch("features.cache.service.resolve_password_value", return_value="secret"):
                with patch("shared.deploy.script_generator.build_variables", return_value={"db_engine": "postgresql", "readyset_port": 5433, "db_user": "admin", "db_name": "myapp", "container_name": "rs-mydb"}):
                    with patch("shared.deploy.remote.deploy_remote", return_value=remote_result):
                        with patch.object(service, "_register_cache_target", return_value="mydb-cache") as mock_register:
                            events = [e async for e in service.deploy(
                                CacheInput(target="mydb"),
                                CacheOptions(mode="systemd", host="remote-host", ssh_user="root"),
                            )]

        final = events[-1]
        assert isinstance(final, CacheDeployCompleteEvent)
        assert final.success is True
        assert final.cache_target == "mydb-cache"
        assert final.endpoint is None  # Non-local: user must provide endpoint
        mock_register.assert_called_once()

    @pytest.mark.asyncio
    async def test_deploy_docker_registers_target(self):
        """Regression: Docker deploy still auto-registers."""
        from features.cache.service import CacheService

        service = CacheService()
        target_config = {
            "engine": "postgresql", "host": "db.host", "port": 5432,
            "user": "admin", "database": "myapp", "password_env": "DB_PASS",
        }
        docker_result = {"success": True, "container_name": "rs-mydb"}

        with patch("features.cache.service.TargetsConfig") as MockConfig:
            MockConfig.return_value.load.return_value = None
            MockConfig.return_value.get.return_value = target_config
            with patch("features.cache.service.resolve_password_value", return_value="secret"):
                with patch("shared.deploy.script_generator.build_variables", return_value={"db_engine": "postgresql", "readyset_port": 5433, "db_user": "admin", "db_name": "myapp", "container_name": "rs-mydb"}):
                    with patch("shared.deploy.local_docker.deploy_local_docker", return_value=docker_result):
                        with patch.object(service, "_register_cache_target", return_value="mydb-cache") as mock_register:
                            events = [e async for e in service.deploy(
                                CacheInput(target="mydb"),
                                CacheOptions(mode="docker"),
                            )]

        final = events[-1]
        assert isinstance(final, CacheDeployCompleteEvent)
        assert final.success is True
        assert final.cache_target == "mydb-cache"  # Auto-registered
        assert final.endpoint is not None
        mock_register.assert_called_once()


# ============================================================================
# CacheOptions — new fields
# ============================================================================


class TestCacheOptionsExtended:
    def test_new_fields_default_none(self):
        opts = CacheOptions()
        assert opts.namespace is None
        assert opts.kubeconfig is None
        assert opts.host is None
        assert opts.ssh_key is None
        assert opts.ssh_user is None

    def test_new_fields_set(self):
        opts = CacheOptions(
            mode="kubernetes",
            namespace="prod",
            kubeconfig="/home/user/.kube/config",
            deploy_config="readyset-squeepy",
        )
        assert opts.mode == "kubernetes"
        assert opts.namespace == "prod"
        assert opts.kubeconfig == "/home/user/.kube/config"
        assert opts.deploy_config == "readyset-squeepy"

    def test_remote_fields(self):
        opts = CacheOptions(
            mode="systemd",
            host="10.0.0.5",
            ssh_key="/home/user/.ssh/id_rsa",
            ssh_user="deploy",
        )
        assert opts.host == "10.0.0.5"
        assert opts.ssh_key == "/home/user/.ssh/id_rsa"
        assert opts.ssh_user == "deploy"


class TestResolveCacheIdForDrop:
    """CacheService._resolve_cache_id_for_drop translates RDST registry hashes
    to ReadySet's q_<hash> format so DROP CACHE targets the correct cache.
    Fixes CLD-1754 (DROP CACHE was passing the registry hash to ReadySet,
    which doesn't recognize it).
    """

    def test_q_prefix_returned_as_is(self):
        from features.cache.service import CacheService
        assert CacheService._resolve_cache_id_for_drop("q_abc123def456") == "q_abc123def456"

    def test_long_q_prefix(self):
        from features.cache.service import CacheService
        assert CacheService._resolve_cache_id_for_drop("q_13b0714e3f57aa57") == "q_13b0714e3f57aa57"

    def test_invalid_format_returns_none(self):
        from features.cache.service import CacheService
        assert CacheService._resolve_cache_id_for_drop("garbage_123") is None
        assert CacheService._resolve_cache_id_for_drop("foo bar") is None

    def test_empty_returns_none(self):
        from features.cache.service import CacheService
        assert CacheService._resolve_cache_id_for_drop("") is None
        assert CacheService._resolve_cache_id_for_drop(None) is None

    def test_unknown_hex_returns_none(self):
        from features.cache.service import CacheService
        # Looks like our hash, but not in registry
        assert CacheService._resolve_cache_id_for_drop("a1b2c3d4") is None

    def test_known_hex_translated_to_q_id(self, tmp_path, monkeypatch):
        from features.cache.service import CacheService
        from shared.query_registry.query_registry import QueryRegistry
        registry_path = tmp_path / "queries.toml"
        reg = QueryRegistry(registry_path=str(registry_path))
        reg.load()
        h, _ = reg.add_query(sql="SELECT 1", source="manual", target="db1")
        reg.update_readyset_identity(query_hash=h, readyset_query_id="q_translated")
        import shared.constants
        monkeypatch.setattr(shared.constants, "RDST_DATA_DIR", tmp_path)
        result = CacheService._resolve_cache_id_for_drop(h)
        assert result == "q_translated"

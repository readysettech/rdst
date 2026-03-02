"""
Unit tests for SchemaService.

Tests the semantic layer management service including schema fetching,
status checking, and export functionality.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import Any, Dict, List

# Import from lib package (conftest.py adds rdst root to path)
from lib.services.types import (
    SchemaStatus,
    SchemaDetails,
    SchemaTable,
    SchemaTableColumn,
    SchemaTableRelationship,
    SchemaTerminology,
    SchemaExtension,
    SchemaCustomType,
    SchemaMetric,
    SchemaTargetSummary,
    SchemaTargetList,
    SchemaInitOptions,
    SchemaInitResult,
    SchemaExportResult,
    SchemaDeleteResult,
    SchemaUpdateResult,
)
from lib.services.schema_service import SchemaService


class TestSchemaServiceInit:
    """Tests for SchemaService initialization."""

    def test_initialization(self):
        """Test service initializes correctly."""
        with patch("lib.services.schema_service.SemanticLayerManager"):
            service = SchemaService()
            assert service is not None

    def test_has_required_methods(self):
        """Test service has required methods."""
        with patch("lib.services.schema_service.SemanticLayerManager"):
            service = SchemaService()
            assert hasattr(service, "get_status")
            assert hasattr(service, "get_schema")
            assert hasattr(service, "list_targets")
            assert hasattr(service, "init")
            assert hasattr(service, "delete")
            assert hasattr(service, "export")
            assert hasattr(service, "add_table")
            assert hasattr(service, "add_terminology")


class TestSchemaServiceGetStatus:
    """Tests for get_status() method."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock SemanticLayerManager."""
        manager = Mock()
        return manager

    @pytest.fixture
    def service(self, mock_manager):
        """Create SchemaService instance with mocked manager."""
        with patch(
            "lib.services.schema_service.SemanticLayerManager",
            return_value=mock_manager,
        ):
            svc = SchemaService()
            return svc

    def test_returns_schema_status_when_exists(self, service):
        """Test get_status returns SchemaStatus when layer exists."""
        service._manager.exists.return_value = True
        service._manager.get_summary.return_value = {
            "tables": 5,
            "columns": 25,
            "relationships": 3,
            "terminology": 10,
            "updated_at": "2024-01-01T00:00:00Z",
        }

        status = service.get_status("test-target")

        assert isinstance(status, SchemaStatus)
        assert status.target == "test-target"
        assert status.exists is True
        assert status.tables == 5
        assert status.columns == 25
        assert status.relationships == 3
        assert status.terminology == 10

    def test_returns_empty_status_when_not_exists(self, service):
        """Test get_status when semantic layer doesn't exist."""
        service._manager.exists.return_value = False

        status = service.get_status("test-target")

        assert isinstance(status, SchemaStatus)
        assert status.exists is False
        assert status.tables == 0
        assert status.columns == 0


class TestSchemaServiceGetSchema:
    """Tests for get_schema() method."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock SemanticLayerManager."""
        return Mock()

    @pytest.fixture
    def service(self, mock_manager):
        """Create SchemaService instance with mocked manager."""
        with patch(
            "lib.services.schema_service.SemanticLayerManager",
            return_value=mock_manager,
        ):
            svc = SchemaService()
            return svc

    @pytest.fixture
    def mock_layer(self):
        """Create mock SemanticLayer."""
        layer = Mock()

        # Mock a table with columns
        mock_column = Mock()
        mock_column.data_type = "integer"
        mock_column.description = "User ID"
        mock_column.unit = None
        mock_column.is_pii = False
        mock_column.enum_values = None

        mock_table = Mock()
        mock_table.description = "User accounts"
        mock_table.business_context = "Core user data"
        mock_table.row_estimate = "1M"
        mock_table.columns = {"id": mock_column}
        mock_table.relationships = []

        layer.tables = {"users": mock_table}
        layer.terminology = {}
        layer.metrics = {}
        layer.extensions = {}
        layer.custom_types = {}

        return layer

    def test_returns_none_when_not_exists(self, service):
        """Test get_schema returns None when layer doesn't exist."""
        service._manager.exists.return_value = False

        result = service.get_schema("nonexistent")

        assert result is None

    def test_returns_schema_details(self, service, mock_layer):
        """Test get_schema returns SchemaDetails."""
        service._manager.exists.return_value = True
        service._manager.load.return_value = mock_layer

        result = service.get_schema("test-target")

        assert isinstance(result, SchemaDetails)
        assert result.target == "test-target"
        assert len(result.tables) == 1
        assert result.tables[0].name == "users"

    def test_filters_single_table(self, service, mock_layer):
        """Test get_schema with specific table_name."""
        service._manager.exists.return_value = True
        service._manager.load.return_value = mock_layer

        result = service.get_schema("test-target", table_name="users")

        assert result is not None
        assert len(result.tables) == 1
        assert result.tables[0].name == "users"

    def test_returns_none_for_nonexistent_table(self, service, mock_layer):
        """Test get_schema returns None for nonexistent table."""
        service._manager.exists.return_value = True
        service._manager.load.return_value = mock_layer

        result = service.get_schema("test-target", table_name="nonexistent")

        assert result is None


class TestSchemaServiceListTargets:
    """Tests for list_targets() method."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock SemanticLayerManager."""
        return Mock()

    @pytest.fixture
    def service(self, mock_manager):
        """Create SchemaService instance with mocked manager."""
        with patch(
            "lib.services.schema_service.SemanticLayerManager",
            return_value=mock_manager,
        ):
            svc = SchemaService()
            return svc

    def test_returns_target_list(self, service):
        """Test list_targets returns SchemaTargetList."""
        service._manager.list_targets.return_value = ["prod", "staging"]
        service._manager.get_summary.side_effect = [
            {"tables": 10, "terminology": 5, "updated_at": "2024-01-01"},
            {"tables": 8, "terminology": 3, "updated_at": "2024-01-02"},
        ]

        result = service.list_targets()

        assert isinstance(result, SchemaTargetList)
        assert len(result.targets) == 2
        assert result.targets[0].name == "prod"
        assert result.targets[0].tables == 10

    def test_returns_empty_list_when_no_targets(self, service):
        """Test list_targets with no configured targets."""
        service._manager.list_targets.return_value = []

        result = service.list_targets()

        assert isinstance(result, SchemaTargetList)
        assert len(result.targets) == 0


class TestSchemaServiceInitMethod:
    """Tests for init() method."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock SemanticLayerManager."""
        return Mock()

    @pytest.fixture
    def service(self, mock_manager):
        """Create SchemaService instance with mocked manager."""
        with patch(
            "lib.services.schema_service.SemanticLayerManager",
            return_value=mock_manager,
        ):
            svc = SchemaService()
            return svc

    def test_returns_error_if_exists_and_no_force(self, service):
        """Test init returns error when layer exists and force=False."""
        service._manager.exists.return_value = True

        result = service.init(
            "test-target",
            {"engine": "postgresql"},
            SchemaInitOptions(force=False),
        )

        assert isinstance(result, SchemaInitResult)
        assert result.success is False
        assert "already exists" in result.error

    def test_succeeds_with_force_flag(self, service):
        """Test init succeeds when layer exists but force=True."""
        service._manager.exists.return_value = True

        # Mock introspector
        mock_layer = Mock()
        mock_layer.tables = {"users": Mock(columns={"id": Mock()}, relationships=[])}

        with patch(
            "lib.services.schema_service.SchemaIntrospector"
        ) as MockIntrospector:
            MockIntrospector.return_value.introspect.return_value = mock_layer
            service._manager.get_path.return_value = "/path/to/layer"

            result = service.init(
                "test-target",
                {"engine": "postgresql"},
                SchemaInitOptions(force=True),
            )

        assert result.success is True

    def test_handles_connection_error(self, service):
        """Test init handles connection errors."""
        service._manager.exists.return_value = False

        with patch(
            "lib.services.schema_service.SchemaIntrospector"
        ) as MockIntrospector:
            MockIntrospector.return_value.introspect.side_effect = ConnectionError(
                "Could not connect"
            )

            result = service.init("test-target", {"engine": "postgresql"})

        assert result.success is False
        assert "connection failed" in result.error.lower()


class TestSchemaServiceExport:
    """Tests for export() method."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock SemanticLayerManager."""
        return Mock()

    @pytest.fixture
    def service(self, mock_manager):
        """Create SchemaService instance with mocked manager."""
        with patch(
            "lib.services.schema_service.SemanticLayerManager",
            return_value=mock_manager,
        ):
            svc = SchemaService()
            return svc

    def test_export_yaml(self, service):
        """Test exporting schema as YAML."""
        service._manager.exists.return_value = True
        service._manager.export_yaml.return_value = "tables:\n  - name: users"

        result = service.export("test-target", format="yaml")

        assert isinstance(result, SchemaExportResult)
        assert result.success is True
        assert result.format == "yaml"
        assert "tables:" in result.content

    def test_export_json(self, service):
        """Test exporting schema as JSON."""
        service._manager.exists.return_value = True
        mock_layer = Mock()
        mock_layer.to_dict.return_value = {"tables": []}
        service._manager.load.return_value = mock_layer

        result = service.export("test-target", format="json")

        assert result.success is True
        assert result.format == "json"
        assert "tables" in result.content

    def test_export_error_when_not_exists(self, service):
        """Test export returns error when layer doesn't exist."""
        service._manager.exists.return_value = False

        result = service.export("nonexistent")

        assert result.success is False
        assert "No semantic layer found" in result.error

    def test_export_unknown_format(self, service):
        """Test export returns error for unknown format."""
        service._manager.exists.return_value = True

        result = service.export("test-target", format="xml")

        assert result.success is False
        assert "Unknown format" in result.error


class TestSchemaServiceDelete:
    """Tests for delete() method."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock SemanticLayerManager."""
        return Mock()

    @pytest.fixture
    def service(self, mock_manager):
        """Create SchemaService instance with mocked manager."""
        with patch(
            "lib.services.schema_service.SemanticLayerManager",
            return_value=mock_manager,
        ):
            svc = SchemaService()
            return svc

    def test_delete_success(self, service):
        """Test delete_schema returns success."""
        service._manager.exists.return_value = True
        service._manager.delete.return_value = True

        result = service.delete("test-target")

        assert isinstance(result, SchemaDeleteResult)
        assert result.success is True

    def test_delete_error_when_not_exists(self, service):
        """Test delete returns error when layer doesn't exist."""
        service._manager.exists.return_value = False

        result = service.delete("nonexistent")

        assert result.success is False
        assert "No semantic layer found" in result.error


class TestSchemaServiceAddTable:
    """Tests for add_table() method."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock SemanticLayerManager."""
        return Mock()

    @pytest.fixture
    def service(self, mock_manager):
        """Create SchemaService instance with mocked manager."""
        with patch(
            "lib.services.schema_service.SemanticLayerManager",
            return_value=mock_manager,
        ):
            svc = SchemaService()
            return svc

    def test_add_table_success(self, service):
        """Test adding a table annotation."""
        service._manager.add_table.return_value = True

        result = service.add_table(
            "test-target",
            "new_table",
            description="A new table",
            business_context="Business info",
            row_estimate="1000",
        )

        assert isinstance(result, SchemaUpdateResult)
        assert result.success is True
        service._manager.add_table.assert_called_once()

    def test_add_table_handles_error(self, service):
        """Test add_table handles errors."""
        service._manager.add_table.side_effect = Exception("Manager error")

        result = service.add_table("test-target", "new_table", description="desc")

        assert result.success is False
        assert "Failed to add table" in result.error


class TestSchemaServiceAddTerminology:
    """Tests for add_terminology() method."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock SemanticLayerManager."""
        return Mock()

    @pytest.fixture
    def service(self, mock_manager):
        """Create SchemaService instance with mocked manager."""
        with patch(
            "lib.services.schema_service.SemanticLayerManager",
            return_value=mock_manager,
        ):
            svc = SchemaService()
            return svc

    def test_add_terminology_success(self, service):
        """Test adding a terminology entry."""
        service._manager.add_terminology.return_value = True

        result = service.add_terminology(
            "test-target",
            term="churn",
            definition="Customer who cancelled",
            sql_pattern="status = 'cancelled'",
            synonyms=["churned", "cancelled"],
        )

        assert isinstance(result, SchemaUpdateResult)
        assert result.success is True
        service._manager.add_terminology.assert_called_once()


class TestSchemaServiceAddMetric:
    """Tests for add_metric() method."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock SemanticLayerManager."""
        return Mock()

    @pytest.fixture
    def service(self, mock_manager):
        """Create SchemaService instance with mocked manager."""
        with patch(
            "lib.services.schema_service.SemanticLayerManager",
            return_value=mock_manager,
        ):
            svc = SchemaService()
            return svc

    def test_add_metric_success(self, service):
        """Test adding a metric definition."""
        service._manager.add_metric.return_value = True

        result = service.add_metric(
            "test-target",
            name="revenue",
            definition="Total revenue",
            sql="SUM(amount)",
            unit="USD",
        )

        assert isinstance(result, SchemaUpdateResult)
        assert result.success is True
        service._manager.add_metric.assert_called_once()


class TestSchemaServiceAnnotate:
    """Tests for annotate() method."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock SemanticLayerManager."""
        return Mock()

    @pytest.fixture
    def service(self, mock_manager):
        """Create SchemaService instance with mocked manager."""
        with patch(
            "lib.services.schema_service.SemanticLayerManager",
            return_value=mock_manager,
        ):
            svc = SchemaService()
            return svc

    def test_annotate_requires_anthropic_key(self, service):
        """Test annotate returns key error when neither env var is set."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("lib.services.anthropic_env._has_active_trial", return_value=False):
                result = service.annotate("test-target", {"engine": "postgresql"})

        assert result.success is False
        assert "ANTHROPIC_API_KEY" in (result.error or "")

    def test_annotate_accepts_rdst_trial_token(self, service):
        """Test annotate accepts RDST_TRIAL_TOKEN."""
        service._manager.exists.return_value = False

        with patch.dict(
            "os.environ", {"RDST_TRIAL_TOKEN": "test-token"}, clear=True
        ):
            result = service.annotate("test-target", {"engine": "postgresql"})

        assert result.success is False
        assert "No semantic layer found" in (result.error or "")


class TestSchemaServiceEventTypes:
    """Tests for service event types and dataclasses."""

    def test_schema_status_structure(self):
        """Test SchemaStatus dataclass."""
        status = SchemaStatus(
            target="test",
            exists=True,
            tables=5,
            columns=20,
            relationships=3,
            terminology=10,
            updated_at="2024-01-01",
        )

        assert status.target == "test"
        assert status.exists is True
        assert status.tables == 5

    def test_schema_table_column_structure(self):
        """Test SchemaTableColumn dataclass."""
        column = SchemaTableColumn(
            name="id",
            data_type="integer",
            description="Primary key",
            unit=None,
            is_pii=False,
            enum_values=None,
        )

        assert column.name == "id"
        assert column.data_type == "integer"
        assert column.is_pii is False

    def test_schema_table_structure(self):
        """Test SchemaTable dataclass."""
        table = SchemaTable(
            name="users",
            description="User accounts",
            business_context="Core user data",
            row_estimate="1M",
            columns=[],
            relationships=[],
        )

        assert table.name == "users"
        assert table.description == "User accounts"

    def test_schema_terminology_structure(self):
        """Test SchemaTerminology dataclass."""
        term = SchemaTerminology(
            term="active user",
            definition="User with login in last 30 days",
            sql_pattern="last_login > NOW() - INTERVAL '30 days'",
            synonyms=["engaged user"],
        )

        assert term.term == "active user"
        assert len(term.synonyms) == 1

    def test_schema_metric_structure(self):
        """Test SchemaMetric dataclass."""
        metric = SchemaMetric(
            name="revenue",
            definition="Total revenue",
            sql="SUM(amount)",
        )

        assert metric.name == "revenue"
        assert metric.sql == "SUM(amount)"

    def test_schema_init_options_defaults(self):
        """Test SchemaInitOptions has sensible defaults."""
        options = SchemaInitOptions()

        assert options.enum_threshold == 20
        assert options.force is False

    def test_schema_init_result_structure(self):
        """Test SchemaInitResult dataclass."""
        result = SchemaInitResult(
            success=True,
            target="test",
            tables=5,
            columns=25,
            relationships=3,
            enum_columns=["status"],
            path="/path/to/layer",
        )

        assert result.success is True
        assert result.tables == 5
        assert "status" in result.enum_columns

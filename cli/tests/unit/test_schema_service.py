"""
Unit tests for SchemaService.

Tests the semantic layer management service including schema fetching,
status checking, and export functionality.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import Any, Dict, List

from features.schema.semantic_layer.manager import SemanticLayerManager
from features.schema.semantic_models import SemanticLayer, TableAnnotation
from features.schema.service import SchemaService
from features.schema.models import (
    SchemaCustomType,
    SchemaDeleteResult,
    SchemaDetails,
    SchemaExportResult,
    SchemaExtension,
    SchemaInitOptions,
    SchemaInitResult,
    SchemaMetric,
    SchemaStatus,
    SchemaTable,
    SchemaTableColumn,
    SchemaTableRelationship,
    SchemaTargetList,
    SchemaTargetSummary,
    SchemaTerminology,
    SchemaUpdateResult,
)


class TestSchemaServiceInit:
    """Tests for SchemaService initialization."""

    def test_initialization(self):
        """Test service initializes correctly."""
        with patch("features.schema.service.SemanticLayerManager"):
            service = SchemaService()
            assert service is not None

    def test_has_required_methods(self):
        """Test service has required methods."""
        with patch("features.schema.service.SemanticLayerManager"):
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
            "features.schema.service.SemanticLayerManager",
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
            "profiled_tables": 2,
            "profiled_at": "2024-01-02T00:00:00Z",
        }

        status = service.get_status("test-target")

        assert isinstance(status, SchemaStatus)
        assert status.target == "test-target"
        assert status.exists is True
        assert status.tables == 5
        assert status.columns == 25
        assert status.relationships == 3
        assert status.terminology == 10
        assert status.profiled_tables == 2
        assert status.profiled_at == "2024-01-02T00:00:00Z"

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
            "features.schema.service.SemanticLayerManager",
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
            "features.schema.service.SemanticLayerManager",
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
            "features.schema.service.SemanticLayerManager",
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
            "features.schema.service.SchemaIntrospector"
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
            "features.schema.service.SchemaIntrospector"
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
            "features.schema.service.SemanticLayerManager",
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
            "features.schema.service.SemanticLayerManager",
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
            "features.schema.service.SemanticLayerManager",
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
            "features.schema.service.SemanticLayerManager",
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
            "features.schema.service.SemanticLayerManager",
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


class TestDecimalSanitizedBeforeYaml:
    """apply_profile() must convert Decimal to float/int for YAML safety."""

    def _make_table_with_column(self, col_name: str) -> "TableAnnotation":
        from features.schema.semantic_models import ColumnAnnotation, TableAnnotation
        col = ColumnAnnotation(name=col_name)
        return TableAnnotation(name="test_table", columns={col_name: col})

    def _make_profile(self, col_name, null_fraction, distinct_count, top_values=None):
        col_profile = Mock()
        col_profile.null_fraction = null_fraction
        col_profile.distinct_count = distinct_count
        col_profile.top_values = top_values
        profile = Mock()
        profile.row_estimate = 100
        profile.columns = {col_name: col_profile}
        return profile

    def test_decimal_null_fraction_converted_to_float(self):
        import decimal
        table = self._make_table_with_column("status")
        profile = self._make_profile("status", decimal.Decimal("0.1234"), decimal.Decimal("5"))
        table.apply_profile(profile)
        col = table.columns["status"]
        assert isinstance(col.null_fraction, float)
        assert not isinstance(col.null_fraction, decimal.Decimal)

    def test_decimal_distinct_count_converted_to_int(self):
        import decimal
        table = self._make_table_with_column("status")
        profile = self._make_profile("status", decimal.Decimal("0.0"), decimal.Decimal("42"))
        table.apply_profile(profile)
        col = table.columns["status"]
        assert isinstance(col.distinct_count, int)
        assert not isinstance(col.distinct_count, decimal.Decimal)

    def test_decimal_row_estimate_converted_to_int(self):
        import decimal
        table = self._make_table_with_column("id")
        profile = self._make_profile("id", None, decimal.Decimal("100"))
        profile.row_estimate = decimal.Decimal("5000")
        table.apply_profile(profile)
        assert isinstance(table.row_count, int)
        assert table.row_count == 5000

    def test_to_dict_produces_yaml_safe_types(self):
        import decimal
        table = self._make_table_with_column("amount")
        profile = self._make_profile(
            "amount", decimal.Decimal("0.05"), decimal.Decimal("10"),
            top_values={"high": decimal.Decimal("3"), "low": decimal.Decimal("7")},
        )
        table.apply_profile(profile)
        result = table.to_dict()
        stats = result["columns"]["amount"]["stats"]
        assert isinstance(stats["null_fraction"], float)
        assert isinstance(stats["distinct_count"], int)


class TestSchemaInitForceErrorMessage:
    """Schema init error must say '--force' not 'force=True'."""

    @pytest.fixture
    def service(self):
        with patch("features.schema.service.SemanticLayerManager") as MockManager:
            MockManager.return_value.exists.return_value = True
            svc = SchemaService()
            return svc

    def test_error_message_contains_force_flag(self, service):
        result = service.init("my-target", {"engine": "postgresql"}, SchemaInitOptions(force=False))
        assert result.success is False
        assert "--force" in result.error

    def test_error_message_does_not_contain_force_equals_true(self, service):
        result = service.init("my-target", {"engine": "postgresql"}, SchemaInitOptions(force=False))
        assert "force=True" not in (result.error or "")


class TestSchemaListShowsTargetDetails:
    """schema list output should include target names and table counts."""

    def test_schema_list_output_includes_target_names(self):
        """Bug fix: 'rdst schema list' should print target names and table
        counts, not just 'Found N semantic layer(s)'."""
        from shared.cli.rdst_cli import RdstCLI
        from features.schema.events import SchemaCompleteEvent
        from features.schema.models import SchemaTargetList, SchemaTargetSummary

        cli = RdstCLI()

        complete_event = SchemaCompleteEvent(
            type="complete",
            operation="list",
            success=True,
            target_list=SchemaTargetList(
                targets=[
                    SchemaTargetSummary(
                        name="prod-db",
                        tables=12,
                        terminology=5,
                        updated_at="2024-06-01",
                    ),
                    SchemaTargetSummary(
                        name="staging",
                        tables=8,
                        terminology=3,
                        updated_at="2024-05-15",
                    ),
                ]
            ),
        )

        async def fake_list_events():
            yield complete_event

        with patch("features.schema.service.SemanticLayerManager"):
            with patch("features.schema.service.SchemaService.list_targets_events", return_value=fake_list_events()):
                with patch("features.schema.cli.renderer.SchemaRenderer.render"):
                    result = cli.schema(subcommand="list")

        assert result.ok is True
        assert "prod-db" in result.message, (
            f"Expected target name 'prod-db' in list output. Got: {result.message!r}"
        )
        assert "staging" in result.message, (
            f"Expected target name 'staging' in list output. Got: {result.message!r}"
        )
        assert "12 table" in result.message, (
            f"Expected table count in list output. Got: {result.message!r}"
        )


class TestSchemaRendererNoStandaloneTablesHeader:
    """schema show output should NOT contain a standalone 'Tables:' line
    before the tree (the tree already has its own header)."""

    def test_schema_show_no_standalone_tables_header(self):
        """Bug fix: the standalone 'Tables:' print was removed from the
        renderer because the SimpleTree already includes 'Tables (N)' as
        its root label. Verify 'Tables:' does not appear as a standalone
        line."""
        from features.schema.cli.renderer import SchemaRenderer
        from features.schema.events import SchemaCompleteEvent
        from features.schema.models import SchemaDetails, SchemaTable, SchemaTableColumn
        from io import StringIO
        from rich.console import Console

        renderer = SchemaRenderer()

        # Capture all console output
        buf = StringIO()
        capture_console = Console(file=buf, force_terminal=True, width=120)
        renderer._console = capture_console

        event = SchemaCompleteEvent(
            type="complete",
            operation="show",
            success=True,
            details=SchemaDetails(
                target="test-target",
                tables=[
                    SchemaTable(
                        name="users",
                        description="User accounts",
                        business_context="",
                        row_estimate="",
                        columns=[
                            SchemaTableColumn(
                                name="id",
                                data_type="integer",
                                description="Primary key",
                            )
                        ],
                        relationships=[],
                    )
                ],
                terminology=[],
                extensions=[],
                custom_types=[],
                metrics=[],
            ),
        )

        renderer.render(event)

        output = buf.getvalue()
        # Split into lines and check no line is exactly "Tables:" (with optional whitespace)
        lines = output.split("\n")
        standalone_tables_lines = [
            line for line in lines
            if line.strip() == "Tables:" or line.strip() == "Tables"
        ]
        assert len(standalone_tables_lines) == 0, (
            f"Found standalone 'Tables:' header line(s) in schema show output. "
            f"Lines: {standalone_tables_lines}"
        )
        # The tree root should include the count, e.g., "Tables (1)"
        assert "Tables" in output, "Expected 'Tables' to appear in the tree header"


class TestSchemaTableNotFoundErrorSplit:
    """Error message when table not found must be specific, not ambiguous."""

    @pytest.fixture
    def mock_manager(self):
        manager = Mock()
        return manager

    @pytest.fixture
    def service(self, mock_manager):
        with patch(
            "features.schema.service.SemanticLayerManager",
            return_value=mock_manager,
        ):
            svc = SchemaService()
            return svc

    @pytest.mark.asyncio
    async def test_table_not_found_but_layer_exists_no_or(self, service):
        """Bug fix: when a table doesn't exist but the semantic layer does,
        the error should say 'Table X not found in semantic layer' without
        an ambiguous 'or' that conflates two different error conditions.

        Previously the error message was a single ambiguous sentence covering
        both 'no semantic layer' and 'table not in layer'. Now these are
        split into two distinct cases.
        """
        service._manager.exists.return_value = True

        # Mock load to return a layer with no matching table
        mock_layer = Mock()
        mock_layer.tables = {"users": Mock()}
        mock_layer.terminology = {}
        mock_layer.metrics = {}
        mock_layer.extensions = {}
        mock_layer.custom_types = {}
        service._manager.load.return_value = mock_layer

        events = []
        async for event in service.get_schema_events("test-target", table_name="nonexistent_table"):
            events.append(event)

        from features.schema.events import SchemaErrorEvent

        error_events = [e for e in events if isinstance(e, SchemaErrorEvent)]
        assert len(error_events) == 1

        msg = error_events[0].message
        assert "nonexistent_table" in msg, f"Error should mention the table name, got: {msg!r}"
        assert " or " not in msg, (
            f"Error should NOT contain 'or' when table doesn't exist but layer does. Got: {msg!r}"
        )
        assert "not found" in msg.lower()

    @pytest.mark.asyncio
    async def test_no_layer_error_is_distinct(self, service):
        """When the semantic layer itself doesn't exist, the error should say
        'No semantic layer found' without mentioning a specific table.
        """
        service._manager.exists.return_value = False

        events = []
        async for event in service.get_schema_events("test-target", table_name="users"):
            events.append(event)

        from features.schema.events import SchemaErrorEvent

        error_events = [e for e in events if isinstance(e, SchemaErrorEvent)]
        assert len(error_events) == 1

        msg = error_events[0].message
        assert "No semantic layer found" in msg
        assert " or " not in msg


class TestGetSummaryProfileFields:
    """get_summary must surface how much of the layer has been profiled."""

    def _layer(self, tmp_path, profiled_at_by_table):
        manager = SemanticLayerManager(base_dir=tmp_path)
        layer = SemanticLayer(target="t")
        for name, profiled_at in profiled_at_by_table.items():
            table = TableAnnotation(name=name)
            table.profiled_at = profiled_at
            layer.tables[name] = table
        manager.save(layer)
        return manager

    def test_unprofiled_layer_reports_zero(self, tmp_path):
        manager = self._layer(tmp_path, {"users": "", "orders": ""})
        summary = manager.get_summary("t")
        assert summary["profiled_tables"] == 0
        assert summary["profiled_at"] is None

    def test_partial_profile_counts_and_latest_timestamp(self, tmp_path):
        manager = self._layer(
            tmp_path,
            {
                "users": "2026-07-20T10:00:00+00:00",
                "orders": "",
                "items": "2026-07-21T09:00:00+00:00",
            },
        )
        summary = manager.get_summary("t")
        assert summary["profiled_tables"] == 2
        assert summary["profiled_at"] == "2026-07-21T09:00:00+00:00"

"""
Unit tests for PostgreSQL extension and custom type detection in schema_collector.

Tests the _collect_postgres_extensions and _collect_postgres_custom_types functions
that detect installed extensions and custom types to provide context to the LLM.
"""

from unittest.mock import Mock

from lib.functions.schema_collector import (
    _collect_postgres_extensions,
    _collect_postgres_custom_types
)


class TestCollectPostgresExtensions:
    """Tests for _collect_postgres_extensions function."""

    def test_returns_empty_string_when_no_extensions(self):
        """Test that empty string is returned when no extensions found."""
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = []

        result = _collect_postgres_extensions(mock_cursor)
        assert result == ""

    def test_formats_extensions_with_descriptions(self):
        """Test that extensions include descriptions from database."""
        mock_cursor = Mock()
        # Format: (name, version, description, types_csv)
        mock_cursor.fetchall.return_value = [
            ('ulid', '0.1.3', 'ULID data type', 'ulid'),
            ('pg_trgm', '1.6', 'text similarity measurement and index searching based on trigrams', ''),
            ('postgis', '3.4.0', 'PostGIS geometry and geography spatial types', 'geometry, geography'),
        ]

        result = _collect_postgres_extensions(mock_cursor)

        assert "Installed Extensions:" in result
        assert "ulid v0.1.3" in result
        assert "ULID data type" in result
        assert "pg_trgm v1.6" in result
        assert "trigram" in result
        assert "postgis v3.4.0" in result

    def test_formats_extensions_without_descriptions(self):
        """Test that extensions without descriptions are listed properly."""
        mock_cursor = Mock()
        # Format: (name, version, description, types_csv) - empty description
        mock_cursor.fetchall.return_value = [
            ('custom_extension', '1.0.0', '', ''),
        ]

        result = _collect_postgres_extensions(mock_cursor)

        assert "custom_extension v1.0.0" in result
        assert "Installed Extensions:" in result

    def test_handles_cursor_exception(self):
        """Test that exceptions are handled gracefully."""
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("Database error")

        result = _collect_postgres_extensions(mock_cursor)
        assert result == ""

    def test_multiple_extensions(self):
        """Test detecting multiple extensions."""
        mock_cursor = Mock()
        # Format: (name, version, description, types_csv)
        mock_cursor.fetchall.return_value = [
            ('btree_gin', '1.3', 'support for indexing common datatypes in GIN', ''),
            ('btree_gist', '1.7', 'support for indexing common datatypes in GiST', ''),
            ('hstore', '1.8', 'data type for storing sets of (key, value) pairs', 'hstore'),
            ('ltree', '1.2', 'data type for hierarchical tree-like structures', 'ltree'),
        ]

        result = _collect_postgres_extensions(mock_cursor)

        assert "btree_gin" in result
        assert "btree_gist" in result
        assert "hstore" in result
        assert "ltree" in result


class TestCollectPostgresCustomTypes:
    """Tests for _collect_postgres_custom_types function."""

    def test_returns_empty_string_when_no_custom_types(self):
        """Test that empty string is returned when no custom types found."""
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = []

        result = _collect_postgres_custom_types(mock_cursor)
        assert result == ""

    def test_formats_enum_types_with_values(self):
        """Test that enum types show their values."""
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ('status_type', 'e', 'enum', None, 'pending, active, completed'),
        ]

        result = _collect_postgres_custom_types(mock_cursor)

        assert "Custom Types:" in result
        assert "status_type (enum)" in result
        assert "pending, active, completed" in result

    def test_formats_domain_types_with_base_type(self):
        """Test that domain types show their base type."""
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ('email_address', 'd', 'domain', 'character varying(255)', ''),
        ]

        result = _collect_postgres_custom_types(mock_cursor)

        assert "email_address (domain over character varying(255))" in result

    def test_formats_extension_types_with_warning(self):
        """Test that extension base types include comparison warning."""
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ('ulid', 'b', 'base', None, ''),
            ('geometry', 'b', 'base', None, ''),
        ]

        result = _collect_postgres_custom_types(mock_cursor)

        assert "ulid (extension type)" in result
        assert "geometry (extension type)" in result
        assert "compare with same type only" in result

    def test_formats_composite_types(self):
        """Test that composite types are listed."""
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ('address', 'c', 'composite', None, ''),
        ]

        result = _collect_postgres_custom_types(mock_cursor)

        assert "address (composite)" in result

    def test_handles_cursor_exception(self):
        """Test that exceptions are handled gracefully."""
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("Database error")

        result = _collect_postgres_custom_types(mock_cursor)
        assert result == ""

    def test_mixed_custom_types(self):
        """Test detecting multiple types of different categories."""
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ('order_status', 'e', 'enum', None, 'pending, shipped, delivered'),
            ('positive_int', 'd', 'domain', 'integer', ''),
            ('ulid', 'b', 'base', None, ''),
            ('address_type', 'c', 'composite', None, ''),
        ]

        result = _collect_postgres_custom_types(mock_cursor)

        assert "Custom Types:" in result
        assert "order_status (enum)" in result
        assert "pending, shipped, delivered" in result
        assert "positive_int (domain over integer)" in result
        assert "ulid (extension type)" in result
        assert "address_type (composite)" in result

    def test_enum_with_many_values(self):
        """Test enum type with multiple values."""
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ('country_code', 'e', 'enum', None, 'US, CA, UK, DE, FR, JP, AU, BR'),
        ]

        result = _collect_postgres_custom_types(mock_cursor)

        assert "country_code (enum)" in result
        assert "US" in result
        assert "CA" in result


class TestIntegrationSchemaCollector:
    """Integration tests for extension detection in schema collection flow."""

    def test_extensions_and_types_together(self):
        """Test that both extensions and custom types are detected."""
        mock_cursor = Mock()

        # Setup for extensions query
        def execute_side_effect(query):
            pass

        mock_cursor.execute.side_effect = execute_side_effect

        # First call returns extensions (format: name, version, description, types_csv)
        mock_cursor.fetchall.side_effect = [
            [('pgx_ulid', '0.1.3', 'ULID support', 'ulid'), ('postgis', '3.4.0', 'PostGIS', 'geometry, geography')],
        ]

        ext_result = _collect_postgres_extensions(mock_cursor)
        assert "pgx_ulid" in ext_result
        assert "postgis" in ext_result

        # Reset for custom types query
        mock_cursor.fetchall.side_effect = [
            [('ulid', 'b', 'base', None, ''),
             ('geometry', 'b', 'base', None, '')],  # custom types
        ]

        types_result = _collect_postgres_custom_types(mock_cursor)
        assert "ulid (extension type)" in types_result
        assert "geometry (extension type)" in types_result


# =============================================================================
# Semantic Layer Extension/CustomType Tests
# =============================================================================

from lib.data_structures.semantic_layer import (
    SemanticLayer,
    Extension,
    CustomType
)
from lib.semantic_layer.introspector import SchemaIntrospector


class TestExtensionDataclass:
    """Tests for Extension dataclass serialization."""

    def test_to_dict_with_all_fields(self):
        """Test serialization with all fields populated."""
        ext = Extension(
            name='pgx_ulid',
            version='0.1.3',
            description='Provides ULID type',
            types_provided=['ulid']
        )
        result = ext.to_dict()

        assert result['version'] == '0.1.3'
        assert result['description'] == 'Provides ULID type'
        assert result['types_provided'] == ['ulid']

    def test_to_dict_omits_empty_fields(self):
        """Test that empty fields are not serialized."""
        ext = Extension(name='unknown_ext', version='1.0')
        result = ext.to_dict()

        assert 'version' in result
        assert 'description' not in result
        assert 'types_provided' not in result

    def test_from_dict_roundtrip(self):
        """Test that from_dict reverses to_dict."""
        original = Extension(
            name='postgis',
            version='3.4.0',
            description='Geographic types',
            types_provided=['geometry', 'geography']
        )
        data = original.to_dict()
        restored = Extension.from_dict('postgis', data)

        assert restored.name == original.name
        assert restored.version == original.version
        assert restored.description == original.description
        assert restored.types_provided == original.types_provided

    def test_from_dict_with_missing_fields(self):
        """Test from_dict handles missing optional fields."""
        data = {'version': '1.0'}
        ext = Extension.from_dict('test_ext', data)

        assert ext.name == 'test_ext'
        assert ext.version == '1.0'
        assert ext.description == ''
        assert ext.types_provided == []


class TestCustomTypeDataclass:
    """Tests for CustomType dataclass serialization."""

    def test_enum_type_serialization(self):
        """Test serialization of enum type."""
        ct = CustomType(
            name='order_status',
            type_category='enum',
            enum_values=['pending', 'shipped', 'delivered']
        )
        result = ct.to_dict()

        assert result['category'] == 'enum'
        assert result['enum_values'] == ['pending', 'shipped', 'delivered']
        assert 'base_type' not in result

    def test_domain_type_serialization(self):
        """Test serialization of domain type."""
        ct = CustomType(
            name='email_domain',
            type_category='domain',
            base_type='varchar(255)'
        )
        result = ct.to_dict()

        assert result['category'] == 'domain'
        assert result['base_type'] == 'varchar(255)'

    def test_base_type_serialization(self):
        """Test serialization of extension base type."""
        ct = CustomType(
            name='ulid',
            type_category='base',
            description='Sortable unique ID',
            extension='pgx_ulid'
        )
        result = ct.to_dict()

        assert result['category'] == 'base'
        assert result['description'] == 'Sortable unique ID'
        assert result['extension'] == 'pgx_ulid'

    def test_from_dict_roundtrip(self):
        """Test that from_dict reverses to_dict."""
        original = CustomType(
            name='ulid',
            type_category='base',
            description='ULID type',
            extension='pgx_ulid'
        )
        data = original.to_dict()
        restored = CustomType.from_dict('ulid', data)

        assert restored.name == original.name
        assert restored.type_category == original.type_category
        assert restored.description == original.description
        assert restored.extension == original.extension


class TestSemanticLayerExtensions:
    """Tests for SemanticLayer with extensions and custom_types."""

    def test_to_dict_includes_extensions(self):
        """Test that extensions are serialized."""
        layer = SemanticLayer(target='test')
        layer.extensions['pgx_ulid'] = Extension(
            name='pgx_ulid',
            version='0.1.3',
            types_provided=['ulid']
        )

        data = layer.to_dict()

        assert 'extensions' in data
        assert 'pgx_ulid' in data['extensions']

    def test_to_dict_includes_custom_types(self):
        """Test that custom_types are serialized."""
        layer = SemanticLayer(target='test')
        layer.custom_types['status'] = CustomType(
            name='status',
            type_category='enum',
            enum_values=['active', 'inactive']
        )

        data = layer.to_dict()

        assert 'custom_types' in data
        assert 'status' in data['custom_types']

    def test_from_dict_restores_extensions(self):
        """Test that extensions are deserialized."""
        data = {
            'target': 'test',
            'version': 1,
            'extensions': {
                'postgis': {
                    'version': '3.4.0',
                    'types_provided': ['geometry']
                }
            }
        }

        layer = SemanticLayer.from_dict(data)

        assert 'postgis' in layer.extensions
        assert layer.extensions['postgis'].version == '3.4.0'

    def test_from_dict_restores_custom_types(self):
        """Test that custom_types are deserialized."""
        data = {
            'target': 'test',
            'version': 1,
            'custom_types': {
                'ulid': {
                    'category': 'base',
                    'extension': 'pgx_ulid'
                }
            }
        }

        layer = SemanticLayer.from_dict(data)

        assert 'ulid' in layer.custom_types
        assert layer.custom_types['ulid'].type_category == 'base'

    def test_full_roundtrip(self):
        """Test complete serialization/deserialization roundtrip."""
        layer = SemanticLayer(target='test')
        layer.extensions['pgx_ulid'] = Extension(
            name='pgx_ulid', version='0.1.3', types_provided=['ulid']
        )
        layer.custom_types['ulid'] = CustomType(
            name='ulid', type_category='base', extension='pgx_ulid'
        )
        layer.custom_types['status'] = CustomType(
            name='status', type_category='enum',
            enum_values=['pending', 'active']
        )

        data = layer.to_dict()
        restored = SemanticLayer.from_dict(data)

        assert len(restored.extensions) == 1
        assert len(restored.custom_types) == 2
        assert 'pgx_ulid' in restored.extensions
        assert 'ulid' in restored.custom_types
        assert 'status' in restored.custom_types


class TestGetExtensionsContext:
    """Tests for SemanticLayer.get_extensions_context() method."""

    def test_empty_when_no_extensions_or_types(self):
        """Test returns empty string when nothing to report."""
        layer = SemanticLayer(target='test')
        result = layer.get_extensions_context()
        assert result == ""

    def test_formats_extensions(self):
        """Test extensions are formatted correctly."""
        layer = SemanticLayer(target='test')
        layer.extensions['pgx_ulid'] = Extension(
            name='pgx_ulid',
            version='0.1.3',
            description='Provides ULID type',
            types_provided=['ulid']
        )

        result = layer.get_extensions_context()

        assert "Installed Extensions:" in result
        assert "pgx_ulid v0.1.3" in result
        assert "Provides ULID type" in result
        assert "Types: ulid" in result

    def test_formats_enum_custom_type(self):
        """Test enum types are formatted with values."""
        layer = SemanticLayer(target='test')
        layer.custom_types['status'] = CustomType(
            name='status',
            type_category='enum',
            enum_values=['pending', 'active', 'completed']
        )

        result = layer.get_extensions_context()

        assert "Custom Types:" in result
        assert "status (enum)" in result
        assert "pending" in result
        assert "active" in result

    def test_formats_domain_custom_type(self):
        """Test domain types show base type."""
        layer = SemanticLayer(target='test')
        layer.custom_types['email'] = CustomType(
            name='email',
            type_category='domain',
            base_type='varchar(255)'
        )

        result = layer.get_extensions_context()

        assert "email (domain over varchar(255))" in result

    def test_formats_base_custom_type(self):
        """Test extension base types show description."""
        layer = SemanticLayer(target='test')
        layer.custom_types['ulid'] = CustomType(
            name='ulid',
            type_category='base',
            description='Sortable unique ID'
        )

        result = layer.get_extensions_context()

        assert "ulid (extension type)" in result
        assert "Sortable unique ID" in result

    def test_truncates_long_enum_values(self):
        """Test that long enum lists are truncated."""
        layer = SemanticLayer(target='test')
        layer.custom_types['country'] = CustomType(
            name='country',
            type_category='enum',
            enum_values=['US', 'CA', 'UK', 'DE', 'FR', 'JP', 'AU', 'BR']
        )

        result = layer.get_extensions_context()

        assert "country (enum)" in result
        # First 5 values shown
        assert "US" in result
        assert "FR" in result
        # Truncation indicator
        assert "8 total" in result


class TestIntrospectorExtensionMethods:
    """Tests for SchemaIntrospector extension collection methods."""

    def _create_introspector(self):
        """Create an introspector with mock config."""
        config = {
            'engine': 'postgresql',
            'host': 'localhost',
            'port': 5432,
            'user': 'test',
            'database': 'test',
            'password_env': 'TEST_PASSWORD'
        }
        return SchemaIntrospector(config)

    def test_add_postgres_extensions(self):
        """Test _add_postgres_extensions populates layer.extensions."""
        introspector = self._create_introspector()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ('pgx_ulid', '0.1.3', 'ULID support', 'ulid'),
            ('postgis', '3.4.0', 'Geographic types', 'geometry, geography'),
        ]

        layer = SemanticLayer(target='test')
        introspector._add_postgres_extensions(mock_cursor, layer)

        assert 'pgx_ulid' in layer.extensions
        assert layer.extensions['pgx_ulid'].version == '0.1.3'
        assert layer.extensions['pgx_ulid'].description == 'ULID support'
        assert 'ulid' in layer.extensions['pgx_ulid'].types_provided

        assert 'postgis' in layer.extensions
        assert 'geometry' in layer.extensions['postgis'].types_provided
        assert 'geography' in layer.extensions['postgis'].types_provided

    def test_add_postgres_extensions_handles_unknown(self):
        """Test unknown extensions are added without type info."""
        introspector = self._create_introspector()
        mock_cursor = Mock()
        # New format: (name, version, description, types_str)
        mock_cursor.fetchall.return_value = [
            ('custom_ext', '1.0.0', '', ''),
        ]

        layer = SemanticLayer(target='test')
        introspector._add_postgres_extensions(mock_cursor, layer)

        assert 'custom_ext' in layer.extensions
        assert layer.extensions['custom_ext'].types_provided == []

    def test_add_postgres_custom_types(self):
        """Test _add_postgres_custom_types populates layer.custom_types."""
        introspector = self._create_introspector()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ('ulid', 'b', 'base', None, ''),
            ('order_status', 'e', 'enum', None, 'pending, shipped'),
            ('email_domain', 'd', 'domain', 'varchar(255)', ''),
        ]

        layer = SemanticLayer(target='test')
        introspector._add_postgres_custom_types(mock_cursor, layer)

        assert 'ulid' in layer.custom_types
        assert layer.custom_types['ulid'].type_category == 'base'

        assert 'order_status' in layer.custom_types
        assert layer.custom_types['order_status'].type_category == 'enum'
        assert 'pending' in layer.custom_types['order_status'].enum_values

        assert 'email_domain' in layer.custom_types
        assert layer.custom_types['email_domain'].base_type == 'varchar(255)'

    def test_add_postgres_custom_types_links_extension(self):
        """Test that base types are linked to their extensions."""
        introspector = self._create_introspector()

        # First add extensions with types_provided
        ext_cursor = Mock()
        # New format: (name, version, description, types_str)
        ext_cursor.fetchall.return_value = [('pgx_ulid', '0.1.3', 'ULID support', 'ulid')]

        layer = SemanticLayer(target='test')
        introspector._add_postgres_extensions(ext_cursor, layer)

        # Then add custom types
        type_cursor = Mock()
        type_cursor.fetchall.return_value = [
            ('ulid', 'b', 'base', None, ''),
        ]
        introspector._add_postgres_custom_types(type_cursor, layer)

        assert layer.custom_types['ulid'].extension == 'pgx_ulid'

    def test_add_postgres_extensions_handles_exception(self):
        """Test that exceptions don't crash introspection."""
        introspector = self._create_introspector()
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("DB error")

        layer = SemanticLayer(target='test')
        # Should not raise
        introspector._add_postgres_extensions(mock_cursor, layer)

        assert len(layer.extensions) == 0

    def test_add_postgres_custom_types_handles_exception(self):
        """Test that exceptions don't crash introspection."""
        introspector = self._create_introspector()
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("DB error")

        layer = SemanticLayer(target='test')
        # Should not raise
        introspector._add_postgres_custom_types(mock_cursor, layer)

        assert len(layer.custom_types) == 0

    def test_normalize_postgres_type_user_defined(self):
        """Test that USER-DEFINED types use udt_name."""
        introspector = self._create_introspector()

        # Standard type
        result = introspector._normalize_postgres_type('integer', None, None)
        assert result == 'int'

        # USER-DEFINED with udt_name
        result = introspector._normalize_postgres_type('USER-DEFINED', None, None, 'ulid')
        assert result == 'ulid'

        result = introspector._normalize_postgres_type('USER-DEFINED', None, None, 'geometry')
        assert result == 'geometry'

        # USER-DEFINED without udt_name (edge case)
        result = introspector._normalize_postgres_type('USER-DEFINED', None, None, None)
        assert result == 'USER-DEFINED'

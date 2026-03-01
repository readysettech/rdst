"""Tests for ColumnAnnotation value_pattern field.

Covers:
- Serialization roundtrip (to_dict/from_dict)
- Backward compatibility (old YAML without value_pattern)
- Empty pattern not serialized
"""

from lib.data_structures.semantic_layer import ColumnAnnotation


class TestColumnAnnotationEnrichment:
    """Test value_pattern on ColumnAnnotation."""

    def test_roundtrip_with_pattern(self):
        col = ColumnAnnotation(
            name="directors",
            data_type="text",
            value_pattern="comma_separated_list",
        )

        d = col.to_dict()
        assert d["value_pattern"] == "comma_separated_list"

        restored = ColumnAnnotation.from_dict("directors", d)
        assert restored.value_pattern == "comma_separated_list"

    def test_backward_compat_old_yaml(self):
        """Old YAML data without value_pattern should load with empty default."""
        old_data = {
            "type": "text",
            "description": "Director identifiers",
        }
        col = ColumnAnnotation.from_dict("directors", old_data)
        assert col.value_pattern == ""

    def test_empty_pattern_not_serialized(self):
        """Empty value_pattern should not appear in serialized dict."""
        col = ColumnAnnotation(name="id", data_type="int")
        d = col.to_dict()
        assert "value_pattern" not in d

    def test_pattern_preserved_with_existing_fields(self):
        """value_pattern should coexist with existing annotation fields."""
        col = ColumnAnnotation(
            name="genre",
            data_type="text",
            description="Movie genres",
            quality_notes="May contain duplicates",
            value_pattern="comma_separated_list",
        )
        d = col.to_dict()
        restored = ColumnAnnotation.from_dict("genre", d)
        assert restored.description == "Movie genres"
        assert restored.quality_notes == "May contain duplicates"
        assert restored.value_pattern == "comma_separated_list"

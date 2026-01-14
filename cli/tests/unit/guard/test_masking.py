"""Tests for masking functions."""

import pytest

from lib.guard.masking import mask_value, mask_results, get_masked_columns


class TestMaskValue:
    """Test individual value masking."""

    def test_redact(self):
        """Should fully redact value."""
        assert mask_value("sensitive", "redact") == "[REDACTED]"
        assert mask_value(12345, "redact") == "[REDACTED]"
        assert mask_value(None, "redact") is None  # None stays None

    def test_email_standard(self):
        """Should mask standard email format."""
        result = mask_value("user@example.com", "email")
        assert result.startswith("u")
        assert "@" in result
        assert result.endswith(".com")
        assert "***" in result

    def test_email_with_subdomain(self):
        """Should handle email with subdomain."""
        result = mask_value("john@mail.example.org", "email")
        assert "@" in result
        assert result.endswith(".org")

    def test_email_invalid(self):
        """Should redact invalid email."""
        result = mask_value("not-an-email", "email")
        assert result == "[REDACTED]"

    def test_partial_with_count(self):
        """Should show specified number of characters."""
        result = mask_value("1234567890", "partial:4")
        assert result.endswith("7890")
        assert result.startswith("*")
        assert len(result) == 10

    def test_partial_with_6(self):
        """Should show 6 characters."""
        result = mask_value("1234567890", "partial:6")
        assert result.endswith("567890")
        assert result.startswith("*")

    def test_partial_short_value(self):
        """Should handle values shorter than reveal count."""
        result = mask_value("123", "partial:4")
        assert result == "***"  # All masked

    def test_hash(self):
        """Should return consistent hash."""
        result = mask_value("secret", "hash")
        assert len(result) == 8  # First 8 hex chars

        # Same input should produce same hash
        result2 = mask_value("secret", "hash")
        assert result == result2

        # Different input should produce different hash
        result3 = mask_value("other", "hash")
        assert result != result3

    def test_unknown_mask_type(self):
        """Should fall back to redact for unknown type."""
        result = mask_value("data", "unknown-type")
        assert result == "[REDACTED]"


class TestGetMaskedColumns:
    """Test column pattern matching."""

    def test_wildcard_prefix(self):
        """Should match wildcard prefix patterns."""
        columns = ["id", "email", "phone"]
        patterns = {"*.email": "email", "*.phone": "partial:4"}

        masked = get_masked_columns(columns, patterns)

        assert "email" in masked
        assert "phone" in masked
        assert "id" not in masked

    def test_exact_match(self):
        """Should match exact column names."""
        columns = ["id", "user_email", "ssn"]
        patterns = {"ssn": "redact", "user_email": "email"}

        masked = get_masked_columns(columns, patterns)

        assert "ssn" in masked
        assert "user_email" in masked

    def test_table_qualified(self):
        """Should match table.column patterns."""
        columns = ["users.email", "orders.email"]
        patterns = {"users.email": "email"}

        masked = get_masked_columns(columns, patterns)

        assert "users.email" in masked
        assert len(masked) == 1  # Only users.email should match

    def test_case_insensitive(self):
        """Should match case-insensitively."""
        columns = ["EMAIL", "Phone", "ssn"]
        patterns = {"*.email": "email", "*.phone": "partial:4", "ssn": "redact"}

        masked = get_masked_columns(columns, patterns)

        assert len(masked) == 3  # All should match

    def test_no_patterns(self):
        """Should return empty list when no patterns."""
        columns = ["id", "email"]
        masked = get_masked_columns(columns, None)
        assert masked == []

    def test_no_matches(self):
        """Should return empty list when no matches."""
        columns = ["id", "name"]
        patterns = {"*.email": "email"}

        masked = get_masked_columns(columns, patterns)
        assert masked == []


class TestMaskResults:
    """Test full result set masking."""

    def test_mask_column(self):
        """Should mask specified column in results."""
        columns = ["id", "email", "name"]
        rows = [
            [1, "alice@example.com", "Alice"],
            [2, "bob@test.org", "Bob"],
        ]
        patterns = {"*.email": "email"}

        masked = mask_results(columns, rows, patterns)

        assert masked[0][0] == 1  # id unchanged
        assert masked[0][1] != "alice@example.com"  # email masked
        assert "@" in str(masked[0][1])  # Still looks like email
        assert masked[0][2] == "Alice"  # name unchanged

    def test_mask_multiple_columns(self):
        """Should mask multiple columns."""
        columns = ["id", "email", "ssn"]
        rows = [
            [1, "user@mail.com", "123-45-6789"],
        ]
        patterns = {"*.email": "email", "ssn": "redact"}

        masked = mask_results(columns, rows, patterns)

        assert masked[0][0] == 1  # id unchanged
        assert masked[0][1] != "user@mail.com"  # email masked
        assert masked[0][2] == "[REDACTED]"  # ssn redacted

    def test_empty_rows(self):
        """Should handle empty result set."""
        columns = ["id", "email"]
        rows = []
        patterns = {"*.email": "email"}

        masked = mask_results(columns, rows, patterns)
        assert masked == []

    def test_null_values(self):
        """Should preserve null values."""
        columns = ["id", "email"]
        rows = [
            [1, None],
        ]
        patterns = {"*.email": "email"}

        masked = mask_results(columns, rows, patterns)
        assert masked[0][1] is None

    def test_no_patterns(self):
        """Should return original rows when no patterns."""
        columns = ["id", "email"]
        rows = [[1, "user@mail.com"]]

        masked = mask_results(columns, rows, None)
        assert masked == rows

    def test_preserves_row_structure(self):
        """Should not mutate original rows."""
        columns = ["id", "email"]
        rows = [[1, "user@mail.com"]]
        patterns = {"*.email": "email"}

        masked = mask_results(columns, rows, patterns)

        # Original should be unchanged
        assert rows[0][1] == "user@mail.com"
        # Masked should be different
        assert masked[0][1] != "user@mail.com"

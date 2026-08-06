import pytest
from shared.db_connection import quote_identifier


class TestQuoteIdentifier:
    """Test cases for SQL identifier quoting."""

    def test_postgres_is_the_default_dialect(self):
        """Omitting the engine quotes for PostgreSQL."""
        assert quote_identifier("users") == '"users"'

    @pytest.mark.parametrize("engine", ["postgresql", "postgres", "psql", "POSTGRES"])
    def test_postgres_aliases_use_double_quotes(self, engine):
        """All PostgreSQL engine aliases resolve to double-quote style."""
        assert quote_identifier("users", engine) == '"users"'

    @pytest.mark.parametrize("engine", ["mysql", "MySQL"])
    def test_mysql_uses_backticks(self, engine):
        """MySQL identifiers are wrapped in backticks."""
        assert quote_identifier("users", engine) == "`users`"

    def test_unknown_engine_falls_back_to_double_quotes(self):
        """Unrecognized engines get the SQL-standard quote character."""
        assert quote_identifier("users", "sqlite") == '"users"'

    def test_postgres_doubles_embedded_double_quote(self):
        """An embedded double quote is escaped by doubling it."""
        assert quote_identifier('we"ird') == '"we""ird"'

    def test_mysql_doubles_embedded_backtick(self):
        """An embedded backtick is escaped by doubling it."""
        assert quote_identifier("we`ird", "mysql") == "`we``ird`"

    def test_postgres_neutralizes_injection_attempt(self):
        """A quote-and-break payload stays inside a single identifier."""
        quoted = quote_identifier('users" ; DROP TABLE users; --')
        assert quoted == '"users"" ; DROP TABLE users; --"'

    def test_mysql_neutralizes_a_break_out_payload(self):
        """A backtick-and-break payload stays inside a single identifier."""
        quoted = quote_identifier("users` ; DROP TABLE users; --", "mysql")
        assert quoted == "`users`` ; DROP TABLE users; --`"

    def test_backtick_is_untouched_in_postgres(self):
        """Backticks are not special to PostgreSQL and are left alone."""
        assert quote_identifier("we`ird") == '"we`ird"'

    def test_double_quote_is_untouched_in_mysql(self):
        """Double quotes are not the MySQL quote character and are left alone."""
        assert quote_identifier('we"ird', "mysql") == '`we"ird`'

    def test_names_needing_quotes_survive(self):
        """Mixed case, spaces, and reserved words are preserved verbatim."""
        assert quote_identifier("Order Details") == '"Order Details"'
        assert quote_identifier("select") == '"select"'

    @pytest.mark.parametrize("engine", ["postgresql", "mysql"])
    def test_nul_byte_is_rejected(self, engine):
        """A NUL byte cannot be quoted safely, so it is refused."""
        with pytest.raises(ValueError, match="NUL byte"):
            quote_identifier("users\x00evil", engine)

    @pytest.mark.parametrize("name", ["", None])
    def test_empty_identifier_is_rejected(self, name):
        """An empty or missing identifier is never valid."""
        with pytest.raises(ValueError, match="non-empty"):
            quote_identifier(name)

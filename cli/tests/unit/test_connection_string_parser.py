import pytest
from lib.cli.rdst_cli import parse_connection_string


class TestConnectionStringParser:
    """Test cases for connection string parsing."""

    def test_postgresql_basic(self):
        """Test basic PostgreSQL connection string."""
        conn_str = "postgresql://user:pass@localhost:5432/mydb"
        result = parse_connection_string(conn_str)

        assert result['engine'] == 'postgresql'
        assert result['host'] == 'localhost'
        assert result['port'] == 5432
        assert result['user'] == 'user'
        assert result['password'] == 'pass'
        assert result['database'] == 'mydb'
        assert result['tls'] is False

    def test_postgresql_with_postgres_scheme(self):
        """Test PostgreSQL connection string with 'postgres' scheme."""
        conn_str = "postgres://user:pass@localhost:5432/mydb"
        result = parse_connection_string(conn_str)

        assert result['engine'] == 'postgresql'

    def test_mysql_basic(self):
        """Test basic MySQL connection string."""
        conn_str = "mysql://user:pass@localhost:3306/mydb"
        result = parse_connection_string(conn_str)

        assert result['engine'] == 'mysql'
        assert result['host'] == 'localhost'
        assert result['port'] == 3306
        assert result['user'] == 'user'
        assert result['password'] == 'pass'
        assert result['database'] == 'mydb'
        assert result['tls'] is False

    def test_postgresql_no_port_uses_default(self):
        """Test PostgreSQL connection string without port uses default 5432."""
        conn_str = "postgresql://user:pass@db.example.com/mydb"
        result = parse_connection_string(conn_str)

        assert result['port'] == 5432

    def test_mysql_no_port_uses_default(self):
        """Test MySQL connection string without port uses default 3306."""
        conn_str = "mysql://user:pass@db.example.com/mydb"
        result = parse_connection_string(conn_str)

        assert result['port'] == 3306

    def test_postgresql_with_sslmode_require(self):
        """Test PostgreSQL connection string with sslmode=require."""
        conn_str = "postgresql://user:pass@localhost:5432/mydb?sslmode=require"
        result = parse_connection_string(conn_str)

        assert result['tls'] is True
        assert result['ssl_params']['sslmode'] == 'require'

    def test_postgresql_with_sslmode_prefer(self):
        """Test PostgreSQL connection string with sslmode=prefer (TLS not required)."""
        conn_str = "postgresql://user:pass@localhost:5432/mydb?sslmode=prefer"
        result = parse_connection_string(conn_str)

        assert result['tls'] is False
        assert result['ssl_params']['sslmode'] == 'prefer'

    def test_postgresql_with_sslmode_verify_ca(self):
        """Test PostgreSQL connection string with sslmode=verify-ca."""
        conn_str = "postgresql://user:pass@localhost:5432/mydb?sslmode=verify-ca"
        result = parse_connection_string(conn_str)

        assert result['tls'] is True

    def test_postgresql_with_sslmode_verify_full(self):
        """Test PostgreSQL connection string with sslmode=verify-full."""
        conn_str = "postgresql://user:pass@localhost:5432/mydb?sslmode=verify-full"
        result = parse_connection_string(conn_str)

        assert result['tls'] is True

    def test_mysql_with_ssl_true(self):
        """Test MySQL connection string with ssl=true."""
        conn_str = "mysql://user:pass@localhost:3306/mydb?ssl=true"
        result = parse_connection_string(conn_str)

        assert result['tls'] is True
        assert result['ssl_params']['ssl'] == 'true'

    def test_mysql_with_ssl_mode_required(self):
        """Test MySQL connection string with ssl-mode=REQUIRED."""
        conn_str = "mysql://user:pass@localhost:3306/mydb?ssl-mode=REQUIRED"
        result = parse_connection_string(conn_str)

        assert result['tls'] is True
        assert result['ssl_params']['ssl-mode'] == 'REQUIRED'

    def test_no_password_in_connection_string(self):
        """Test connection string without password."""
        conn_str = "postgresql://user@localhost:5432/mydb"
        result = parse_connection_string(conn_str)

        assert result['user'] == 'user'
        assert result['password'] is None

    def test_password_with_special_characters(self):
        """Test password with special characters gets URL decoded."""
        # @ symbol encoded as %40, : encoded as %3A
        conn_str = "postgresql://user:p%40ss%3Aword@localhost:5432/mydb"
        result = parse_connection_string(conn_str)

        assert result['password'] == 'p@ss:word'

    def test_username_with_special_characters(self):
        """Test username with special characters gets URL decoded."""
        conn_str = "postgresql://user%40domain:pass@localhost:5432/mydb"
        result = parse_connection_string(conn_str)

        assert result['user'] == 'user@domain'

    def test_custom_port(self):
        """Test connection string with custom port."""
        conn_str = "postgresql://user:pass@localhost:15432/mydb"
        result = parse_connection_string(conn_str)

        assert result['port'] == 15432

    def test_remote_host(self):
        """Test connection string with remote hostname."""
        conn_str = "postgresql://user:pass@db.example.com:5432/production"
        result = parse_connection_string(conn_str)

        assert result['host'] == 'db.example.com'
        assert result['database'] == 'production'

    def test_ip_address_host(self):
        """Test connection string with IP address."""
        conn_str = "postgresql://user:pass@192.168.1.100:5432/mydb"
        result = parse_connection_string(conn_str)

        assert result['host'] == '192.168.1.100'

    def test_multiple_query_parameters(self):
        """Test connection string with multiple query parameters."""
        conn_str = "postgresql://user:pass@localhost:5432/mydb?sslmode=require&sslrootcert=/path/to/cert&application_name=myapp"
        result = parse_connection_string(conn_str)

        assert result['ssl_params']['sslmode'] == 'require'
        assert result['ssl_params']['sslrootcert'] == '/path/to/cert'
        assert result['tls'] is True

    # Error cases

    def test_empty_connection_string_raises_error(self):
        """Test that empty connection string raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_connection_string("")

    def test_unsupported_engine_raises_error(self):
        """Test that unsupported database engine raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported database engine"):
            parse_connection_string("mongodb://user:pass@localhost:27017/mydb")

    def test_missing_hostname_raises_error(self):
        """Test that missing hostname raises ValueError."""
        with pytest.raises(ValueError, match="missing hostname"):
            parse_connection_string("postgresql://user:pass@/mydb")

    def test_missing_username_raises_error(self):
        """Test that missing username raises ValueError."""
        with pytest.raises(ValueError, match="missing username"):
            parse_connection_string("postgresql://localhost:5432/mydb")

    def test_missing_database_raises_error(self):
        """Test that missing database name raises ValueError."""
        with pytest.raises(ValueError, match="missing database name"):
            parse_connection_string("postgresql://user:pass@localhost:5432")

    def test_missing_database_with_trailing_slash_raises_error(self):
        """Test that missing database name with trailing slash raises ValueError."""
        with pytest.raises(ValueError, match="missing database name"):
            parse_connection_string("postgresql://user:pass@localhost:5432/")

    def test_invalid_url_format_raises_error(self):
        """Test that completely invalid URL format raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported database engine"):
            parse_connection_string("not-a-valid-url")


class TestConnectionStringIntegration:
    """Integration tests for connection string parsing with rdst configure."""

    def test_postgresql_connection_string_full_example(self):
        """Test complete PostgreSQL connection string as would be used in rdst configure."""
        # This is the format from the Linear ticket
        conn_str = "postgresql://user:pass@host:5432/dbname?sslmode=require"
        result = parse_connection_string(conn_str)

        assert result['engine'] == 'postgresql'
        assert result['host'] == 'host'
        assert result['port'] == 5432
        assert result['user'] == 'user'
        assert result['password'] == 'pass'
        assert result['database'] == 'dbname'
        assert result['tls'] is True
        assert 'sslmode' in result['ssl_params']

    def test_mysql_connection_string_full_example(self):
        """Test complete MySQL connection string as would be used in rdst configure."""
        conn_str = "mysql://admin:secret@mysql.example.com:3306/production?ssl=true"
        result = parse_connection_string(conn_str)

        assert result['engine'] == 'mysql'
        assert result['host'] == 'mysql.example.com'
        assert result['port'] == 3306
        assert result['user'] == 'admin'
        assert result['password'] == 'secret'
        assert result['database'] == 'production'
        assert result['tls'] is True

    def test_real_world_aws_rds_postgresql(self):
        """Test real-world AWS RDS PostgreSQL connection string."""
        conn_str = "postgresql://myuser:mypassword@mydb.abc123.us-east-1.rds.amazonaws.com:5432/mydbname?sslmode=require"
        result = parse_connection_string(conn_str)

        assert result['engine'] == 'postgresql'
        assert result['host'] == 'mydb.abc123.us-east-1.rds.amazonaws.com'
        assert result['port'] == 5432
        assert result['user'] == 'myuser'
        assert result['database'] == 'mydbname'
        assert result['tls'] is True

    def test_real_world_google_cloud_sql_mysql(self):
        """Test real-world Google Cloud SQL MySQL connection string."""
        conn_str = "mysql://root:password@35.192.123.456:3306/mydatabase?ssl-mode=REQUIRED"
        result = parse_connection_string(conn_str)

        assert result['engine'] == 'mysql'
        assert result['host'] == '35.192.123.456'
        assert result['port'] == 3306
        assert result['database'] == 'mydatabase'
        assert result['tls'] is True

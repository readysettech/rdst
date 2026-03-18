"""Tests for CSV fleet importer."""

import os
import tempfile

import pytest

from lib.fleet.csv_importer import (
    detect_region_from_hostname,
    parse_csv,
)


class TestDetectRegion:
    def test_standard_rds_hostname(self):
        assert (
            detect_region_from_hostname(
                "prod-db.abc123.us-east-1.rds.amazonaws.com"
            )
            == "us-east-1"
        )

    def test_us_west_2(self):
        assert (
            detect_region_from_hostname(
                "mydb.xyz.us-west-2.rds.amazonaws.com"
            )
            == "us-west-2"
        )

    def test_eu_region(self):
        assert (
            detect_region_from_hostname(
                "mydb.xyz.eu-west-1.rds.amazonaws.com"
            )
            == "eu-west-1"
        )

    def test_non_rds_hostname(self):
        assert detect_region_from_hostname("localhost") is None

    def test_custom_hostname(self):
        assert detect_region_from_hostname("db.mycompany.com") is None


class TestParseCSV:
    def _write_csv(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_basic_import(self):
        path = self._write_csv(
            "name,host,port,database,user,engine\n"
            "db1,host1.us-east-1.rds.amazonaws.com,5432,mydb,postgres,postgresql\n"
            "db2,host2.us-east-1.rds.amazonaws.com,3306,mydb,admin,mysql\n"
        )
        try:
            members, errors = parse_csv(path, password_env="TEST_PASS")
            assert len(members) == 2
            assert len(errors) == 0
            assert members[0].name == "db1"
            assert members[0].engine == "postgresql"
            assert members[0].port == 5432
            assert members[0].password_env == "TEST_PASS"
            assert members[0].region == "us-east-1"
            assert members[1].engine == "mysql"
            assert members[1].port == 3306
        finally:
            os.unlink(path)

    def test_missing_required_columns(self):
        path = self._write_csv("hostname,port\ndb1,5432\n")
        try:
            members, errors = parse_csv(path)
            assert len(members) == 0
            assert len(errors) == 1
            assert "Missing required columns" in errors[0]["error"]
        finally:
            os.unlink(path)

    def test_engine_normalization(self):
        path = self._write_csv(
            "name,host,engine\n"
            "a,h1,postgres\n"
            "b,h2,POSTGRESQL\n"
            "c,h3,psql\n"
            "d,h4,MySQL\n"
            "e,h5,mariadb\n"
        )
        try:
            members, errors = parse_csv(path)
            assert len(members) == 5
            assert members[0].engine == "postgresql"
            assert members[1].engine == "postgresql"
            assert members[2].engine == "postgresql"
            assert members[3].engine == "mysql"
            assert members[4].engine == "mysql"
        finally:
            os.unlink(path)

    def test_unsupported_engine(self):
        path = self._write_csv("name,host,engine\ndb1,h1,oracle\n")
        try:
            members, errors = parse_csv(path)
            assert len(members) == 0
            assert len(errors) == 1
            assert "Unsupported engine" in errors[0]["error"]
        finally:
            os.unlink(path)

    def test_default_ports(self):
        path = self._write_csv(
            "name,host,engine\n"
            "pg,h1,postgresql\n"
            "my,h2,mysql\n"
        )
        try:
            members, _ = parse_csv(path)
            assert members[0].port == 5432
            assert members[1].port == 3306
        finally:
            os.unlink(path)

    def test_group_and_tags(self):
        path = self._write_csv(
            'name,host,engine,group,tags\n'
            'db1,h1,postgresql,prod,"tag1,tag2"\n'
        )
        try:
            members, _ = parse_csv(path)
            assert members[0].group == "prod"
            assert "tag1" in members[0].tags
            assert "tag2" in members[0].tags
        finally:
            os.unlink(path)

    def test_default_group_override(self):
        path = self._write_csv("name,host,engine\ndb1,h1,postgresql\n")
        try:
            members, _ = parse_csv(path, default_group="my-fleet")
            assert members[0].group == "my-fleet"
        finally:
            os.unlink(path)

    def test_auto_detect_region_as_group(self):
        path = self._write_csv(
            "name,host,engine\n"
            "db1,prod.abc.us-east-2.rds.amazonaws.com,postgresql\n"
        )
        try:
            members, _ = parse_csv(path)
            assert members[0].group == "us-east-2"
            assert members[0].region == "us-east-2"
        finally:
            os.unlink(path)

    def test_file_not_found(self):
        members, errors = parse_csv("/nonexistent/path.csv")
        assert len(members) == 0
        assert "File not found" in errors[0]["error"]

    def test_empty_csv(self):
        path = self._write_csv("")
        try:
            members, errors = parse_csv(path)
            assert len(members) == 0
            assert len(errors) == 1
        finally:
            os.unlink(path)

    def test_default_tags_merged(self):
        path = self._write_csv(
            'name,host,engine,tags\ndb1,h1,postgresql,"local"\n'
        )
        try:
            members, _ = parse_csv(path, default_tags=["fleet-test"])
            assert "local" in members[0].tags
            assert "fleet-test" in members[0].tags
        finally:
            os.unlink(path)

    def test_per_row_password_env(self):
        path = self._write_csv(
            "name,host,engine,password_env\n"
            "db1,h1,postgresql,DB1_PASS\n"
            "db2,h2,mysql,DB2_PASS\n"
            "db3,h3,postgresql,\n"
        )
        try:
            members, _ = parse_csv(path, password_env="DEFAULT_PASS")
            assert members[0].password_env == "DB1_PASS"
            assert members[1].password_env == "DB2_PASS"
            assert members[2].password_env == "DEFAULT_PASS"  # Falls back to default
        finally:
            os.unlink(path)

    def test_skip_rows_with_missing_fields(self):
        path = self._write_csv(
            "name,host,engine\n"
            "db1,h1,postgresql\n"
            ",h2,mysql\n"  # Missing name
            "db3,,postgresql\n"  # Missing host
        )
        try:
            members, errors = parse_csv(path)
            assert len(members) == 1
            assert len(errors) == 2
        finally:
            os.unlink(path)


class TestFleetMemberToConfig:
    def test_basic_config(self):
        from lib.fleet.models import FleetMember

        m = FleetMember(
            name="test",
            engine="postgresql",
            host="localhost",
            port=5432,
            database="testdb",
            user="postgres",
            password_env="PASS",
        )
        cfg = m.to_target_config()
        assert cfg["engine"] == "postgresql"
        assert cfg["host"] == "localhost"
        assert cfg["port"] == 5432
        assert cfg["password_env"] == "PASS"
        assert "target_type" not in cfg  # Default "database" not stored
        assert "group" not in cfg  # None not stored

    def test_config_with_fleet_fields(self):
        from lib.fleet.models import FleetMember

        m = FleetMember(
            name="test",
            engine="mysql",
            host="h1",
            port=3306,
            database="db",
            user="admin",
            password_env="PASS",
            group="us-east-1",
            tags=["prod", "fleet"],
            instance_class="db.r6g.xlarge",
            region="us-east-1",
            target_type="read_replica",
            primary_target="primary-db",
        )
        cfg = m.to_target_config()
        assert cfg["group"] == "us-east-1"
        assert cfg["tags"] == ["prod", "fleet"]
        assert cfg["instance_class"] == "db.r6g.xlarge"
        assert cfg["target_type"] == "read_replica"
        assert cfg["primary_target"] == "primary-db"

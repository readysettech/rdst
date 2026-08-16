from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

import pymysql
import requests

from shared.persistence import write_json

from .executor import MySQLConnectionConfig, database_user

ARCHIVE_URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip"
ARCHIVE_SIZE = 800_943_648
ARCHIVE_MD5 = "7beb6dab11e65f0fde563e644a1ea319"
ARCHIVE_LAST_MODIFIED = "Thu, 20 Jun 2024 05:01:24 GMT"
MYSQL_DUMP_ENTRY = "minidev/MINIDEV_mysql/BIRD_dev.sql"
TABLES_ENTRY = "minidev/MINIDEV/dev_tables.json"
DESCRIPTION_PREFIX = "minidev/MINIDEV/dev_databases/"
COMPOSE_FILE = Path(__file__).with_name("compose.yaml")
COMPOSE_PROJECT = "rdst-bird-benchmark"
DEFAULT_MYSQL_IMAGE = "mysql:8.4.0"
DEFAULT_ROOT_PASSWORD = "rdst-bird-root"


class ProvisioningError(RuntimeError):
    pass


def download_archive(cache_dir: Path, *, timeout_seconds: float = 60.0) -> Path:
    archive = cache_dir / "downloads" / "minidev.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists() and _valid_archive(archive):
        return archive

    partial = archive.with_suffix(".zip.part")
    if partial.exists() and partial.stat().st_size >= ARCHIVE_SIZE:
        partial.unlink()
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    response = requests.get(
        ARCHIVE_URL,
        headers=headers,
        stream=True,
        timeout=timeout_seconds,
    )
    if offset and response.status_code != 206:
        partial.unlink(missing_ok=True)
        offset = 0
        response.close()
        response = requests.get(ARCHIVE_URL, stream=True, timeout=timeout_seconds)
    response.raise_for_status()
    if offset:
        content_range = response.headers.get("Content-Range", "")
        if not content_range.startswith(f"bytes {offset}-"):
            raise ProvisioningError(
                f"Unexpected Content-Range while resuming BIRD archive: {content_range}"
            )

    downloaded = offset
    mode = "ab" if offset else "wb"
    with open(partial, mode) as file_obj:
        for chunk in response.iter_content(chunk_size=4 * 1024 * 1024):
            if chunk:
                downloaded += len(chunk)
                if downloaded > ARCHIVE_SIZE:
                    raise ProvisioningError("BIRD archive exceeded its pinned size")
                file_obj.write(chunk)
        file_obj.flush()
        os.fsync(file_obj.fileno())
    if not _valid_archive(partial):
        raise ProvisioningError(
            f"BIRD archive failed size or MD5 validation at {partial}"
        )
    os.replace(partial, archive)
    return archive


def extract_archive(archive: Path, cache_dir: Path) -> tuple[Path, str]:
    archive_sha256 = _hash_file(archive, "sha256")
    destination = cache_dir / "extracted" / archive_sha256
    marker = destination / "extraction-manifest.json"
    if _extraction_valid(destination, marker, archive_sha256):
        return destination, archive_sha256

    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    selected = []
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            path = PurePosixPath(info.filename)
            _validate_zip_path(path)
            wanted = info.filename in {MYSQL_DUMP_ENTRY, TABLES_ENTRY} or (
                info.filename.startswith(DESCRIPTION_PREFIX)
                and "/database_description/" in info.filename
                and info.filename.lower().endswith(".csv")
            )
            if not wanted or info.is_dir():
                continue
            output = destination.joinpath(*path.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            with source.open(info) as input_file, open(output, "wb") as output_file:
                while chunk := input_file.read(4 * 1024 * 1024):
                    output_file.write(chunk)
            selected.append(
                {
                    "path": info.filename,
                    "size": info.file_size,
                    "crc32": f"{info.CRC:08x}",
                }
            )

    selected_paths = {entry["path"] for entry in selected}
    if MYSQL_DUMP_ENTRY not in selected_paths or TABLES_ENTRY not in selected_paths:
        raise ProvisioningError("BIRD archive is missing required MySQL entries")
    write_json(
        marker,
        {
            "archive_sha256": archive_sha256,
            "archive_md5": ARCHIVE_MD5,
            "entries": selected,
        },
    )
    return destination, archive_sha256


def start_mysql(*, root_password: str, port: int) -> None:
    env = _compose_env(root_password=root_password, port=port)
    command = _compose_command("up", "-d")
    subprocess.run(command, check=True, env=env)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            connection = _admin_connection(root_password, port)
        except pymysql.MySQLError:
            time.sleep(2)
            continue
        connection.close()
        return
    raise ProvisioningError("Timed out waiting for the BIRD MySQL container")


def provision_mysql(
    extracted_dir: Path,
    *,
    archive_sha256: str,
    cache_dir: Path,
    root_password: str = DEFAULT_ROOT_PASSWORD,
    runtime_user: str = "rdst_bird",
    runtime_password: str = "rdst-benchmark",
    port: int = 13316,
    database_prefix: str = "bird_",
    force: bool = False,
) -> dict[str, Any]:
    tables_path = extracted_dir.joinpath(*PurePosixPath(TABLES_ENTRY).parts)
    dump_path = extracted_dir.joinpath(*PurePosixPath(MYSQL_DUMP_ENTRY).parts)
    database_tables = _load_database_tables(tables_path)
    start_mysql(root_password=root_password, port=port)

    connection = _admin_connection(root_password, port)
    try:
        previous_user_prefix = _marker_runtime_user(connection)
        if previous_user_prefix and previous_user_prefix != runtime_user:
            _drop_runtime_users(connection, database_tables, previous_user_prefix)
        if (
            _provision_matches(connection, archive_sha256, database_prefix)
            and not force
        ):
            _create_runtime_users(
                connection,
                database_tables,
                database_prefix=database_prefix,
                user_prefix=runtime_user,
                password=runtime_password,
            )
            manifest = _provision_manifest(
                connection,
                archive_sha256=archive_sha256,
                database_prefix=database_prefix,
                runtime_user=runtime_user,
                port=port,
            )
            write_json(cache_dir / "mysql-provision.json", manifest)
            return manifest
        _drop_benchmark_databases(connection, database_tables, database_prefix)
        with connection.cursor() as cursor:
            cursor.execute("DROP DATABASE IF EXISTS `BIRD`")
            cursor.execute(
                "CREATE DATABASE `BIRD` CHARACTER SET utf8mb4 "
                "COLLATE utf8mb4_0900_ai_ci"
            )
        connection.commit()
    finally:
        connection.close()

    _import_dump(dump_path, root_password=root_password, port=port)
    connection = _admin_connection(root_password, port)
    try:
        _create_isolated_views(connection, database_tables, database_prefix)
        _write_provision_marker(
            connection, archive_sha256, database_prefix, runtime_user
        )
        _create_runtime_users(
            connection,
            database_tables,
            database_prefix=database_prefix,
            user_prefix=runtime_user,
            password=runtime_password,
        )
        manifest = _provision_manifest(
            connection,
            archive_sha256=archive_sha256,
            database_prefix=database_prefix,
            runtime_user=runtime_user,
            port=port,
        )
    finally:
        connection.close()
    write_json(cache_dir / "mysql-provision.json", manifest)
    return manifest


def verify_mysql_provision(
    cache_dir: Path, config: MySQLConnectionConfig, db_id: str
) -> dict[str, Any]:
    path = cache_dir / "mysql-provision.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvisioningError(
            f"MySQL provision manifest is unavailable at {path}; run prepare first"
        ) from exc
    expected = {
        "port": config.port,
        "database_prefix": config.database_prefix,
        "runtime_user_prefix": config.user,
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise ProvisioningError(
                f"MySQL provision manifest has {field}={manifest.get(field)!r}; "
                f"expected {value!r}"
            )

    connection = pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user_for(db_id),
        password=config.password,
        database=config.database_for(db_id),
        connect_timeout=5,
        read_timeout=10,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT archive_sha256, database_prefix "
                "FROM `rdst_benchmark_meta`.`provision` LIMIT 1"
            )
            marker = cursor.fetchone()
            cursor.execute("SELECT VERSION(), @@SESSION.sql_mode")
            version, sql_mode = cursor.fetchone()
    finally:
        connection.close()
    if marker != (manifest.get("archive_sha256"), config.database_prefix):
        raise ProvisioningError("MySQL server provenance does not match its manifest")
    if "ONLY_FULL_GROUP_BY" in str(sql_mode).split(","):
        raise ProvisioningError("MySQL server still enables ONLY_FULL_GROUP_BY")
    if str(version) != manifest.get("mysql_version"):
        raise ProvisioningError("MySQL server version does not match its manifest")
    return manifest


def _import_dump(dump_path: Path, *, root_password: str, port: int) -> None:
    env = _compose_env(root_password=root_password, port=port)
    command = _compose_command(
        "exec",
        "-T",
        "-e",
        f"MYSQL_PWD={root_password}",
        "mysql",
        "mysql",
        "--default-character-set=utf8mb4",
        "-uroot",
        "BIRD",
    )
    with open(dump_path, "rb") as dump:
        result = subprocess.run(command, stdin=dump, env=env, check=False)
    if result.returncode != 0:
        raise ProvisioningError(
            f"MySQL dump import failed with status {result.returncode}"
        )


def _load_database_tables(path: Path) -> dict[str, list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvisioningError(f"Unable to read {path}: {exc}") from exc
    result = {}
    for database in data:
        db_id = str(database["db_id"])
        tables = [str(table) for table in database["table_names_original"]]
        result[db_id] = tables
    if len(result) != 11:
        raise ProvisioningError(f"Expected 11 BIRD databases, found {len(result)}")
    return result


def _create_isolated_views(
    connection, database_tables: dict[str, list[str]], database_prefix: str
) -> None:
    with connection.cursor() as cursor:
        for db_id, tables in sorted(database_tables.items()):
            database = _identifier(f"{database_prefix}{db_id}")
            cursor.execute(
                f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 "
                "COLLATE utf8mb4_0900_ai_ci"
            )
            for table in tables:
                identifier = _identifier(table)
                cursor.execute(
                    f"CREATE SQL SECURITY DEFINER VIEW `{database}`.`{identifier}` "
                    f"AS SELECT * FROM `BIRD`.`{identifier}`"
                )
    connection.commit()


def _create_runtime_users(
    connection,
    database_tables: dict[str, list[str]],
    *,
    database_prefix: str,
    user_prefix: str,
    password: str,
) -> None:
    with connection.cursor() as cursor:
        for db_id in sorted(database_tables):
            user = database_user(user_prefix, db_id)
            database = _identifier(f"{database_prefix}{db_id}")
            cursor.execute(f"DROP USER IF EXISTS '{user}'@'%'")
            cursor.execute(f"CREATE USER '{user}'@'%%' IDENTIFIED BY %s", (password,))
            cursor.execute(f"GRANT SELECT, SHOW VIEW ON `{database}`.* TO '{user}'@'%'")
            cursor.execute(
                f"GRANT SELECT ON `rdst_benchmark_meta`.`provision` TO '{user}'@'%'"
            )
    connection.commit()


def _write_provision_marker(
    connection, archive_sha256: str, prefix: str, runtime_user_prefix: str
) -> None:
    with connection.cursor() as cursor:
        cursor.execute("CREATE DATABASE `rdst_benchmark_meta`")
        cursor.execute(
            "CREATE TABLE `rdst_benchmark_meta`.`provision` "
            "(archive_sha256 CHAR(64) NOT NULL, database_prefix VARCHAR(64) NOT NULL, "
            "runtime_user_prefix VARCHAR(32) NOT NULL)"
        )
        cursor.execute(
            "INSERT INTO `rdst_benchmark_meta`.`provision` VALUES (%s, %s, %s)",
            (archive_sha256, prefix, runtime_user_prefix),
        )
    connection.commit()


def _marker_runtime_user(connection) -> str | None:
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT runtime_user_prefix "
                "FROM `rdst_benchmark_meta`.`provision` LIMIT 1"
            )
            row = cursor.fetchone()
    except pymysql.MySQLError:
        return None
    return str(row[0]) if row else None


def _drop_runtime_users(
    connection, database_tables: dict[str, list[str]], user_prefix: str
) -> None:
    with connection.cursor() as cursor:
        for db_id in database_tables:
            user = database_user(user_prefix, db_id)
            cursor.execute(f"DROP USER IF EXISTS '{user}'@'%'")
    connection.commit()


def _provision_matches(connection, archive_sha256: str, prefix: str) -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT archive_sha256, database_prefix "
                "FROM `rdst_benchmark_meta`.`provision` LIMIT 1"
            )
            row = cursor.fetchone()
    except pymysql.MySQLError:
        return False
    return row == (archive_sha256, prefix)


def _drop_benchmark_databases(
    connection, database_tables: dict[str, list[str]], prefix: str
) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DROP DATABASE IF EXISTS `rdst_benchmark_meta`")
        for db_id in database_tables:
            database = _identifier(f"{prefix}{db_id}")
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
    connection.commit()


def _provision_manifest(
    connection,
    *,
    archive_sha256: str,
    database_prefix: str,
    runtime_user: str,
    port: int,
):
    with connection.cursor() as cursor:
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()[0]
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.VIEWS "
            "WHERE LEFT(TABLE_SCHEMA, %s) = %s",
            (len(database_prefix), database_prefix),
        )
        view_count = int(cursor.fetchone()[0])
    if view_count != 75:
        raise ProvisioningError(f"Expected 75 isolated views, found {view_count}")
    return {
        "archive_url": ARCHIVE_URL,
        "archive_sha256": archive_sha256,
        "archive_md5": ARCHIVE_MD5,
        "mysql_image": os.getenv("BIRD_MYSQL_IMAGE", DEFAULT_MYSQL_IMAGE),
        "mysql_image_id": _mysql_image_id(),
        "mysql_version": str(version),
        "port": port,
        "database_prefix": database_prefix,
        "runtime_user_prefix": runtime_user,
        "view_count": view_count,
    }


def _mysql_image_id() -> str:
    container_id = subprocess.run(
        _compose_command("ps", "-q", "mysql"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not container_id:
        raise ProvisioningError("BIRD MySQL container is unavailable")
    return subprocess.run(
        ["docker", "inspect", "--format", "{{.Image}}", container_id],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _admin_connection(password: str, port: int):
    return pymysql.connect(
        host="127.0.0.1",
        port=port,
        user="root",
        password=password,
        autocommit=False,
        connect_timeout=5,
        read_timeout=30,
        write_timeout=30,
    )


def _compose_command(*args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        os.getenv("BIRD_MYSQL_COMPOSE_PROJECT", COMPOSE_PROJECT),
        "-f",
        str(COMPOSE_FILE),
        *args,
    ]


def _compose_env(*, root_password: str, port: int) -> dict[str, str]:
    return {
        **os.environ,
        "BIRD_MYSQL_ROOT_PASSWORD": root_password,
        "BIRD_MYSQL_PORT": str(port),
    }


def _valid_archive(path: Path) -> bool:
    return (
        path.stat().st_size == ARCHIVE_SIZE and _hash_file(path, "md5") == ARCHIVE_MD5
    )


def _hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with open(path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extraction_valid(destination: Path, marker: Path, archive_sha256: str) -> bool:
    try:
        manifest = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if manifest.get("archive_sha256") != archive_sha256:
        return False
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            return False
        try:
            path = destination.joinpath(*PurePosixPath(entry["path"]).parts)
            expected_size = int(entry["size"])
        except (KeyError, TypeError, ValueError):
            return False
        if not path.is_file() or path.stat().st_size != expected_size:
            return False
        if f"{_crc32_file(path):08x}" != entry.get("crc32"):
            return False
    return True


def _crc32_file(path: Path) -> int:
    checksum = 0
    with open(path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(4 * 1024 * 1024), b""):
            checksum = zlib.crc32(chunk, checksum)
    return checksum & 0xFFFFFFFF


def _validate_zip_path(path: PurePosixPath) -> None:
    if path.is_absolute() or ".." in path.parts:
        raise ProvisioningError(f"Unsafe path in BIRD archive: {path}")


def _identifier(value: str) -> str:
    if not value or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
        for character in value
    ):
        raise ProvisioningError(f"Unsafe MySQL identifier: {value!r}")
    return value

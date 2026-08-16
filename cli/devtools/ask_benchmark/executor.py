from __future__ import annotations

import hashlib
import multiprocessing
import re
import time
from dataclasses import dataclass
from queue import Empty
from typing import Any

import pymysql

from .models import QueryResult

_DATABASE_NAME = re.compile(r"[A-Za-z0-9_]+")
_MYSQL_TIMEOUT_ERROR = 3024


@dataclass(frozen=True)
class QueryBounds:
    timeout_seconds: int = 30
    max_rows: int = 100_000
    max_result_bytes: int = 64 * 1024 * 1024


@dataclass(frozen=True)
class MySQLConnectionConfig:
    host: str
    port: int
    user: str
    password: str
    database_prefix: str = ""
    unix_socket: str | None = None

    def database_for(self, db_id: str) -> str:
        if not _DATABASE_NAME.fullmatch(db_id):
            raise ValueError(f"Unsafe BIRD database ID: {db_id!r}")
        return f"{self.database_prefix}{db_id}"

    def user_for(self, db_id: str) -> str:
        return database_user(self.user, db_id)


class MySQLExecutor:
    def __init__(
        self, config: MySQLConnectionConfig, bounds: QueryBounds | None = None
    ):
        self.config = config
        self.bounds = bounds or QueryBounds()

    def execute(self, sql: str, *, db_id: str) -> QueryResult:
        start = time.perf_counter()
        methods = multiprocessing.get_all_start_methods()
        context = multiprocessing.get_context("fork" if "fork" in methods else "spawn")
        result_queue = context.Queue(maxsize=1)
        process = context.Process(
            target=_execute_worker,
            args=(self.config, self.bounds, sql, db_id, result_queue),
            daemon=True,
        )
        process.start()
        grace_seconds = min(5.0, max(0.1, self.bounds.timeout_seconds * 0.1))
        try:
            status, payload = result_queue.get(
                timeout=self.bounds.timeout_seconds + grace_seconds
            )
        except Empty:
            process.terminate()
            process.join(2)
            if process.is_alive():
                process.kill()
                process.join()
            result_queue.close()
            return QueryResult(
                execution_time_ms=_elapsed_ms(start),
                error="Query exceeded benchmark wall-clock timeout",
                timed_out=True,
            )
        process.join(2)
        if process.is_alive():
            process.terminate()
            process.join()
        result_queue.close()
        if status == "error":
            raise RuntimeError(f"Query worker failed: {payload}")
        return payload

    def _execute_direct(self, sql: str, *, db_id: str) -> QueryResult:
        start = time.perf_counter()
        connection = None
        try:
            connection = pymysql.connect(
                host=self.config.host,
                port=self.config.port,
                user=self.config.user_for(db_id),
                password=self.config.password,
                database=self.config.database_for(db_id),
                cursorclass=pymysql.cursors.SSCursor,
                unix_socket=self.config.unix_socket,
                autocommit=False,
                connect_timeout=self.bounds.timeout_seconds,
                read_timeout=self.bounds.timeout_seconds + 5,
                write_timeout=self.bounds.timeout_seconds + 5,
            )
            cursor = connection.cursor()
            timeout_ms = self.bounds.timeout_seconds * 1000
            cursor.execute(f"SET SESSION max_execution_time = {timeout_ms}")
            cursor.execute(
                "SET SESSION sql_mode = "
                "REPLACE(@@SESSION.sql_mode, 'ONLY_FULL_GROUP_BY', '')"
            )
            cursor.execute("START TRANSACTION READ ONLY")
            cursor.execute(sql)
            columns = tuple(
                description[0] for description in (cursor.description or ())
            )
            rows, too_large = self._fetch_bounded(cursor)
            if too_large:
                connection.close()
                connection = None
                return QueryResult(
                    execution_time_ms=_elapsed_ms(start),
                    error="Query result exceeded benchmark bounds",
                    result_too_large=True,
                )
            cursor.close()
            return QueryResult(
                columns=columns,
                rows=tuple(rows),
                execution_time_ms=_elapsed_ms(start),
            )
        except pymysql.err.OperationalError as exc:
            code = exc.args[0] if exc.args else None
            timed_out = code == _MYSQL_TIMEOUT_ERROR or "timed out" in str(exc).lower()
            return QueryResult(
                execution_time_ms=_elapsed_ms(start),
                error=str(exc),
                timed_out=timed_out,
            )
        except pymysql.MySQLError as exc:
            return QueryResult(execution_time_ms=_elapsed_ms(start), error=str(exc))
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    connection.close()

    def as_ask_executor(self, db_id: str):
        def execute(sql: str, _target_config: dict[str, Any]):
            result = self.execute(sql, db_id=db_id)
            error_kind = None
            if result.timed_out:
                error_kind = "timeout"
            elif result.result_too_large:
                error_kind = "result_too_large"
            return {
                "success": result.succeeded,
                "rows": [list(row) for row in result.rows],
                "columns": list(result.columns),
                "error": result.error,
                "error_kind": error_kind,
            }

        return execute

    def _fetch_bounded(self, cursor):
        rows: list[tuple[Any, ...]] = []
        result_bytes = 0
        while row := cursor.fetchone():
            if len(rows) >= self.bounds.max_rows:
                return [], True
            result_bytes += _row_size(row)
            if result_bytes > self.bounds.max_result_bytes:
                return [], True
            rows.append(tuple(row))
        return rows, False


def _execute_worker(config, bounds, sql: str, db_id: str, result_queue) -> None:
    try:
        result = MySQLExecutor(config, bounds)._execute_direct(sql, db_id=db_id)
    except Exception as exc:  # noqa: BLE001 - propagate worker failures to parent
        result_queue.put(("error", repr(exc)))
    else:
        result_queue.put(("ok", result))


def database_user(prefix: str, db_id: str) -> str:
    if not _DATABASE_NAME.fullmatch(prefix) or not _DATABASE_NAME.fullmatch(db_id):
        raise ValueError("Unsafe MySQL benchmark user prefix or database ID")
    suffix = hashlib.sha256(db_id.encode()).hexdigest()[:12]
    user = f"{prefix}_{suffix}"
    if len(user) > 32:
        raise ValueError("MySQL benchmark user prefix is too long")
    return user


def _row_size(row: tuple[Any, ...]) -> int:
    total = 0
    for value in row:
        if value is None:
            total += 1
        elif isinstance(value, bytes):
            total += len(value)
        elif isinstance(value, str):
            total += len(value.encode("utf-8"))
        else:
            total += len(str(value).encode("utf-8"))
    return total


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000

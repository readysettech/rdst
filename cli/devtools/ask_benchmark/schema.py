from __future__ import annotations

from collections import defaultdict

import pymysql

from features.ask.engine.ask3.phases.schema import _format_semantic_schema
from features.schema.semantic_layer.manager import SemanticLayerManager

from .executor import MySQLConnectionConfig


def load_semantic_schema(base_dir, target: str) -> str:
    manager = SemanticLayerManager(base_dir=base_dir)
    if not manager.exists(target):
        raise ValueError(f"Semantic layer is missing for {target}")
    return _format_semantic_schema(manager.load(target))


class MySQLSchemaLoader:
    def __init__(self, config: MySQLConnectionConfig):
        self.config = config
        self._cache: dict[str, str] = {}

    def load(self, db_id: str) -> str:
        if db_id not in self._cache:
            self._cache[db_id] = self._load(db_id)
        return self._cache[db_id]

    def _load(self, db_id: str) -> str:
        database = self.config.database_for(db_id)
        connection = pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user_for(db_id),
            password=self.config.password,
            database=database,
            unix_socket=self.config.unix_socket,
            connect_timeout=10,
            read_timeout=30,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE,
                           COLUMN_KEY, EXTRA
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = %s
                    ORDER BY TABLE_NAME, ORDINAL_POSITION
                    """,
                    (database,),
                )
                columns = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT TABLE_NAME, COLUMN_NAME,
                           REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                    FROM information_schema.KEY_COLUMN_USAGE
                    WHERE TABLE_SCHEMA = %s
                      AND REFERENCED_TABLE_NAME IS NOT NULL
                    ORDER BY TABLE_NAME, COLUMN_NAME
                    """,
                    (database,),
                )
                foreign_keys = cursor.fetchall()
        finally:
            connection.close()

        tables: dict[str, list[str]] = defaultdict(list)
        for table, column, column_type, nullable, column_key, extra in columns:
            attributes = [str(column_type)]
            if nullable == "NO":
                attributes.append("NOT NULL")
            if column_key == "PRI":
                attributes.append("PRIMARY KEY")
            if extra:
                attributes.append(str(extra).upper())
            tables[str(table)].append(f"  `{column}` {' '.join(attributes)}")

        relations: dict[str, list[str]] = defaultdict(list)
        for table, column, target_table, target_column in foreign_keys:
            relations[str(table)].append(
                f"  FOREIGN KEY (`{column}`) REFERENCES "
                f"`{target_table}` (`{target_column}`)"
            )

        parts = ["Dialect: MySQL"]
        for table in sorted(tables):
            parts.append(f"\nTable: `{table}`")
            parts.extend(tables[table])
            parts.extend(relations[table])
        if len(parts) == 1:
            raise ValueError(f"No tables found in benchmark database {database}")
        return "\n".join(parts)

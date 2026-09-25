"""The ci-shop schema and its deterministic seed, identical on PostgreSQL and MySQL.

About 150k rows in seven tables. Every value is arithmetic on the row id, so a seed is the same on
every run and on both engines. Only three secondary indexes exist; the heavy queries in the
catalogue filter on columns without one.
"""

from __future__ import annotations

from typing import Any, Iterable

REGIONS = ("north", "south", "east", "west", "central", "islands")
SEGMENTS = ("retail", "wholesale", "partner", "staff")
CATEGORIES = tuple(f"cat{index:02d}" for index in range(12))
STATUSES = ("new", "paid", "packed", "shipped", "returned")
CHANNELS = ("web", "app", "store", "phone")
METHODS = ("card", "wallet", "transfer", "voucher")
PAYMENT_STATES = ("pending", "settled", "failed", "refunded")
EVENT_KINDS = ("view", "cart", "search", "wishlist", "review", "share", "return", "support")
DEVICES = ("desktop", "mobile", "tablet")

# Every table a heavy query scans holds at least 12k rows, so a scan costs well above an indexed
# lookup on both engines; MySQL scans a few thousand rows about as fast as it opens a row.
# order_items is the largest, so dropping the regression query's index costs over ten times its
# indexed latency on both engines.
SIZES = {"customers": 24000, "products": 1000, "orders": 18000, "order_items": 50000,
         "payments": 24000, "events": 24000, "sessions": 12000}

TABLES = {
    "customers": ("id integer PRIMARY KEY, email varchar(80) NOT NULL, region varchar(16) NOT NULL, "
                  "segment varchar(16) NOT NULL, signup_day integer NOT NULL, score integer NOT NULL"),
    "products": ("id integer PRIMARY KEY, sku varchar(24) NOT NULL, category varchar(24) NOT NULL, "
                 "price_cents integer NOT NULL, stock integer NOT NULL"),
    "orders": ("id integer PRIMARY KEY, customer_id integer NOT NULL, status varchar(16) NOT NULL, "
               "channel varchar(16) NOT NULL, total_cents integer NOT NULL, "
               "created_day integer NOT NULL"),
    "order_items": ("id integer PRIMARY KEY, order_id integer NOT NULL, product_id integer NOT NULL, "
                    "qty integer NOT NULL, price_cents integer NOT NULL"),
    "payments": ("id integer PRIMARY KEY, order_id integer NOT NULL, method varchar(16) NOT NULL, "
                 "amount_cents integer NOT NULL, state varchar(16) NOT NULL"),
    "events": ("id integer PRIMARY KEY, customer_id integer NOT NULL, kind varchar(24) NOT NULL, "
               "ts_day integer NOT NULL, payload_len integer NOT NULL"),
    "sessions": ("id integer PRIMARY KEY, customer_id integer NOT NULL, device varchar(16) NOT NULL, "
                 "duration_s integer NOT NULL"),
}

# (name, table, column). The regression scenario drops and restores REGRESSION_INDEX.
INDEXES = (("ci_orders_customer_id", "orders", "customer_id"),
           ("ci_order_items_order_id", "order_items", "order_id"),
           ("ci_payments_state", "payments", "state"))
REGRESSION_INDEX = INDEXES[1]


def _rows(table: str) -> Iterable[tuple[Any, ...]]:
    count = SIZES[table]
    for i in range(1, count + 1):
        if table == "customers":
            yield (i, f"c{i}@shop.test", REGIONS[i % 6], SEGMENTS[(i * 7) % 4], i % 720,
                   (i * 37) % 1000)
        elif table == "products":
            yield (i, f"SKU-{i:05d}", CATEGORIES[i % 12], 100 + (i * 97) % 9900, (i * 13) % 500)
        elif table == "orders":
            yield (i, 1 + (i * 7919) % SIZES["customers"], STATUSES[(i * 31) % 5],
                   CHANNELS[(i * 17) % 4], 500 + (i * 7727) % 50000, i % 365)
        elif table == "order_items":
            yield (i, 1 + (i - 1) % SIZES["orders"], 1 + (i * 6007) % SIZES["products"],
                   1 + i % 5, 100 + (i * 389) % 9900)
        elif table == "payments":
            yield (i, 1 + (i * 3) % SIZES["orders"], METHODS[i % 4], 500 + (i * 911) % 40000,
                   PAYMENT_STATES[(i * 11) % 4])
        elif table == "events":
            yield (i, 1 + (i * 2749) % SIZES["customers"], EVENT_KINDS[(i * 5) % 8], i % 365,
                   (i * 53) % 4000)
        elif table == "sessions":
            yield (i, 1 + (i * 4271) % SIZES["customers"], DEVICES[i % 3], (i * 61) % 3600)


def seed(connection: Any, engine: str) -> int:
    """Create and fill the schema on an open connection in autocommit mode; returns rows."""
    total = 0
    with connection.cursor() as cursor:
        for table, columns in TABLES.items():
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
            cursor.execute(f"CREATE TABLE {table} ({columns})")
            rows = list(_rows(table))
            if engine == "postgresql":
                from psycopg2.extras import execute_values

                execute_values(cursor, f"INSERT INTO {table} VALUES %s", rows, page_size=2000)
            else:
                placeholders = ", ".join(["%s"] * len(rows[0]))
                for start in range(0, len(rows), 2000):
                    cursor.executemany(f"INSERT INTO {table} VALUES ({placeholders})",
                                       rows[start:start + 2000])
            total += len(rows)
        for name, table, column in INDEXES:
            cursor.execute(create_index_sql(name, table, column))
        for table in TABLES:
            cursor.execute(f"ANALYZE TABLE {table}" if engine == "mysql" else f"ANALYZE {table}")
            if engine == "mysql":
                cursor.fetchall()
    return total


def create_index_sql(name: str, table: str, column: str) -> str:
    return f"CREATE INDEX {name} ON {table} ({column})"


def drop_index_sql(engine: str, name: str, table: str) -> str:
    return f"DROP INDEX {name} ON {table}" if engine == "mysql" else f"DROP INDEX {name}"

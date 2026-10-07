"""The app-sim shop schema, its indexes and a deterministic seed, identical on both engines.

Planted index problems live in INDEXES (what exists) and in what's missing from it; each one is
tagged with its plant id from expected.py.

At scale 1.0 the seed is about 1.5M rows: roughly 200 MB on PostgreSQL and 250 MB on MySQL.
Every row comes from a per-table RNG seeded with a string, so a seed is byte-identical across
runs and engines. Seeded timestamps sit in the year before ANCHOR; live traffic writes real now().
"""

from __future__ import annotations

import hashlib
import io
import random
from datetime import datetime, timedelta
from typing import Any, Iterable, Iterator

ANCHOR = datetime(2026, 1, 1)
SEED_DAYS = 365
WAREHOUSES = 3

# Base row counts at scale 1.0. inventory is products x WAREHOUSES.
BASE_SIZES = {
    "categories": 200,
    "products": 20000,
    "customers": 80000,
    "sessions": 60000,
    "carts": 40000,
    "cart_items": 80000,
    "orders": 160000,
    "order_items": 440000,
    "payments": 160000,
    "reviews": 50000,
    "events": 400000,
}

REGIONS = ("north", "south", "east", "west", "central", "islands")
SEGMENTS = ("retail", "wholesale", "partner", "staff")
ORDER_STATUSES = ("paid", "packed", "shipped", "delivered", "returned")
METHODS = ("card", "wallet", "transfer", "voucher")
PAYMENT_STATES = ("settled", "settled", "settled", "refunded", "failed")
CART_STATUSES = ("converted", "abandoned", "converted", "abandoned", "converted", "abandoned",
                 "converted", "abandoned", "converted", "abandoned", "converted", "abandoned",
                 "converted", "abandoned", "converted", "abandoned", "converted", "abandoned",
                 "converted", "open")
EVENT_KINDS = ("page_view", "product_view", "search", "add_to_cart", "checkout", "login")
PATHS = ("/", "/c/", "/p/", "/search", "/cart", "/checkout", "/account/orders", "/login")
BRANDS = ("acme", "globex", "initech", "umbrella", "stark", "wayne", "tyrell", "cyberdyne",
          "soylent", "hooli", "vandelay", "wonka")
ADJECTIVES = ("red", "blue", "green", "quiet", "rapid", "solid", "smart", "classic", "urban",
              "vintage", "compact", "deluxe", "eco", "pro", "ultra", "mini", "grand", "royal",
              "wild", "lunar")
NOUNS = ("kettle", "lamp", "chair", "backpack", "headphones", "mug", "blender", "jacket",
         "sneaker", "watch", "desk", "speaker", "tent", "bottle", "camera", "drone", "scarf",
         "wallet", "router", "keyboard", "pillow", "skillet", "bike", "helmet", "notebook")
AGENTS = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Mozilla/5.0 (Macintosh; Intel Mac OS X)",
          "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)", "Mozilla/5.0 (Linux; Android 14)")

# Column specs use {pk} and {ts}, filled per engine.
TABLES = {
    "categories": ("id {pk}, parent_id integer, name varchar(60) NOT NULL, "
                   "slug varchar(60) NOT NULL"),
    "products": ("id {pk}, category_id integer NOT NULL, sku varchar(24) NOT NULL, "
                 "slug varchar(120) NOT NULL, name varchar(120) NOT NULL, "
                 "brand varchar(40) NOT NULL, "
                 "price_cents integer NOT NULL, status varchar(16) NOT NULL, "
                 "popularity integer NOT NULL, description varchar(255) NOT NULL, "
                 "created_at {ts} NOT NULL"),
    "inventory": ("product_id bigint NOT NULL, warehouse_id integer NOT NULL, "
                  "quantity integer NOT NULL, updated_at {ts} NOT NULL, "
                  "PRIMARY KEY (product_id, warehouse_id)"),
    "customers": ("id {pk}, email varchar(120) NOT NULL, name varchar(80) NOT NULL, "
                  "phone varchar(24) NOT NULL, segment varchar(16) NOT NULL, "
                  "region varchar(16) NOT NULL, created_at {ts} NOT NULL, last_login_at {ts}"),
    "sessions": ("id {pk}, customer_id bigint NOT NULL, token varchar(64) NOT NULL, "
                 "user_agent varchar(80) NOT NULL, created_at {ts} NOT NULL, "
                 "last_seen_at {ts} NOT NULL, expires_at {ts} NOT NULL"),
    "carts": ("id {pk}, customer_id bigint NOT NULL, status varchar(16) NOT NULL, "
              "created_at {ts} NOT NULL, updated_at {ts} NOT NULL, expires_at {ts} NOT NULL"),
    "cart_items": ("id {pk}, cart_id bigint NOT NULL, product_id bigint NOT NULL, "
                   "qty integer NOT NULL, added_at {ts} NOT NULL"),
    "orders": ("id {pk}, customer_id bigint NOT NULL, status varchar(16) NOT NULL, "
               "total_cents integer NOT NULL, shipping_region varchar(16) NOT NULL, "
               "created_at {ts} NOT NULL, updated_at {ts} NOT NULL"),
    "order_items": ("id {pk}, order_id bigint NOT NULL, product_id bigint NOT NULL, "
                    "qty integer NOT NULL, price_cents integer NOT NULL"),
    "payments": ("id {pk}, order_id bigint NOT NULL, method varchar(16) NOT NULL, "
                 "amount_cents integer NOT NULL, state varchar(16) NOT NULL, "
                 "created_at {ts} NOT NULL, settled_at {ts}"),
    "reviews": ("id {pk}, product_id bigint NOT NULL, customer_id bigint NOT NULL, "
                "rating smallint NOT NULL, body varchar(255) NOT NULL, created_at {ts} NOT NULL"),
    "events": ("id {pk}, customer_id bigint, session_id bigint, kind varchar(24) NOT NULL, "
               "path varchar(120) NOT NULL, payload varchar(255) NOT NULL, "
               "created_at {ts} NOT NULL"),
}

TYPES = {
    "postgresql": {"pk": "bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
                   "ts": "timestamp"},
    "mysql": {"pk": "bigint AUTO_INCREMENT PRIMARY KEY", "ts": "datetime"},
}

# (name, table, columns, unique, plant). Missing on purpose: products(slug) P1,
# orders(customer_id, created_at) P2, events(session_id) P5, sessions(token) P6,
# carts(expires_at) P7.
INDEXES = (
    ("customers_email_key", "customers", ("email",), True, None),
    ("products_category_idx", "products", ("category_id",), False, None),
    ("sessions_customer_idx", "sessions", ("customer_id",), False, None),
    ("carts_customer_idx", "carts", ("customer_id",), False, None),
    ("cart_items_cart_idx", "cart_items", ("cart_id",), False, None),
    # P3: a leading prefix of the next index, so it's redundant.
    ("order_items_order_idx", "order_items", ("order_id",), False, "P3"),
    ("order_items_order_product_idx", "order_items", ("order_id", "product_id"), False, None),
    ("payments_order_idx", "payments", ("order_id",), False, None),
    # P10: only the nightly settlement batch uses it, in bursts.
    ("payments_created_at_idx", "payments", ("created_at",), False, "P10"),
    ("reviews_product_created_idx", "reviews", ("product_id", "created_at"), False, None),
    # P4: no query filters or sorts on events.kind; every event insert still maintains it.
    ("events_kind_idx", "events", ("kind",), False, "P4"),
)


def sizes(scale: float = 1.0) -> dict[str, int]:
    out = {table: max(4, int(count * scale)) for table, count in BASE_SIZES.items()}
    out["inventory"] = out["products"] * WAREHOUSES
    return out


def columns(table: str) -> list[str]:
    spec = TABLES[table].split(", PRIMARY KEY")[0]
    return [part.strip().split()[0] for part in spec.split(",")]


def ddl(table: str, engine: str) -> str:
    return f"CREATE TABLE {table} ({TABLES[table].format(**TYPES[engine])})"


def index_ddl(name: str, table: str, cols: Iterable[str], unique: bool) -> str:
    kind = "UNIQUE INDEX" if unique else "INDEX"
    return f"CREATE {kind} {name} ON {table} ({', '.join(cols)})"


# Deterministic helpers the traffic driver shares with the seed.

def session_token(session_id: int) -> str:
    return hashlib.md5(f"appsim-session-{session_id}".encode()).hexdigest()


def session_customer(session_id: int, n_customers: int) -> int:
    return 1 + (session_id * 7919) % n_customers


def product_slug(product_id: int) -> str:
    adj = ADJECTIVES[product_id % len(ADJECTIVES)]
    noun = NOUNS[(product_id // len(ADJECTIVES)) % len(NOUNS)]
    return f"{adj}-{noun}-{product_id}"


def product_category(product_id: int, n_categories: int) -> int:
    return 1 + (product_id * 31) % n_categories


def customer_email(customer_id: int) -> str:
    return f"user{customer_id}@example.com"


def _ts(rng: random.Random, days: int = SEED_DAYS) -> datetime:
    return ANCHOR - timedelta(seconds=rng.randrange(days * 86400))


def rows(table: str, n: dict[str, int], seed: int = 7) -> Iterator[tuple[Any, ...]]:
    rng = random.Random(f"appsim:{table}:{seed}")
    count = n[table]
    if table == "categories":
        for i in range(1, count + 1):
            parent = None if i <= 20 else 1 + i % 20
            yield (i, parent, f"Category {i}", f"category-{i}")
    elif table == "products":
        for i in range(1, count + 1):
            slug = product_slug(i)
            yield (i, product_category(i, n["categories"]), f"SKU-{i:06d}", slug,
                   slug.replace("-", " ").title(), BRANDS[rng.randrange(len(BRANDS))],
                   rng.randrange(299, 49999), "active" if rng.random() < 0.95 else "retired",
                   int(rng.paretovariate(1.5) * 10),
                   f"A {slug.replace('-', ' ')} for everyday use.",
                   _ts(rng, 900))
    elif table == "inventory":
        for i in range(1, n["products"] + 1):
            for w in range(1, WAREHOUSES + 1):
                yield (i, w, rng.randrange(0, 400), _ts(rng, 30))
    elif table == "customers":
        for i in range(1, count + 1):
            created = _ts(rng, 900)
            yield (i, customer_email(i), f"Customer {i}", f"+1555{i:07d}",
                   SEGMENTS[rng.randrange(4)], REGIONS[rng.randrange(6)], created,
                   created + timedelta(days=rng.randrange(1, 60)))
    elif table == "sessions":
        for i in range(1, count + 1):
            created = _ts(rng, 30)
            yield (i, session_customer(i, n["customers"]), session_token(i),
                   AGENTS[i % len(AGENTS)], created, created + timedelta(minutes=rng.randrange(90)),
                   created + timedelta(days=30))
    elif table == "carts":
        for i in range(1, count + 1):
            created = _ts(rng, 60)
            yield (i, 1 + rng.randrange(n["customers"]), CART_STATUSES[i % len(CART_STATUSES)],
                   created, created + timedelta(minutes=rng.randrange(120)),
                   created + timedelta(days=2))
    elif table == "cart_items":
        for i in range(1, count + 1):
            yield (i, 1 + (i - 1) % n["carts"], 1 + rng.randrange(n["products"]),
                   1 + rng.randrange(3), _ts(rng, 60))
    elif table == "orders":
        for i in range(1, count + 1):
            created = _ts(rng)
            yield (i, _skewed(rng, n["customers"]), ORDER_STATUSES[rng.randrange(5)],
                   rng.randrange(500, 90000), REGIONS[rng.randrange(6)], created,
                   created + timedelta(days=rng.randrange(1, 9)))
    elif table == "order_items":
        for i in range(1, count + 1):
            order_id = 1 + (i - 1) * n["orders"] // count
            yield (i, order_id, 1 + rng.randrange(n["products"]), 1 + rng.randrange(3),
                   rng.randrange(299, 29999))
    elif table == "payments":
        for i in range(1, count + 1):
            created = _ts(rng)
            state = PAYMENT_STATES[rng.randrange(len(PAYMENT_STATES))]
            yield (i, i, METHODS[rng.randrange(4)], rng.randrange(500, 90000), state, created,
                   created + timedelta(hours=rng.randrange(1, 48)) if state == "settled" else None)
    elif table == "reviews":
        for i in range(1, count + 1):
            yield (i, 1 + rng.randrange(n["products"]), 1 + rng.randrange(n["customers"]),
                   1 + rng.randrange(5), "Solid product, would buy again." * (1 + i % 3),
                   _ts(rng, 900))
    elif table == "events":
        for i in range(1, count + 1):
            session_id = 1 + rng.randrange(n["sessions"])
            kind = EVENT_KINDS[rng.randrange(len(EVENT_KINDS))]
            yield (i, session_customer(session_id, n["customers"]), session_id, kind,
                   PATHS[rng.randrange(len(PATHS))], f'{{"ref":"seed","n":{i % 997}}}',
                   _ts(rng, 30))
    else:
        raise KeyError(table)


def _skewed(rng: random.Random, n: int) -> int:
    """Most activity comes from a minority of customers."""
    return 1 + int(n * rng.random() ** 2)


def _copy_value(value: Any) -> str:
    if value is None:
        return "\\N"
    return str(value).replace("\\", "\\\\").replace("\t", " ").replace("\n", " ")


def seed(connection: Any, engine: str, scale: float = 1.0, seed_value: int = 7,
         log: Any = print) -> int:
    """Create and fill the schema on an open autocommit connection; returns rows written."""
    n = sizes(scale)
    total = 0
    with connection.cursor() as cursor:
        for table in reversed(list(TABLES)):
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
        for table in TABLES:
            cursor.execute(ddl(table, engine))
            cols = columns(table)
            batch: list[tuple[Any, ...]] = []
            written = 0

            def flush() -> None:
                if not batch:
                    return
                if engine == "postgresql":
                    buf = io.StringIO("".join("\t".join(_copy_value(v) for v in row) + "\n"
                                              for row in batch))
                    cursor.copy_expert(f"COPY {table} ({', '.join(cols)}) FROM STDIN", buf)
                else:
                    marks = ", ".join(["%s"] * len(cols))
                    cursor.executemany(
                        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})", batch)
                batch.clear()

            for row in rows(table, n, seed_value):
                batch.append(row)
                written += 1
                if len(batch) >= 5000:
                    flush()
            flush()
            if engine == "postgresql" and "{pk}" in TABLES[table]:
                cursor.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                               f"(SELECT max(id) FROM {table}))")
            total += written
            log(f"  {engine}: {table} {written} rows")
        for name, table, cols, unique, _plant in INDEXES:
            cursor.execute(index_ddl(name, table, cols, unique))
        for table in TABLES:
            if engine == "mysql":
                cursor.execute(f"ANALYZE TABLE {table}")
                cursor.fetchall()
            else:
                cursor.execute(f"VACUUM ANALYZE {table}")
    return total

"""The ci-shop query catalogue: the same statement text and rates on PostgreSQL and MySQL.

Roles:
- heavy (10): filters on columns with no index, so Deep Analyze has a real fix to find.
- medium (13) plus the report group (6 burst members with a trickle, 1 burst-only): 20.
- cheap (25): primary-key lookups; one of them is the noise query.
- write (5): UPDATE, INSERT and DELETE, never analyzed.
- new_heavy: runs only in the new-heavy phase.
- stopped: a payments INSERT that exists only in the backdated history, which ends 32 minutes
  before the run, so the live monitor finds a steady write that has gone quiet for longer than
  its 30-minute rule.

The regression query is an indexed lookup frequent enough to rank with the heavy queries by
database time; the regression phase drops its index.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Callable

from tests.web_e2e.ci_shop.schema import (
    CHANNELS,
    DEVICES,
    EVENT_KINDS,
    PAYMENT_STATES,
    SEGMENTS,
    SIZES,
    STATUSES,
)


@dataclass(frozen=True)
class Shape:
    name: str
    role: str
    sql: str
    params: Callable[[random.Random], tuple]
    rate: float = 0.0
    burst_rate: float = 0.0
    # Mean latency the backdated history uses when the live run hasn't measured the shape.
    default_ms: float = 0.5


def _id(table: str) -> Callable[[random.Random], tuple]:
    return lambda rng: (rng.randint(1, SIZES[table]),)


def _range(table: str, width: int) -> Callable[[random.Random], tuple]:
    def params(rng: random.Random) -> tuple:
        start = rng.randint(1, SIZES[table] - width)
        return (start, start + width)
    return params


def _pick(values: tuple[str, ...]) -> Callable[[random.Random], str]:
    return lambda rng: values[rng.randrange(len(values))]


HEAVY = [
    Shape("h01_orders_by_status_channel", "heavy",
          "SELECT id, customer_id, total_cents FROM orders WHERE status = %s AND channel = %s "
          "ORDER BY created_day DESC LIMIT 20",
          lambda rng: (_pick(STATUSES)(rng), _pick(CHANNELS)(rng))),
    Shape("h02_items_by_product", "heavy",
          "SELECT id, order_id, qty FROM order_items WHERE product_id = %s",
          _id("products")),
    Shape("h03_events_by_customer", "heavy",
          "SELECT id, kind, ts_day FROM events WHERE customer_id = %s ORDER BY ts_day DESC LIMIT 10",
          _id("customers")),
    Shape("h04_payments_by_order", "heavy",
          "SELECT id, method, amount_cents FROM payments WHERE order_id = %s",
          _id("orders")),
    Shape("h05_customer_by_email", "heavy",
          "SELECT id, region, segment FROM customers WHERE email = %s",
          lambda rng: (f"c{rng.randint(1, SIZES['customers'])}@shop.test",)),
    Shape("h06_orders_in_days", "heavy",
          "SELECT count(*) FROM orders WHERE created_day BETWEEN %s AND %s AND status = %s",
          lambda rng: ((day := rng.randint(0, 300)), day + 30, _pick(STATUSES)(rng))),
    Shape("h07_sessions_by_customer", "heavy",
          "SELECT id, device, duration_s FROM sessions WHERE customer_id = %s",
          _id("customers")),
    Shape("h08_big_orders_by_channel", "heavy",
          "SELECT id, total_cents FROM orders WHERE total_cents > %s AND channel = %s "
          "ORDER BY total_cents DESC LIMIT 5",
          lambda rng: (rng.randint(30000, 45000), _pick(CHANNELS)(rng))),
    Shape("h09_orders_by_segment", "heavy",
          "SELECT o.id, o.total_cents, c.region FROM orders o JOIN customers c "
          "ON c.id = o.customer_id WHERE c.segment = %s AND o.status = %s ORDER BY o.id DESC LIMIT 50",
          lambda rng: (_pick(SEGMENTS)(rng), _pick(STATUSES)(rng))),
    Shape("h10_items_by_price", "heavy",
          "SELECT id, qty, price_cents FROM order_items WHERE price_cents BETWEEN %s AND %s "
          "ORDER BY price_cents LIMIT 25",
          lambda rng: ((low := rng.randint(100, 9000)), low + 50)),
]
for index, shape in enumerate(HEAVY):
    HEAVY[index] = Shape(shape.name, shape.role, shape.sql, shape.params, rate=0.4, default_ms=4.0)

REGRESSION = "m02_items_by_order"

MEDIUM = [
    Shape("m01_orders_by_customer", "medium",
          "SELECT id, status, total_cents FROM orders WHERE customer_id = %s", _id("customers"),
          rate=0.8),
    Shape(REGRESSION, "medium",
          "SELECT id, product_id, qty, price_cents FROM order_items WHERE order_id = %s",
          _id("orders"), rate=8.0, default_ms=0.1),
    Shape("m03_orders_by_customer_status", "medium",
          "SELECT count(*) FROM orders WHERE customer_id = %s AND status = %s",
          lambda rng: (rng.randint(1, SIZES["customers"]), _pick(STATUSES)(rng)), rate=0.8),
    Shape("m04_payments_by_state", "medium",
          "SELECT id, amount_cents FROM payments WHERE state = %s ORDER BY id DESC LIMIT 20",
          lambda rng: (_pick(PAYMENT_STATES)(rng),), rate=0.8),
    Shape("m05_products_in_category", "medium",
          "SELECT id, sku, price_cents FROM products WHERE category = %s ORDER BY price_cents LIMIT 20",
          lambda rng: (f"cat{rng.randrange(12):02d}",), rate=0.8),
    Shape("m06_customer_spend", "medium",
          "SELECT sum(total_cents) FROM orders WHERE customer_id = %s", _id("customers"), rate=0.8),
    Shape("m07_events_range", "medium",
          "SELECT id, kind FROM events WHERE id BETWEEN %s AND %s", _range("events", 40), rate=0.8),
    Shape("m08_customers_range", "medium",
          "SELECT id, email FROM customers WHERE id BETWEEN %s AND %s", _range("customers", 40),
          rate=0.8),
    Shape("m09_orders_range", "medium",
          "SELECT id, customer_id FROM orders WHERE id BETWEEN %s AND %s ORDER BY id",
          _range("orders", 40), rate=0.8),
    Shape("m10_items_per_order", "medium",
          "SELECT order_id, sum(qty) FROM order_items WHERE order_id BETWEEN %s AND %s "
          "GROUP BY order_id", _range("orders", 40), rate=0.8),
    Shape("m11_low_stock", "medium",
          "SELECT id, stock FROM products WHERE stock < %s ORDER BY stock LIMIT 10",
          lambda rng: (rng.randint(5, 40),), rate=0.8),
    Shape("m12_sessions_range", "medium",
          "SELECT id, duration_s FROM sessions WHERE id BETWEEN %s AND %s", _range("sessions", 40),
          rate=0.8),
    Shape("m13_payments_range", "medium",
          "SELECT id, state FROM payments WHERE id BETWEEN %s AND %s", _range("payments", 40),
          rate=0.8),
]

# The report group: aggregates a nightly-report job would run. Six keep a trickle all day, one
# runs only inside bursts.
BURST = [
    Shape("b1_orders_by_status", "burst", "SELECT status, count(*) FROM orders GROUP BY status",
          lambda rng: ()),
    Shape("b2_revenue_by_channel", "burst",
          "SELECT channel, sum(total_cents) FROM orders GROUP BY channel", lambda rng: ()),
    Shape("b3_orders_per_day", "burst",
          "SELECT created_day, count(*) FROM orders WHERE created_day >= %s GROUP BY created_day",
          lambda rng: (rng.randint(0, 200),)),
    Shape("b4_top_products", "burst",
          "SELECT product_id, sum(qty) FROM order_items GROUP BY product_id ORDER BY 2 DESC LIMIT 10",
          lambda rng: ()),
    Shape("b5_items_value", "burst",
          "SELECT count(*), sum(price_cents) FROM order_items WHERE qty >= %s",
          lambda rng: (rng.randint(1, 4),)),
    Shape("b6_top_customers", "burst",
          "SELECT customer_id, count(*) FROM orders GROUP BY customer_id ORDER BY 2 DESC LIMIT 10",
          lambda rng: ()),
]
# b4 aggregates the largest table, so it costs the most per call of the group on both engines,
# and it runs twice as often as the rest in a burst: it leads every occurrence by database time.
BURST_LEAD = "b4_top_products"
for index, shape in enumerate(BURST):
    BURST[index] = Shape(shape.name, shape.role, shape.sql, shape.params, rate=0.05,
                         burst_rate=12.0 if shape.name == BURST_LEAD else 6.0, default_ms=4.0)
BURST_ONLY = Shape("b7_status_channel_matrix", "burst_only",
                   "SELECT status, channel, avg(total_cents) FROM orders GROUP BY status, channel",
                   lambda rng: (), rate=0.0, burst_rate=3.0, default_ms=2.5)

NOISE = "c25_customer_exists"
CHEAP = [
    Shape(name, "cheap", sql, _id(table), rate=0.9, default_ms=0.05)
    for name, table, sql in (
        ("c01_customer_email", "customers", "SELECT email FROM customers WHERE id = %s"),
        ("c02_customer_region", "customers", "SELECT region FROM customers WHERE id = %s"),
        ("c03_customer_score", "customers", "SELECT segment, score FROM customers WHERE id = %s"),
        ("c04_customer_signup", "customers", "SELECT signup_day FROM customers WHERE id = %s"),
        ("c05_product_sku", "products", "SELECT sku FROM products WHERE id = %s"),
        ("c06_product_price", "products", "SELECT price_cents FROM products WHERE id = %s"),
        ("c07_product_stock", "products", "SELECT stock, category FROM products WHERE id = %s"),
        ("c08_order_status", "orders", "SELECT status FROM orders WHERE id = %s"),
        ("c09_order_total", "orders", "SELECT total_cents FROM orders WHERE id = %s"),
        ("c10_order_channel", "orders", "SELECT channel, created_day FROM orders WHERE id = %s"),
        ("c11_order_customer", "orders", "SELECT customer_id FROM orders WHERE id = %s"),
        ("c12_item_qty", "order_items", "SELECT qty FROM order_items WHERE id = %s"),
        ("c13_item_product", "order_items",
         "SELECT product_id, price_cents FROM order_items WHERE id = %s"),
        ("c14_item_order", "order_items", "SELECT order_id FROM order_items WHERE id = %s"),
        ("c15_payment_method", "payments", "SELECT method FROM payments WHERE id = %s"),
        ("c16_payment_state", "payments", "SELECT amount_cents, state FROM payments WHERE id = %s"),
        ("c17_payment_order", "payments", "SELECT order_id FROM payments WHERE id = %s"),
        ("c18_event_kind", "events", "SELECT kind FROM events WHERE id = %s"),
        ("c19_event_customer", "events", "SELECT customer_id, ts_day FROM events WHERE id = %s"),
        ("c20_event_payload", "events", "SELECT payload_len FROM events WHERE id = %s"),
        ("c21_session_device", "sessions", "SELECT device FROM sessions WHERE id = %s"),
        ("c22_session_duration", "sessions", "SELECT duration_s FROM sessions WHERE id = %s"),
        ("c23_session_customer", "sessions", "SELECT customer_id FROM sessions WHERE id = %s"),
        ("c24_customer_contact", "customers", "SELECT email, region FROM customers WHERE id = %s"),
        (NOISE, "customers", "SELECT id FROM customers WHERE id = %s"),
    )
]

# Inserted event ids start above the seed; the DELETE removes an earlier insert.
WRITES = [
    Shape("w1_set_order_status", "write", "UPDATE orders SET status = %s WHERE id = %s",
          lambda rng: (_pick(STATUSES)(rng), rng.randint(1, SIZES["orders"])), rate=0.5,
          default_ms=0.3),
    Shape("w2_restock_product", "write", "UPDATE products SET stock = stock + %s WHERE id = %s",
          lambda rng: (rng.randint(1, 3), rng.randint(1, SIZES["products"])), rate=0.5,
          default_ms=0.3),
    Shape("w3_bump_score", "write", "UPDATE customers SET score = score + %s WHERE id = %s",
          lambda rng: (1, rng.randint(1, SIZES["customers"])), rate=0.5, default_ms=0.3),
    Shape("w4_record_event", "write",
          "INSERT INTO events (id, customer_id, kind, ts_day, payload_len) VALUES (%s, %s, %s, %s, %s)",
          lambda rng: (), rate=0.5, default_ms=0.3),
    Shape("w5_forget_event", "write", "DELETE FROM events WHERE id = %s", lambda rng: (),
          rate=0.5, default_ms=0.3),
]

NEW_HEAVY = Shape("n1_category_revenue", "new_heavy",
                  "SELECT p.category, sum(oi.qty * oi.price_cents) FROM order_items oi "
                  "JOIN products p ON p.id = oi.product_id GROUP BY p.category ORDER BY 2 DESC",
                  lambda rng: (), rate=0.0, burst_rate=8.0, default_ms=8.0)

STOPPED = Shape("s1_record_payment", "stopped",
                "INSERT INTO payments (id, order_id, method, amount_cents, state) "
                "VALUES (%s, %s, %s, %s, %s)",
                lambda rng: (rng.randint(10_000, 99_999), rng.randint(1, SIZES["orders"]),
                             _pick(("card", "wallet"))(rng), rng.randint(500, 40000), "pending"),
                rate=1.0, default_ms=1.5)

LIVE = HEAVY + MEDIUM + BURST + [BURST_ONLY] + CHEAP + WRITES
ALL = LIVE + [NEW_HEAVY, STOPPED]
BY_NAME = {shape.name: shape for shape in ALL}

assert len(HEAVY) == 10 and len(MEDIUM) + len(BURST) + 1 == 20
assert len(CHEAP) == 25 and len(WRITES) == 5 and len(BY_NAME) == len(ALL)


def render(shape: Shape, rng: random.Random) -> str:
    """The statement text with literals inlined, as a history row's text."""
    values = [_literal(value) for value in shape.params(rng)]
    # The writes take their ids from the driver; any id renders the same statement shape.
    values += ["1"] * (shape.sql.count("%s") - len(values))
    return shape.sql % tuple(values)


def _literal(value: object) -> str:
    return f"'{value}'" if isinstance(value, str) else str(value)


_STRING = re.compile(r"'(?:[^']|'')*'")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\b")
_PLACEHOLDER = re.compile(r"\$\d+|%s|\?|:p\d+")


def canonical(text: str) -> str:
    """Text reduced so a catalogue statement, pg_stat_statements text and a MySQL digest meet."""
    value = text.lower().replace("`", "").replace('"', "")
    value = _STRING.sub("?", value)
    value = _PLACEHOLDER.sub("?", value)
    value = _NUMBER.sub("?", value)
    value = re.sub(r"values\s*\([^)]*\)", "values(?)", value)
    return re.sub(r"\s+", "", value)


CANONICAL = {canonical(shape.sql): shape.name for shape in ALL}
assert len(CANONICAL) == len(ALL), "catalogue statements must differ after normalization"


def shape_for(text: str) -> str | None:
    return CANONICAL.get(canonical(text))

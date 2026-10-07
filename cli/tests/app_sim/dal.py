"""The shop's data-access layer: named statements shaped like SQLAlchemy Core output.

Statements use table-qualified columns and pyformat binds (`%(slug_1)s`), as SQLAlchemy renders
them for psycopg2 and pymysql. Both drivers interpolate client side, and pg_stat_statements and
performance_schema normalize the literals back into one digest per statement.

Planted problems are marked with their plant id from expected.py.
"""

from __future__ import annotations

import queue
import threading
from collections import Counter
from contextlib import contextmanager
from typing import Any, Callable, Iterator

Q = {
    # Request middleware: touch the session row on every request.
    # P6: sessions.token has no index, so every touch scans sessions.
    "session_touch": ("UPDATE sessions SET last_seen_at=%(last_seen_at)s "
                      "WHERE sessions.token = %(token_1)s"),
    "event_insert": ("INSERT INTO events (customer_id, session_id, kind, path, payload, "
                     "created_at) VALUES (%(customer_id)s, %(session_id)s, %(kind)s, "
                     "%(path)s, %(payload)s, %(created_at)s)"),
    "category_get": ("SELECT categories.id, categories.parent_id, categories.name, categories.slug "
                     "FROM categories WHERE categories.id = %(pk_1)s"),
    "category_products": ("SELECT products.id, products.slug, products.name, products.brand, "
                          "products.price_cents FROM products WHERE products.category_id = "
                          "%(category_id_1)s AND products.status = %(status_1)s "
                          "ORDER BY products.popularity DESC LIMIT %(param_1)s"),
    "search_products": ("SELECT products.id, products.slug, products.name, products.price_cents "
                        "FROM products WHERE products.category_id = %(category_id_1)s AND "
                        "products.name LIKE %(name_1)s ORDER BY products.popularity DESC "
                        "LIMIT %(param_1)s"),
    # P1: products.slug has no index; the product page is the hottest read.
    "product_by_slug": ("SELECT products.id, products.category_id, products.sku, products.slug, "
                        "products.name, products.brand, products.price_cents, products.status, "
                        "products.description FROM products WHERE products.slug = %(slug_1)s"),
    "product_stock": ("SELECT sum(inventory.quantity) AS sum_1 FROM inventory "
                      "WHERE inventory.product_id = %(product_id_1)s"),
    "product_reviews": ("SELECT reviews.id, reviews.rating, reviews.body, reviews.created_at "
                        "FROM reviews WHERE reviews.product_id = %(product_id_1)s "
                        "ORDER BY reviews.created_at DESC LIMIT %(param_1)s"),
    "cart_open": ("SELECT carts.id FROM carts WHERE carts.customer_id = %(customer_id_1)s AND "
                  "carts.status = %(status_1)s ORDER BY carts.id DESC LIMIT %(param_1)s"),
    "cart_insert": ("INSERT INTO carts (customer_id, status, created_at, updated_at, expires_at) "
                    "VALUES (%(customer_id)s, %(status)s, %(created_at)s, %(updated_at)s, "
                    "%(expires_at)s)"),
    "cart_item_insert": ("INSERT INTO cart_items (cart_id, product_id, qty, added_at) "
                         "VALUES (%(cart_id)s, %(product_id)s, %(qty)s, %(added_at)s)"),
    "cart_touch": "UPDATE carts SET updated_at=%(updated_at)s WHERE carts.id = %(id_1)s",
    "cart_lines": ("SELECT cart_items.id, cart_items.product_id, cart_items.qty, products.name, "
                   "products.price_cents FROM cart_items JOIN products ON products.id = "
                   "cart_items.product_id WHERE cart_items.cart_id = %(cart_id_1)s"),
    "cart_convert": ("UPDATE carts SET status=%(status)s, updated_at=%(updated_at)s "
                     "WHERE carts.id = %(id_1)s"),
    "inventory_reserve": ("UPDATE inventory SET quantity=(inventory.quantity - %(quantity_1)s), "
                          "updated_at=%(updated_at)s WHERE inventory.product_id = %(product_id_1)s "
                          "AND inventory.warehouse_id = %(warehouse_id_1)s AND "
                          "inventory.quantity >= %(quantity_2)s"),
    "inventory_restock": ("UPDATE inventory SET quantity=(inventory.quantity + %(quantity_1)s), "
                          "updated_at=%(updated_at)s WHERE inventory.product_id = %(product_id_1)s "
                          "AND inventory.warehouse_id = %(warehouse_id_1)s"),
    "order_insert": ("INSERT INTO orders (customer_id, status, total_cents, shipping_region, "
                     "created_at, updated_at) VALUES (%(customer_id)s, %(status)s, "
                     "%(total_cents)s, %(shipping_region)s, %(created_at)s, %(updated_at)s)"),
    "order_item_insert": ("INSERT INTO order_items (order_id, product_id, qty, price_cents) "
                          "VALUES (%(order_id)s, %(product_id)s, %(qty)s, %(price_cents)s)"),
    "payment_insert": ("INSERT INTO payments (order_id, method, amount_cents, state, created_at) "
                       "VALUES (%(order_id)s, %(method)s, %(amount_cents)s, %(state)s, "
                       "%(created_at)s)"),
    # P2: two hot reads on orders.customer_id. Their ideal indexes are (customer_id) and
    # (customer_id, created_at); the second serves both.
    "orders_count_for_customer": ("SELECT count(*) AS count_1 FROM orders "
                                  "WHERE orders.customer_id = %(customer_id_1)s"),
    "orders_for_customer": ("SELECT orders.id, orders.status, orders.total_cents, "
                            "orders.created_at FROM orders "
                            "WHERE orders.customer_id = %(customer_id_1)s "
                            "ORDER BY orders.created_at DESC LIMIT %(param_1)s"),
    "order_get": ("SELECT orders.id, orders.customer_id, orders.status, orders.total_cents, "
                  "orders.created_at FROM orders WHERE orders.id = %(pk_1)s"),
    # P3 lives on order_items: either index serves this, one of them is redundant.
    "order_lines": ("SELECT order_items.product_id, order_items.qty, order_items.price_cents, "
                    "products.name FROM order_items JOIN products ON products.id = "
                    "order_items.product_id WHERE order_items.order_id = %(order_id_1)s"),
    # Returns check the line by both columns, which only the wider P3 index serves fully.
    "order_line_for_return": ("SELECT order_items.id, order_items.qty FROM order_items "
                              "WHERE order_items.order_id = %(order_id_1)s AND "
                              "order_items.product_id = %(product_id_1)s"),
    "order_payment": ("SELECT payments.id, payments.method, payments.state FROM payments "
                      "WHERE payments.order_id = %(order_id_1)s"),
    # P8: lower() on the column defeats customers_email_key. Emails are stored lower case, so
    # `customers.email = %(email_1)s` with a lowered bind returns the same rows.
    "customer_by_email": ("SELECT customers.id, customers.email, customers.name, customers.segment "
                          "FROM customers WHERE lower(customers.email) = lower(%(email_1)s)"),
    "session_insert": ("INSERT INTO sessions (customer_id, token, user_agent, created_at, "
                       "last_seen_at, expires_at) VALUES (%(customer_id)s, %(token)s, "
                       "%(user_agent)s, %(created_at)s, %(last_seen_at)s, %(expires_at)s)"),
    "sessions_for_customer": ("SELECT sessions.id, sessions.user_agent, sessions.last_seen_at "
                              "FROM sessions WHERE sessions.customer_id = %(customer_id_1)s "
                              "ORDER BY sessions.last_seen_at DESC LIMIT %(param_1)s"),
    "customer_login": ("UPDATE customers SET last_login_at=%(last_login_at)s "
                       "WHERE customers.id = %(id_1)s"),
    # Background jobs.
    "order_set_status": ("UPDATE orders SET status=%(status)s, updated_at=%(updated_at)s "
                         "WHERE orders.id = %(id_1)s"),
    "payment_settle": ("UPDATE payments SET state=%(state)s, settled_at=%(settled_at)s "
                       "WHERE payments.order_id = %(order_id_1)s"),
    # P7: carts.expires_at has no index, so both cleanup statements scan carts.
    "cart_items_purge": ("DELETE FROM cart_items WHERE cart_items.cart_id IN (SELECT carts.id "
                         "FROM carts WHERE carts.status = %(status_1)s AND "
                         "carts.expires_at < %(expires_at_1)s)"),
    "carts_purge": ("DELETE FROM carts WHERE carts.status = %(status_1)s AND "
                    "carts.expires_at < %(expires_at_1)s"),
    # P9: two admin reports over most of orders. The first groups ~58k customers in a temp table
    # bigger than the 1M MySQL tmp_table_size, so it goes to disk. The second sorts ~370k joined
    # rows for its DISTINCT aggregate and spills past PostgreSQL's default 4MB work_mem (on
    # MySQL it needs sort merge passes at the default sort_buffer_size).
    "report_top_customers": ("SELECT orders.customer_id, count(orders.id) AS order_count, "
                             "sum(orders.total_cents) AS spend FROM orders "
                             "WHERE orders.created_at >= %(created_at_1)s "
                             "GROUP BY orders.customer_id ORDER BY spend DESC LIMIT %(param_1)s"),
    "report_customer_breadth": ("SELECT orders.customer_id, count(DISTINCT order_items.product_id) "
                                "AS product_count, sum(order_items.qty * order_items.price_cents) "
                                "AS spend FROM orders JOIN order_items ON order_items.order_id = "
                                "orders.id WHERE orders.created_at >= %(created_at_1)s "
                                "GROUP BY orders.customer_id ORDER BY product_count DESC "
                                "LIMIT %(param_1)s"),
    # P10: the nightly settlement batch, one statement per day. Only it uses
    # payments_created_at_idx.
    "batch_settlement_day": ("SELECT payments.method, payments.state, count(payments.id) AS n, "
                             "sum(payments.amount_cents) AS amount FROM payments "
                             "WHERE payments.created_at >= %(created_at_1)s AND "
                             "payments.created_at < %(created_at_2)s "
                             "GROUP BY payments.method, payments.state"),
    # P5: a rare support-tool lookup on the write-heavy events table. events.session_id has no
    # index and shouldn't get one.
    "support_session_events": ("SELECT events.id, events.kind, events.path, events.created_at "
                               "FROM events WHERE events.session_id = %(session_id_1)s "
                               "ORDER BY events.id DESC LIMIT %(param_1)s"),
}


def verb(name: str) -> str:
    return Q[name].split(None, 1)[0].upper()


class Pool:
    """A fixed-size connection pool. A connection that raised is closed instead of returned."""

    def __init__(self, connect: Callable[[], Any], size: int) -> None:
        self._connect = connect
        self._idle: queue.LifoQueue[Any] = queue.LifoQueue()
        self._slots = threading.BoundedSemaphore(size)

    @contextmanager
    def connection(self) -> Iterator[Any]:
        self._slots.acquire()
        conn = None
        try:
            try:
                conn = self._idle.get_nowait()
            except queue.Empty:
                conn = self._connect()
            yield conn
            self._idle.put(conn)
        except BaseException:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            raise
        finally:
            self._slots.release()

    def close(self) -> None:
        while True:
            try:
                self._idle.get_nowait().close()
            except queue.Empty:
                return
            except Exception:
                continue


class Shop:
    """One unit of work on a pooled connection. Autocommit unless inside transaction()."""

    def __init__(self, conn: Any, engine: str, counts: Counter[str]) -> None:
        self.conn = conn
        self.engine = engine
        self.counts = counts
        self._in_tx = False

    def _run(self, name: str, params: dict[str, Any], fetch: bool = False) -> Any:
        with self.conn.cursor() as cursor:
            cursor.execute(Q[name], params)
            self.counts[name] += 1
            if fetch:
                return cursor.fetchall()
            return cursor.rowcount

    def all(self, name: str, **params: Any) -> list[tuple[Any, ...]]:
        return list(self._run(name, params, fetch=True))

    def one(self, name: str, **params: Any) -> tuple[Any, ...] | None:
        rows = self._run(name, params, fetch=True)
        return rows[0] if rows else None

    def execute(self, name: str, **params: Any) -> int:
        result = self._run(name, params)
        if not self._in_tx:
            self.conn.commit()
        return result

    def insert(self, name: str, **params: Any) -> int:
        with self.conn.cursor() as cursor:
            if self.engine == "postgresql":
                cursor.execute(Q[name] + " RETURNING id", params)
                new_id = cursor.fetchone()[0]
            else:
                cursor.execute(Q[name], params)
                new_id = cursor.lastrowid
        self.counts[name] += 1
        if not self._in_tx:
            self.conn.commit()
        return int(new_id)

    def insert_many(self, name: str, rows: list[dict[str, Any]]) -> None:
        with self.conn.cursor() as cursor:
            for row in rows:
                cursor.execute(Q[name], row)
        self.counts[name] += len(rows)

    @contextmanager
    def transaction(self) -> Iterator["Shop"]:
        self._in_tx = True
        try:
            yield self
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        finally:
            self._in_tx = False

    def finish(self) -> None:
        """Ends any implicit read transaction so the pooled connection holds no snapshot."""
        self.conn.commit()

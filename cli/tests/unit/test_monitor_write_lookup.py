import pytest

from shared.monitor.write_lookup import column_roles, lookup_select, write_shape


@pytest.mark.parametrize("sql,engine,expected", [
    ("UPDATE sessions SET last_seen = $1, hits = hits + 1 WHERE token = $2", "postgresql",
     ("SELECT 1 FROM sessions WHERE token = $2", "sessions", 0)),
    ("DELETE FROM carts WHERE updated_at < $1 AND status = $2", "postgresql",
     ("SELECT 1 FROM carts WHERE updated_at < $1 AND status = $2", "carts", 0)),
    ("UPDATE `sessions` SET `last_seen` = ? WHERE `token` = ?", "mysql",
     ("SELECT 1 FROM `sessions` WHERE `token` = ?", "sessions", 1)),
    ("UPDATE orders o SET status = $1 FROM customers c WHERE c.id = o.customer_id AND c.region = $2",
     "postgresql",
     ("SELECT 1 FROM orders AS o, customers AS c WHERE c.id = o.customer_id AND c.region = $2",
      "orders", 0)),
    ("UPDATE orders o JOIN customers c ON c.id = o.customer_id SET o.status = ? WHERE c.region = ?",
     "mysql",
     ("SELECT 1 FROM orders AS o JOIN customers AS c ON c.id = o.customer_id WHERE c.region = ?",
      "orders", 1)),
    ("DELETE FROM a USING b WHERE a.id = b.a_id AND b.x = $1", "postgresql",
     ("SELECT 1 FROM a, b WHERE a.id = b.a_id AND b.x = $1", "a", 0)),
    ("INSERT INTO archive SELECT * FROM orders WHERE created_day < $1", "postgresql",
     ("SELECT * FROM orders WHERE created_day < $1", "archive", 0)),
])
def test_a_write_row_search_becomes_a_select(sql, engine, expected):
    found = lookup_select(sql, engine)
    assert (found["sql"], found["table"], found["skip_params"]) == expected


@pytest.mark.parametrize("sql", ["INSERT INTO events (kind) VALUES ($1)", "UPDATE t SET x = 1",
                                 "SELECT 1", "not sql at all ((("])
def test_writes_without_a_row_search_have_no_lookup(sql):
    assert lookup_select(sql, "postgresql") is None


def test_write_shape_names_the_table_and_updated_columns():
    assert write_shape("UPDATE public.sessions SET last_seen = $1, hits = hits + 1 WHERE id = $2",
                       "postgresql") == {"verb": "update", "table": "sessions",
                                         "set_columns": ["hits", "last_seen"]}
    assert write_shape("INSERT INTO `events` (`kind`) VALUES (?)", "mysql")["table"] == "events"
    assert write_shape("SELECT 1", "postgresql") is None


def test_column_roles_put_equality_before_sort_before_range():
    from shared.monitor.write_lookup import column_roles, order_index_columns

    roles = column_roles("SELECT id FROM orders WHERE customer_id = $1 AND total > $2 "
                         "ORDER BY created_at DESC", "postgresql")
    assert roles == {"customer_id": "equality", "total": "range", "created_at": "sort"}
    assert order_index_columns(["total", "created_at", "customer_id", "status"], roles) == [
        "customer_id", "created_at", "total", "status"]
    joined = column_roles("SELECT o.id FROM orders o JOIN customers c ON c.id = o.customer_id "
                          "WHERE c.region IN ($1,$2)", "postgresql")
    assert joined == {"region": "equality"}
    assert column_roles("SELECT * FROM t WHERE a = ? AND a > ?", "mysql") == {"a": "equality"}


def test_ordered_and_limited_writes_and_ctes_keep_their_shape():
    assert lookup_select("DELETE FROM jobs WHERE done = 1 ORDER BY id LIMIT 100", "mysql")["sql"] == (
        "SELECT 1 FROM jobs WHERE done = 1 ORDER BY id LIMIT 100")
    assert lookup_select("WITH stale AS (SELECT id FROM carts WHERE expires_at < $1) "
                         "DELETE FROM cart_items WHERE cart_id IN (SELECT id FROM stale)",
                         "postgresql")["sql"].startswith("WITH stale AS (")


def test_roles_read_through_casts_on_parameters():
    sql = "SELECT id FROM invoices WHERE date(invoices.created_at) = $1::date ORDER BY id"
    assert column_roles(sql, "postgresql") == {"created_at": "equality", "id": "sort"}

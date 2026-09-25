"""The ci-shop E2E workload: catalogue identity and the cuttable proxy."""

import random
import socket
import threading

import pytest

from shared.query_registry.query_registry import hash_sql
from tests.web_e2e.ci_shop import catalogue
from tests.web_e2e.ci_shop.proxy import CuttableProxy


def test_catalogue_has_the_planned_mix():
    roles = [shape.role for shape in catalogue.LIVE]
    assert roles.count("heavy") == 10 and roles.count("cheap") == 25
    assert roles.count("write") == 5
    assert roles.count("medium") + roles.count("burst") + roles.count("burst_only") == 20


@pytest.mark.parametrize("text, name", [
    ("SELECT id, method, amount_cents FROM payments WHERE order_id = $1", "h04_payments_by_order"),
    ("SELECT `id` , `method` , `amount_cents` FROM `payments` WHERE `order_id` = ?",
     "h04_payments_by_order"),
    ("INSERT INTO `events` ( `id` , `customer_id` , `kind` , `ts_day` , `payload_len` ) "
     "VALUES (...)", "w4_record_event"),
    ("SELECT product_id, sum(qty) FROM order_items GROUP BY product_id ORDER BY $1 DESC LIMIT $2",
     "b4_top_products"),
])
def test_engine_texts_map_to_their_shape(text, name):
    assert catalogue.shape_for(text) == name


def test_rendered_shapes_hash_like_the_collector():
    # The collector keys a statement by the registry hash of its text; the history relies on
    # rendered literals and engine placeholders meeting at the same key.
    shape = catalogue.BY_NAME["h01_orders_by_status_channel"]
    rendered = catalogue.render(shape, random.Random(1))
    normalized = ("SELECT id, customer_id, total_cents FROM orders WHERE status = $1 AND "
                  "channel = $2 ORDER BY created_day DESC LIMIT $3")
    assert hash_sql(rendered) == hash_sql(normalized)


def _echo_server() -> tuple[socket.socket, int]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()

    def serve():
        while True:
            try:
                client, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=lambda: [client.sendall(data) for data in
                                             iter(lambda: client.recv(1024), b"")],
                             daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()
    return server, server.getsockname()[1]


def test_proxy_cuts_and_restores_the_route():
    server, port = _echo_server()
    proxy = CuttableProxy("127.0.0.1", port)
    try:
        proxy.start()
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as client:
            client.sendall(b"ping")
            assert client.recv(4) == b"ping"
            proxy.cut()
            client.settimeout(5)
            assert client.recv(4) == b""
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", proxy.port), timeout=2)
        proxy.start()
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as client:
            client.sendall(b"back")
            assert client.recv(4) == b"back"
    finally:
        proxy.close()
        server.close()

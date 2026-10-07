"""The workload driver: weighted user journeys plus background jobs, on one or both engines.

Each worker thread draws requests from a Poisson process at its share of the target rate, picks a
journey by weight and runs it as a web request would: touch the session, do the page's reads and
writes, log an event. Background jobs run on their own thread on fixed intervals. Every thread
has its own RNG derived from the run seed, so a run's choices repeat exactly.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from tests.app_sim import schema
from tests.app_sim.dal import Pool, Shop, verb

logger = logging.getLogger(__name__)

# Share of requests per journey. Most requests are reads; checkout is the big write transaction.
JOURNEYS = {
    "browse_category": 27,
    "view_product": 30,
    "search": 9,
    "add_to_cart": 10,
    "view_cart": 5,
    "checkout": 4,
    "my_orders": 7,
    "order_detail": 4,
    "login": 4,
    "return_item": 1,
}

# (job, interval seconds). The simulator compresses time: "nightly" runs every few minutes.
JOBS = (
    ("fulfil_orders", 5.0),
    ("restock", 20.0),
    ("purge_carts", 20.0),
    ("admin_reports", 30.0),
    ("support_session_events", 75.0),
    ("nightly_settlement", 120.0),
)

CART_TTL = timedelta(minutes=2)
SEARCH_TERMS = schema.ADJECTIVES + schema.NOUNS


def pick_journey(rng: random.Random) -> str:
    names = list(JOURNEYS)
    return rng.choices(names, weights=[JOURNEYS[n] for n in names], k=1)[0]


def skewed(rng: random.Random, n: int, power: float = 2.0) -> int:
    return 1 + int(n * rng.random() ** power)


@dataclass
class Visitor:
    session_id: int
    customer_id: int
    token: str


@dataclass
class DriverStats:
    statements: Counter[str] = field(default_factory=Counter)
    journeys: Counter[str] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)
    last_error: dict[str, str] = field(default_factory=dict)

    def by_verb(self) -> Counter[str]:
        out: Counter[str] = Counter()
        for name, count in self.statements.items():
            out[verb(name)] += count
        return out


class Driver:
    def __init__(self, engine: str, connect: Callable[[], Any], *, rps: float = 12.0,
                 threads: int = 6, seed: int = 20261007, scale: float = 1.0,
                 jobs: bool = True) -> None:
        self.engine = engine
        self.pool = Pool(connect, threads + 1)
        self.rps = rps
        self.threads = threads
        self.seed = seed
        self.n = schema.sizes(scale)
        self.jobs_enabled = jobs
        self.stats = DriverStats()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._to_ship: deque[int] = deque(maxlen=5000)
        self._new_sessions: list[Visitor] = []
        self._threads: list[threading.Thread] = []
        self.started_at = 0.0

    # Lifecycle

    def start(self) -> None:
        self.started_at = time.time()
        for index in range(self.threads):
            rng = random.Random(f"{self.seed}:{self.engine}:worker:{index}")
            self._threads.append(threading.Thread(
                target=self._worker, args=(rng,), name=f"appsim-{self.engine}-{index}",
                daemon=True))
        if self.jobs_enabled:
            rng = random.Random(f"{self.seed}:{self.engine}:jobs")
            self._threads.append(threading.Thread(
                target=self._jobs, args=(rng,), name=f"appsim-{self.engine}-jobs", daemon=True))
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=30)
        self.pool.close()

    def summary(self) -> dict[str, Any]:
        elapsed = max(1.0, time.time() - self.started_at)
        with self._lock:
            total = sum(self.stats.statements.values())
            return {"engine": self.engine, "elapsed_s": round(elapsed, 1),
                    "statements": total, "per_s": round(total / elapsed, 1),
                    "by_verb": dict(self.stats.by_verb()),
                    "journeys": dict(self.stats.journeys), "errors": dict(self.stats.errors),
                    "last_error": dict(self.stats.last_error)}

    # Workers

    def _worker(self, rng: random.Random) -> None:
        mean_gap = self.threads / self.rps
        next_at = time.monotonic() + rng.random() * mean_gap
        while not self._stop.is_set():
            wait = next_at - time.monotonic()
            if wait > 0:
                self._stop.wait(wait)
                continue
            # A worker that fell far behind resumes the schedule instead of bursting to catch up.
            next_at = max(next_at, time.monotonic() - 2.0) + rng.expovariate(1.0 / mean_gap)
            journey = pick_journey(rng)
            self._guard(journey, lambda shop: getattr(self, f"j_{journey}")(shop, rng))

    def _jobs(self, rng: random.Random) -> None:
        now = time.monotonic()
        due = {name: now + interval * (0.3 + 0.7 * rng.random()) for name, interval in JOBS}
        intervals = dict(JOBS)
        while not self._stop.is_set():
            name = min(due, key=due.get)
            wait = due[name] - time.monotonic()
            if wait > 0:
                self._stop.wait(min(wait, 1.0))
                continue
            due[name] = time.monotonic() + intervals[name]
            self._guard(f"job:{name}", lambda shop: getattr(self, f"b_{name}")(shop, rng))

    def _guard(self, name: str, body: Callable[[Shop], None]) -> None:
        local: Counter[str] = Counter()
        try:
            with self.pool.connection() as conn:
                shop = Shop(conn, self.engine, local)
                try:
                    body(shop)
                finally:
                    shop.finish()
            ok = True
        except Exception as exc:
            ok = False
            with self._lock:
                self.stats.errors[name] += 1
                self.stats.last_error[name] = str(exc)[:300]
            logger.debug("%s %s failed: %s", self.engine, name, exc)
        with self._lock:
            self.stats.statements.update(local)
            if ok:
                self.stats.journeys[name] += 1

    # Request helpers

    def _visitor(self, rng: random.Random) -> Visitor:
        with self._lock:
            if self._new_sessions and rng.random() < 0.3:
                return rng.choice(self._new_sessions)
        session_id = skewed(rng, self.n["sessions"], 1.5)
        return Visitor(session_id, schema.session_customer(session_id, self.n["customers"]),
                       schema.session_token(session_id))

    def _begin(self, shop: Shop, rng: random.Random) -> Visitor:
        visitor = self._visitor(rng)
        shop.execute("session_touch", last_seen_at=datetime.now(), token_1=visitor.token)
        return visitor

    def _event(self, shop: Shop, visitor: Visitor, kind: str, path: str) -> None:
        shop.insert("event_insert", customer_id=visitor.customer_id,
                    session_id=visitor.session_id, kind=kind, path=path,
                    payload='{"ua":"web","v":3}', created_at=datetime.now())

    def _product_id(self, rng: random.Random) -> int:
        return skewed(rng, self.n["products"], 2.5)

    # Journeys

    def j_browse_category(self, shop: Shop, rng: random.Random) -> None:
        visitor = self._begin(shop, rng)
        category = skewed(rng, self.n["categories"], 1.5)
        shop.one("category_get", pk_1=category)
        shop.all("category_products", category_id_1=category, status_1="active", param_1=24)
        self._event(shop, visitor, "page_view", f"/c/category-{category}")

    def j_view_product(self, shop: Shop, rng: random.Random) -> None:
        visitor = self._begin(shop, rng)
        product_id = self._product_id(rng)
        row = shop.one("product_by_slug", slug_1=schema.product_slug(product_id))
        if row:
            shop.one("product_stock", product_id_1=row[0])
            shop.all("product_reviews", product_id_1=row[0], param_1=5)
        self._event(shop, visitor, "product_view", f"/p/{schema.product_slug(product_id)}")

    def j_search(self, shop: Shop, rng: random.Random) -> None:
        visitor = self._begin(shop, rng)
        term = rng.choice(SEARCH_TERMS)
        category = skewed(rng, self.n["categories"], 1.5)
        shop.all("search_products", category_id_1=category, name_1=f"%{term.title()}%",
                 param_1=20)
        self._event(shop, visitor, "search", f"/search?q={term}")

    def _open_cart(self, shop: Shop, customer_id: int, create: bool) -> int | None:
        row = shop.one("cart_open", customer_id_1=customer_id, status_1="open", param_1=1)
        if row:
            return int(row[0])
        if not create:
            return None
        now = datetime.now()
        return shop.insert("cart_insert", customer_id=customer_id, status="open", created_at=now,
                           updated_at=now, expires_at=now + CART_TTL)

    def j_add_to_cart(self, shop: Shop, rng: random.Random) -> None:
        visitor = self._begin(shop, rng)
        product_id = self._product_id(rng)
        with shop.transaction():
            cart_id = self._open_cart(shop, visitor.customer_id, create=True)
            shop.insert("cart_item_insert", cart_id=cart_id, product_id=product_id,
                        qty=1 + rng.randrange(2), added_at=datetime.now())
            shop.execute("cart_touch", updated_at=datetime.now(), id_1=cart_id)
        self._event(shop, visitor, "add_to_cart", f"/p/{schema.product_slug(product_id)}")

    def j_view_cart(self, shop: Shop, rng: random.Random) -> None:
        visitor = self._begin(shop, rng)
        cart_id = self._open_cart(shop, visitor.customer_id, create=False)
        if cart_id:
            shop.all("cart_lines", cart_id_1=cart_id)

    def j_checkout(self, shop: Shop, rng: random.Random) -> None:
        visitor = self._begin(shop, rng)
        cart_id = self._open_cart(shop, visitor.customer_id, create=False)
        if cart_id is None:
            # Buy-now: a one-line cart created on the spot.
            with shop.transaction():
                cart_id = self._open_cart(shop, visitor.customer_id, create=True)
                shop.insert("cart_item_insert", cart_id=cart_id,
                            product_id=self._product_id(rng), qty=1, added_at=datetime.now())
        lines = shop.all("cart_lines", cart_id_1=cart_id)
        if not lines:
            return
        now = datetime.now()
        with shop.transaction():
            kept = []
            # Lock inventory rows in product order so concurrent checkouts can't deadlock.
            for _line_id, product_id, qty, _name, price in sorted(lines, key=lambda r: r[1]):
                warehouse = 1 + product_id % schema.WAREHOUSES
                reserved = shop.execute("inventory_reserve", quantity_1=qty, updated_at=now,
                                        product_id_1=product_id, warehouse_id_1=warehouse,
                                        quantity_2=qty)
                if reserved:
                    kept.append((product_id, qty, price))
            total = sum(qty * price for _p, qty, price in kept) or 1
            order_id = shop.insert("order_insert", customer_id=visitor.customer_id, status="paid",
                                   total_cents=total, shipping_region=rng.choice(schema.REGIONS),
                                   created_at=now, updated_at=now)
            shop.insert_many("order_item_insert", [
                {"order_id": order_id, "product_id": p, "qty": q, "price_cents": c}
                for p, q, c in kept])
            shop.insert("payment_insert", order_id=order_id, method=rng.choice(schema.METHODS),
                        amount_cents=total, state="pending", created_at=now)
            shop.execute("cart_convert", status="converted", updated_at=now, id_1=cart_id)
        with self._lock:
            self._to_ship.append(order_id)
        self._event(shop, visitor, "checkout", "/checkout")

    def j_my_orders(self, shop: Shop, rng: random.Random) -> None:
        visitor = self._begin(shop, rng)
        # Order history is mostly visited by repeat buyers, who are the skewed low ids.
        customer_id = (skewed(rng, self.n["customers"]) if rng.random() < 0.7
                       else visitor.customer_id)
        shop.one("orders_count_for_customer", customer_id_1=customer_id)
        shop.all("orders_for_customer", customer_id_1=customer_id, param_1=20)
        self._event(shop, visitor, "page_view", "/account/orders")

    def j_order_detail(self, shop: Shop, rng: random.Random) -> None:
        self._begin(shop, rng)
        order_id = rng.randrange(1, self.n["orders"])
        shop.one("order_get", pk_1=order_id)
        shop.all("order_lines", order_id_1=order_id)
        shop.one("order_payment", order_id_1=order_id)

    def j_return_item(self, shop: Shop, rng: random.Random) -> None:
        visitor = self._begin(shop, rng)
        order_id = rng.randrange(1, self.n["orders"])
        lines = shop.all("order_lines", order_id_1=order_id)
        if not lines:
            return
        product_id = rng.choice(lines)[0]
        with shop.transaction():
            if shop.one("order_line_for_return", order_id_1=order_id, product_id_1=product_id):
                shop.execute("order_set_status", status="returned", updated_at=datetime.now(),
                             id_1=order_id)
        self._event(shop, visitor, "page_view", f"/account/orders/{order_id}/return")

    def j_login(self, shop: Shop, rng: random.Random) -> None:
        customer_id = skewed(rng, self.n["customers"], 1.5)
        typed = schema.customer_email(customer_id)
        if rng.random() < 0.4:
            typed = typed.capitalize()
        row = shop.one("customer_by_email", email_1=typed)
        if not row:
            return
        now = datetime.now()
        token = "%032x" % rng.getrandbits(128)
        session_id = shop.insert("session_insert", customer_id=customer_id, token=token,
                                 user_agent=rng.choice(schema.AGENTS), created_at=now,
                                 last_seen_at=now, expires_at=now + timedelta(days=30))
        shop.execute("customer_login", last_login_at=now, id_1=customer_id)
        shop.all("sessions_for_customer", customer_id_1=customer_id, param_1=10)
        visitor = Visitor(session_id, customer_id, token)
        with self._lock:
            self._new_sessions.append(visitor)
            if len(self._new_sessions) > 2000:
                del self._new_sessions[:1000]
        self._event(shop, visitor, "login", "/login")

    # Background jobs

    def b_fulfil_orders(self, shop: Shop, rng: random.Random) -> None:
        with self._lock:
            batch = [self._to_ship.popleft() for _ in range(min(10, len(self._to_ship)))]
        now = datetime.now()
        for order_id in batch:
            with shop.transaction():
                shop.execute("order_set_status", status="shipped", updated_at=now, id_1=order_id)
                shop.execute("payment_settle", state="settled", settled_at=now,
                             order_id_1=order_id)

    def b_restock(self, shop: Shop, rng: random.Random) -> None:
        now = datetime.now()
        for _ in range(5):
            shop.execute("inventory_restock", quantity_1=50, updated_at=now,
                         product_id_1=self._product_id(rng),
                         warehouse_id_1=1 + rng.randrange(schema.WAREHOUSES))

    def b_purge_carts(self, shop: Shop, rng: random.Random) -> None:
        cutoff = datetime.now()
        with shop.transaction():
            shop.execute("cart_items_purge", status_1="open", expires_at_1=cutoff)
            shop.execute("carts_purge", status_1="open", expires_at_1=cutoff)

    def b_admin_reports(self, shop: Shop, rng: random.Random) -> None:
        since = schema.ANCHOR - timedelta(days=300 + rng.randrange(30))
        shop.all("report_top_customers", created_at_1=since, param_1=50)
        shop.finish()
        shop.all("report_customer_breadth", created_at_1=since, param_1=50)

    def b_support_session_events(self, shop: Shop, rng: random.Random) -> None:
        shop.all("support_session_events", session_id_1=self._visitor(rng).session_id, param_1=50)

    def b_nightly_settlement(self, shop: Shop, rng: random.Random) -> None:
        start = schema.ANCHOR - timedelta(days=1 + rng.randrange(schema.SEED_DAYS - 8))
        for offset in range(7):
            day = start + timedelta(days=offset)
            shop.all("batch_settlement_day", created_at_1=day,
                     created_at_2=day + timedelta(days=1))
            shop.finish()


def run(drivers: list[Driver], duration: float, report_every: float = 15.0,
        log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    for driver in drivers:
        driver.start()
    deadline = time.monotonic() + duration
    next_report = time.monotonic() + report_every
    try:
        while time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
            if time.monotonic() >= next_report:
                next_report += report_every
                for driver in drivers:
                    s = driver.summary()
                    log(f"[{s['engine']}] {s['elapsed_s']}s {s['statements']} stmts "
                        f"({s['per_s']}/s) {s['by_verb']} errors={sum(s['errors'].values())}")
    except KeyboardInterrupt:
        log("Stopping.")
    finally:
        for driver in drivers:
            driver.stop()
    return [driver.summary() for driver in drivers]

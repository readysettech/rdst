"""Orders-domain QueryPilot demo workload.

Each query keeps a stable ``id`` and the display fields consumed by the web demo:
``title``, ``what``, ``why``, ``category``, and ``sql``. ``role`` remains for the
existing service/catalog path. ``weight`` is the phase-A relative frequency;
late-arriving queries start at weight 0 and carry ``activation_weight`` for the
load driver to enable mid-run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class Query:
    id: str
    title: str
    role: str
    category: str
    group: str
    phase: str
    sql: str
    what: str
    why: str
    weight: int = 1
    activation_weight: int | None = None
    denylist: bool = False

    def as_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.id
        return d


class WeightedSQLs(list[str]):
    """A list[str] with query metadata attached for newer load-driver paths."""

    def __init__(self, queries: Iterable[Query], *, weighted: bool = True,
                 include_inactive: bool = False) -> None:
        queries = list(queries)
        self.query_specs = []
        sqls: list[str] = []
        for q in queries:
            spec = q.as_dict()
            if not weighted and (q.weight > 0 or include_inactive):
                spec["weight"] = 1
            self.query_specs.append(spec)
            if q.weight <= 0 and not include_inactive:
                continue
            repetitions = q.weight if weighted else 1
            if repetitions <= 0:
                continue
            sqls.extend([q.sql] * repetitions)
        super().__init__(sqls)


WORKLOAD: list[Query] = [
    # Cheap high-frequency queries: small, realistic reads over a bounded slice
    # of the fact tables. Each warms to a few milliseconds, so the direct and
    # ReadySet baselines stay neck and neck until caching speeds this tier up.
    # These win count_star by sheer frequency.
    Query(
        id="C01",
        title="Order value from line items",
        role="cheap",
        category="cheap",
        group="cheap_point_lookup",
        phase="base",
        weight=150,
        sql=(
            "SELECT oi.order_id, sum(oi.qty * oi.unit_cents) AS order_cents "
            "FROM order_items oi WHERE oi.id BETWEEN 1000000 AND 1024000 "
            "GROUP BY oi.order_id ORDER BY order_cents DESC LIMIT 20"
        ),
        what="Roll up line-item totals for a recent order slice.",
        why="A very frequent cart/receipt widget; small and fast, but caching removes the repeated group work.",
    ),
    Query(
        id="C02",
        title="Units sold by product",
        role="cheap",
        category="cheap",
        group="cheap_point_lookup",
        phase="base",
        weight=140,
        sql=(
            "SELECT oi.product_id, sum(oi.qty) AS units "
            "FROM order_items oi WHERE oi.id BETWEEN 2000000 AND 2034000 "
            "GROUP BY oi.product_id ORDER BY units DESC LIMIT 20"
        ),
        what="Top products by units sold in a recent line-item slice.",
        why="A hot merchandising tile: cheap on its own, but its frequency makes it a count-based cache candidate.",
    ),
    Query(
        id="C03",
        title="Category revenue (recent slice)",
        role="cheap",
        category="cheap",
        group="cheap_point_lookup",
        phase="base",
        weight=130,
        sql=(
            "SELECT p.category, sum(oi.qty * oi.unit_cents) AS revenue_cents "
            "FROM order_items oi JOIN products p ON p.id = oi.product_id "
            "WHERE oi.id BETWEEN 3000000 AND 3024000 "
            "GROUP BY p.category ORDER BY revenue_cents DESC"
        ),
        what="Revenue by category over a recent line-item slice.",
        why="Frequent dashboard refresh; a small join and group that the cache can serve instantly once picked.",
    ),
    Query(
        id="C04",
        title="Top products by revenue (slice)",
        role="cheap",
        category="cheap",
        group="cheap_point_lookup",
        phase="base",
        weight=125,
        sql=(
            "SELECT p.name, sum(oi.qty * oi.unit_cents) AS revenue_cents "
            "FROM order_items oi JOIN products p ON p.id = oi.product_id "
            "WHERE oi.id BETWEEN 500000 AND 520000 "
            "GROUP BY p.name ORDER BY revenue_cents DESC LIMIT 15"
        ),
        what="Best-selling products by revenue in a bounded slice.",
        why="A product-page sidebar calls it constantly; small enough to stay fast, frequent enough to cache.",
    ),
    Query(
        id="C05",
        title="Order line counts",
        role="cheap",
        category="cheap",
        group="cheap_point_lookup",
        phase="base",
        weight=115,
        sql=(
            "SELECT oi.order_id, count(*) AS lines, avg(oi.unit_cents)::int AS avg_cents "
            "FROM order_items oi WHERE oi.id BETWEEN 1500000 AND 1520000 "
            "GROUP BY oi.order_id ORDER BY lines DESC LIMIT 25"
        ),
        what="Line count and average unit price per order in a slice.",
        why="A fulfillment view that runs often; a light group the cache can keep warm.",
    ),
    Query(
        id="C06",
        title="Units by category (recent slice)",
        role="cheap",
        category="cheap",
        group="cheap_point_lookup",
        phase="base",
        weight=105,
        sql=(
            "SELECT p.category, sum(oi.qty) AS units, count(*) AS lines "
            "FROM order_items oi JOIN products p ON p.id = oi.product_id "
            "WHERE oi.id BETWEEN 2500000 AND 2524000 "
            "GROUP BY p.category ORDER BY units DESC"
        ),
        what="Units and line counts by category for a recent slice.",
        why="A stock dashboard polls it frequently; a small join that becomes free once cached.",
    ),
    Query(
        id="C07",
        title="Orders by hour (recent slice)",
        role="cheap",
        category="cheap",
        group="cheap_point_lookup",
        phase="base",
        weight=100,
        sql=(
            "SELECT extract(hour FROM placed_at)::int AS hr, count(*) AS orders "
            "FROM orders WHERE id BETWEEN 300000 AND 314000 "
            "GROUP BY hr ORDER BY hr"
        ),
        what="Order volume by hour of day for a recent slice.",
        why="A live ops tile that refreshes constantly; cheap to run but worth caching at this frequency.",
    ),
    Query(
        id="C08",
        title="Orders by weekday (slice)",
        role="cheap",
        category="cheap",
        group="cheap_point_lookup",
        phase="base",
        weight=95,
        sql=(
            "SELECT extract(dow FROM placed_at)::int AS dow, count(*) AS orders, "
            "sum(total_cents) AS revenue_cents "
            "FROM orders WHERE id BETWEEN 900000 AND 908000 "
            "GROUP BY dow ORDER BY dow"
        ),
        what="Order counts and revenue by weekday for a slice.",
        why="A recurring trend tile; small but frequent enough to be a count-based cache pick.",
    ),
    Query(
        id="C09",
        title="Line-item slice summary",
        role="cheap",
        category="cheap",
        group="cheap_point_lookup",
        phase="base",
        weight=90,
        sql=(
            "SELECT count(DISTINCT oi.product_id) AS products, count(*) AS lines, "
            "sum(oi.qty) AS units "
            "FROM order_items oi WHERE oi.id BETWEEN 3500000 AND 3524000"
        ),
        what="Distinct products, line count, and units for a slice.",
        why="A summary badge polled by the fulfillment console; light work that caches cleanly.",
    ),
    Query(
        id="C10",
        title="Order value by status (slice)",
        role="cheap",
        category="cheap",
        group="cheap_point_lookup",
        phase="base",
        weight=85,
        sql=(
            "SELECT status, avg(total_cents)::int AS avg_cents, max(total_cents) AS max_cents "
            "FROM orders WHERE id BETWEEN 600000 AND 610000 "
            "GROUP BY status ORDER BY avg_cents DESC"
        ),
        what="Average and peak order value by status for a slice.",
        why="A support summary widget that runs often; a small group ideal for a frequency-based cache.",
    ),

    # Expensive low-frequency aggregates. These should win sum_time.
    Query(
        id="H01",
        title="Revenue by category",
        role="heavy",
        category="heavy",
        group="expensive_aggregate",
        phase="base",
        weight=34,
        sql=(
            "SELECT p.category, sum(oi.qty * oi.unit_cents) AS revenue_cents, "
            "count(*) AS lines "
            "FROM order_items oi "
            "JOIN products p ON p.id = oi.product_id "
            "GROUP BY p.category ORDER BY revenue_cents DESC"
        ),
        what="Total item revenue and line count per product category.",
        why="A merchandising dashboard widget scans and joins millions of rows; cached results make the win obvious.",
    ),
    Query(
        id="H02",
        title="Top products by revenue",
        role="heavy",
        category="heavy",
        group="expensive_aggregate",
        phase="base",
        weight=34,
        sql=(
            "SELECT p.id, p.name, sum(oi.qty * oi.unit_cents) AS revenue_cents, "
            "count(*) AS lines "
            "FROM order_items oi "
            "JOIN products p ON p.id = oi.product_id "
            "GROUP BY p.id, p.name ORDER BY revenue_cents DESC LIMIT 25"
        ),
        what="The best-earning products across the whole catalog.",
        why="This groups the full line-item table and should dominate total time despite low execution count.",
    ),
    Query(
        id="H03",
        title="Daily item revenue",
        role="heavy",
        category="heavy",
        group="expensive_aggregate",
        phase="base",
        weight=60,
        sql=(
            "SELECT date_trunc('day', o.placed_at) AS day, "
            "count(*) AS orders, sum(o.total_cents) AS revenue_cents "
            "FROM orders o "
            "GROUP BY 1 ORDER BY 1 DESC LIMIT 45"
        ),
        what="Daily revenue trend for the executive dashboard.",
        why="The trend chart has low frequency but repeatedly performs a large join and group.",
    ),
    Query(
        id="H04",
        title="Revenue by customer segment",
        role="heavy",
        category="heavy",
        group="expensive_aggregate",
        phase="base",
        weight=34,
        sql=(
            "SELECT c.segment, sum(o.total_cents) AS revenue_cents, count(*) AS orders "
            "FROM orders o "
            "JOIN customers c ON c.id = o.customer_id "
            "GROUP BY c.segment ORDER BY revenue_cents DESC"
        ),
        what="Order revenue split across consumer, SMB, and enterprise customers.",
        why="A full order/customer rollup is stable enough to cache and expensive enough to matter.",
    ),
    Query(
        id="H05",
        title="Country status revenue matrix",
        role="heavy",
        category="heavy",
        group="expensive_aggregate",
        phase="base",
        weight=34,
        sql=(
            "SELECT c.country, o.status, count(*) AS orders, sum(o.total_cents) AS revenue_cents "
            "FROM orders o "
            "JOIN customers c ON c.id = o.customer_id "
            "GROUP BY c.country, o.status ORDER BY revenue_cents DESC"
        ),
        what="Revenue by country and fulfillment status.",
        why="Operations repeatedly asks for this large matrix, and it benefits from being precomputed.",
    ),
    Query(
        id="H06",
        title="Customer cohort revenue",
        role="heavy",
        category="heavy",
        group="expensive_aggregate",
        phase="base",
        weight=10,
        sql=(
            "SELECT date_trunc('month', c.created_at) AS cohort_month, c.segment, "
            "count(o.id) AS orders, sum(o.total_cents) AS revenue_cents "
            "FROM customers c "
            "JOIN orders o ON o.customer_id = c.id "
            "GROUP BY 1, c.segment ORDER BY cohort_month DESC, c.segment"
        ),
        what="Revenue from customer signup cohorts by segment.",
        why="Cohort analysis is a classic low-frequency, high-cost dashboard query.",
    ),
    Query(
        id="H07",
        title="Category by channel revenue",
        role="heavy",
        category="heavy",
        group="expensive_aggregate",
        phase="base",
        weight=34,
        sql=(
            "SELECT p.category, sum(oi.qty * oi.unit_cents) AS revenue_cents, "
            "count(*) AS lines "
            "FROM order_items oi "
            "JOIN products p ON p.id = oi.product_id "
            "WHERE oi.product_id <= 500 "
            "GROUP BY p.category ORDER BY revenue_cents DESC"
        ),
        what="Revenue for the hot-product slice by category.",
        why="It still scans the item fact table, but the hot-product filter keeps it in the intended heavy band.",
    ),
    Query(
        id="H08",
        title="Top customers by spend",
        role="heavy",
        category="heavy",
        group="expensive_aggregate",
        phase="base",
        weight=12,
        sql=(
            "SELECT c.id, c.full_name, c.segment, c.country, "
            "count(o.id) AS orders, sum(o.total_cents) AS revenue_cents "
            "FROM customers c "
            "JOIN orders o ON o.customer_id = c.id "
            "GROUP BY c.id, c.full_name, c.segment, c.country "
            "ORDER BY revenue_cents DESC LIMIT 50"
        ),
        what="The customers with the highest lifetime spend.",
        why="A large grouped join is low-frequency but dominates wall time when uncached.",
    ),
    Query(
        id="H09",
        title="Revenue by country",
        role="heavy",
        category="heavy",
        group="expensive_aggregate",
        phase="base",
        weight=50,
        sql=(
            "SELECT c.country, sum(o.total_cents) AS revenue_cents, count(*) AS orders "
            "FROM orders o "
            "JOIN customers c ON c.id = o.customer_id "
            "WHERE o.status <> 'cancelled' "
            "GROUP BY c.country ORDER BY revenue_cents DESC"
        ),
        what="Country-level order revenue for active orders.",
        why="A broad order/customer rollup is expensive enough for the cost-focused policy without being frequent.",
    ),
    Query(
        id="H10",
        title="Category country revenue",
        role="heavy",
        category="heavy",
        group="expensive_aggregate",
        phase="base",
        weight=9,
        sql=(
            "SELECT p.category, c.country, sum(oi.qty * oi.unit_cents) AS revenue_cents "
            "FROM order_items oi "
            "JOIN products p ON p.id = oi.product_id "
            "JOIN orders o ON o.id = oi.order_id "
            "JOIN customers c ON c.id = o.customer_id "
            "WHERE oi.product_id BETWEEN 51 AND 250 "
            "GROUP BY p.category, c.country ORDER BY revenue_cents DESC LIMIT 40"
        ),
        what="Geo-merchandising revenue by category and country.",
        why="This cross-dimensional report is costly but low-frequency, making it a clear Most expensive candidate.",
    ),

    # Mid-tier dashboard queries.
    Query(
        id="M01",
        title="Customer segment mix",
        role="moderate",
        category="moderate",
        group="mid_tier",
        phase="base",
        weight=7,
        sql="SELECT segment, count(*) AS customers FROM customers GROUP BY segment ORDER BY customers DESC",
        what="Customer counts by segment.",
        why="A small dashboard tile scans customers but avoids the multi-million-row fact tables.",
    ),
    Query(
        id="M02",
        title="Customer country mix",
        role="moderate",
        category="moderate",
        group="mid_tier",
        phase="base",
        weight=6,
        sql="SELECT country, count(*) AS customers FROM customers GROUP BY country ORDER BY customers DESC",
        what="Customer counts by country.",
        why="Frequent enough to show up, but the scan is mid-tier rather than a top cache target.",
    ),
    Query(
        id="M03",
        title="Signup trend by day",
        role="moderate",
        category="moderate",
        group="mid_tier",
        phase="base",
        weight=24,
        sql=(
            "SELECT date_trunc('day', created_at) AS day, count(*) AS customers "
            "FROM customers GROUP BY 1 ORDER BY 1 DESC LIMIT 30"
        ),
        what="Daily customer signup trend.",
        why="This powers a compact growth tile and stays in the middle of the cost spectrum.",
    ),
    Query(
        id="M04",
        title="Product category price stats",
        role="moderate",
        category="moderate",
        group="mid_tier",
        phase="base",
        weight=22,
        sql=(
            "SELECT p.category, count(*) AS lines, avg(oi.unit_cents)::int AS avg_unit_cents "
            "FROM order_items oi "
            "JOIN products p ON p.id = oi.product_id "
            "WHERE oi.id BETWEEN 1 AND 75000 "
            "GROUP BY p.category ORDER BY lines DESC"
        ),
        what="Sampled line-item price statistics by category.",
        why="A bounded primary-key slice makes this a real dashboard query without pushing it into the heavy tier.",
    ),
    Query(
        id="M05",
        title="Recent id-window status mix",
        role="moderate",
        category="moderate",
        group="mid_tier",
        phase="base",
        weight=20,
        sql=(
            "SELECT status, count(*) AS orders, sum(total_cents) AS revenue_cents "
            "FROM orders WHERE id BETWEEN 1450000 AND 1500000 "
            "GROUP BY status ORDER BY orders DESC"
        ),
        what="Status mix for a recent operational slice.",
        why="The primary-key range keeps this useful but below the heavy aggregate tier.",
    ),
    Query(
        id="M06",
        title="Early id-window channel mix",
        role="moderate",
        category="moderate",
        group="mid_tier",
        phase="base",
        weight=18,
        sql=(
            "SELECT channel, count(*) AS orders, sum(total_cents) AS revenue_cents "
            "FROM orders WHERE id BETWEEN 1 AND 50000 "
            "GROUP BY channel ORDER BY revenue_cents DESC"
        ),
        what="Channel revenue for a bounded order slice.",
        why="A range scan creates a visible mid-tier cost without competing with the cache winners.",
    ),
    Query(
        id="M07",
        title="Hot customer slice",
        role="moderate",
        category="moderate",
        group="mid_tier",
        phase="base",
        weight=16,
        sql=(
            "SELECT customer_id, count(*) AS orders, sum(total_cents) AS revenue_cents "
            "FROM orders WHERE id BETWEEN 300000 AND 380000 "
            "GROUP BY customer_id ORDER BY orders DESC LIMIT 20"
        ),
        what="Top customers inside a bounded traffic slice.",
        why="This is intentionally useful and repeated, but still cheaper than the full-table rollups.",
    ),

    # Row-heavy list/detail pages.
    Query(
        id="R01",
        title="Recent orders list",
        role="row_heavy",
        category="row_heavy",
        group="row_heavy",
        phase="base",
        weight=9,
        sql=(
            "SELECT id, customer_id, placed_at, status, channel, total_cents "
            "FROM orders ORDER BY id DESC LIMIT 1000"
        ),
        what="The latest orders page.",
        why="It returns many rows but uses an ordered primary-key walk rather than an expensive aggregate.",
    ),
    Query(
        id="R02",
        title="Customer directory page",
        role="row_heavy",
        category="row_heavy",
        group="row_heavy",
        phase="base",
        weight=8,
        sql="SELECT id, email, full_name, country, segment FROM customers ORDER BY id LIMIT 1000",
        what="A paged customer directory.",
        why="Large result transfer makes it visible in row metrics without making it a cache winner.",
    ),
    Query(
        id="R03",
        title="Product catalog page",
        role="row_heavy",
        category="row_heavy",
        group="row_heavy",
        phase="base",
        weight=7,
        sql="SELECT id, name, category, price_cents, active FROM products ORDER BY id LIMIT 1000",
        what="A catalog browse page.",
        why="It is row-heavy and common, but the bounded primary-key scan is not the expensive path.",
    ),
    Query(
        id="R04",
        title="Order item export page",
        role="row_heavy",
        category="row_heavy",
        group="row_heavy",
        phase="base",
        weight=6,
        sql=(
            "SELECT id, order_id, product_id, qty, unit_cents "
            "FROM order_items ORDER BY id DESC LIMIT 2000"
        ),
        what="A fulfillment export page with many line items.",
        why="It stresses rows returned rather than CPU-heavy grouping.",
    ),

    # Decline examples.
    Query(
        id="D01",
        title="Live server time",
        role="uncacheable",
        category="decline",
        group="decline_examples",
        phase="base",
        weight=3,
        denylist=True,
        sql="SELECT now() AS server_time",
        what="Current server timestamp.",
        why="This stays in the demo as a denylist example because the value is intentionally volatile.",
    ),
    Query(
        id="D02",
        title="Random product picks",
        role="uncacheable",
        category="decline",
        group="decline_examples",
        phase="base",
        weight=2,
        denylist=True,
        sql="SELECT id, name FROM products WHERE active ORDER BY random() LIMIT 10",
        what="Ten random active products.",
        why="The query is deliberately nondeterministic and flagged for the demo denylist.",
    ),
    Query(
        id="D03",
        title="Below-threshold product probe",
        role="cheap",
        category="decline",
        group="decline_examples",
        phase="base",
        weight=1,
        sql="SELECT id FROM products WHERE id = 7",
        what="A tiny product existence probe.",
        why="It runs rarely and is far below the cost threshold by design.",
    ),
    Query(
        id="D04",
        title="Below-threshold customer probe",
        role="cheap",
        category="decline",
        group="decline_examples",
        phase="base",
        weight=1,
        sql="SELECT id FROM customers WHERE id = 7007",
        what="A tiny customer existence probe.",
        why="This is a low-frequency primary-key hit and should be declined as not worth caching.",
    ),
    Query(
        id="D05",
        title="Recursive CTE unsupported",
        role="uncacheable",
        category="decline",
        group="decline_examples",
        phase="base",
        weight=4,
        sql=(
            "WITH RECURSIVE hops(n) AS ("
            "SELECT 1 UNION ALL SELECT n + 1 FROM hops WHERE n < 6"
            ") SELECT sum(n) AS checksum FROM hops"
        ),
        what="A recursive CTE candidate.",
        why="ReadySet rejected this shape in the empirical probe, so it demonstrates a genuine unsupported decline.",
    ),
    Query(
        id="D06",
        title="Generate series unsupported",
        role="uncacheable",
        category="decline",
        group="decline_examples",
        phase="base",
        weight=4,
        sql="SELECT g.n, g.n * g.n AS square FROM generate_series(1, 12) AS g(n)",
        what="A set-returning generate_series candidate.",
        why="ReadySet rejected this FROM shape in the empirical probe, so it is a real unsupported example.",
    ),

    # Late-arriving group: phase-B queries start inactive and can be activated.
    Query(
        id="L01",
        title="Late hot product lookup",
        role="cheap",
        category="cheap",
        group="late_arriving",
        phase="late",
        weight=0,
        activation_weight=120,
        sql="SELECT id, name, price_cents, active FROM products WHERE id = 88",
        what="A newly hot product lookup family.",
        why="Activating it mid-run makes last_seen and count-driven ranking visibly shift.",
    ),
    Query(
        id="L02",
        title="Late hot customer lookup",
        role="cheap",
        category="cheap",
        group="late_arriving",
        phase="late",
        weight=0,
        activation_weight=110,
        sql="SELECT id, full_name, country, segment FROM customers WHERE id = 512",
        what="A newly hot customer lookup family.",
        why="This gives the shifting-load beat a cheap high-frequency entrant.",
    ),
    Query(
        id="L03",
        title="Late country dashboard",
        role="heavy",
        category="heavy",
        group="late_arriving",
        phase="late",
        weight=0,
        activation_weight=7,
        sql=(
            "SELECT c.country, sum(o.total_cents) AS revenue_cents, count(*) AS orders "
            "FROM orders o JOIN customers c ON c.id = o.customer_id "
            "GROUP BY c.country ORDER BY revenue_cents DESC"
        ),
        what="A new country-level revenue dashboard.",
        why="It arrives late and gives QueryPilot a fresh expensive aggregate to notice.",
    ),
    Query(
        id="L04",
        title="Late category country revenue",
        role="heavy",
        category="heavy",
        group="late_arriving",
        phase="late",
        weight=0,
        activation_weight=6,
        sql=(
            "SELECT p.category, c.country, sum(oi.qty * oi.unit_cents) AS revenue_cents "
            "FROM order_items oi "
            "JOIN products p ON p.id = oi.product_id "
            "JOIN orders o ON o.id = oi.order_id "
            "JOIN customers c ON c.id = o.customer_id "
            "WHERE oi.product_id <= 50 "
            "GROUP BY p.category, c.country ORDER BY revenue_cents DESC LIMIT 40"
        ),
        what="A new geo-merchandising dashboard.",
        why="This is a late, heavy cross-dimensional rollup for the shifting-load phase.",
    ),
    Query(
        id="L05",
        title="Late cohort channel dashboard",
        role="heavy",
        category="heavy",
        group="late_arriving",
        phase="late",
        weight=0,
        activation_weight=5,
        sql=(
            "SELECT date_trunc('month', c.created_at) AS cohort_month, o.channel, "
            "count(*) AS orders, sum(o.total_cents) AS revenue_cents "
            "FROM customers c JOIN orders o ON o.customer_id = c.id "
            "GROUP BY 1, o.channel ORDER BY cohort_month DESC, o.channel"
        ),
        what="A new cohort-by-channel dashboard.",
        why="It mirrors an analytics request that appears after the initial workload has settled.",
    ),
    Query(
        id="L06",
        title="Late fulfillment list",
        role="row_heavy",
        category="row_heavy",
        group="late_arriving",
        phase="late",
        weight=0,
        activation_weight=8,
        sql=(
            "SELECT id, order_id, product_id, qty, unit_cents "
            "FROM order_items WHERE id BETWEEN 2500000 AND 2502500 ORDER BY id"
        ),
        what="A newly opened fulfillment line-item page.",
        why="This adds a row-heavy shape late without competing with aggregate cache winners.",
    ),
    Query(
        id="L07",
        title="Late segment rollup",
        role="moderate",
        category="moderate",
        group="late_arriving",
        phase="late",
        weight=0,
        activation_weight=18,
        sql=(
            "SELECT segment, country, count(*) AS customers "
            "FROM customers GROUP BY segment, country ORDER BY customers DESC"
        ),
        what="A newly popular customer segmentation tile.",
        why="This gives phase B a mid-tier query with a different last_seen signature.",
    ),
]


def workload_dicts() -> list[dict]:
    return [q.as_dict() for q in WORKLOAD]


def load_sqls(weighted: bool = True, include_inactive: bool = False) -> WeightedSQLs:
    """The SQL list the load driver fires, with metadata for weighted drivers."""
    return WeightedSQLs(WORKLOAD, weighted=weighted, include_inactive=include_inactive)

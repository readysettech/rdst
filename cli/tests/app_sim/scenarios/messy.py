"""messy: a legacy billing CRM with classic ORM and hand-written SQL anti-patterns.

customers is a 40-column table; invoices is the big one. Most fixes are query rewrites.

Plants:
  M1 `SELECT *` on the 40-column customers table for a list view that shows four columns.
     Rewrite to name the needed columns.
  M2 `email = ? OR alt_email = ?`; alt_email has no index, so every lookup scans customers.
     Index alt_email (then both engines combine the two indexes), or rewrite as a UNION of two
     indexed lookups plus that index.
  M3 `date(invoices.created_at) = ?` defeats invoices_created_at_idx. Rewrite as a half-open
     range on created_at. That index looks unused because of this; dropping it is wrong.
  M4 Implicit cast. MySQL: invoice_no is varchar and the app binds an integer, so MySQL casts
     every row and can't use invoices_invoice_no_idx. PostgreSQL (where varchar = integer is an
     error): the app binds ids parsed from JSON as floats, so `invoices.id = 12345.0` compares
     bigint to numeric and the primary key can't be used. Fix the bind type in the app.
  M5 `NOT IN (SELECT invoices.customer_id ...)` over a nullable column: no anti-join, a full
     invoices scan per call, and because some customer_id are NULL it never returns a row.
     Rewrite as NOT EXISTS.
  M6 A huge IN list (250, 500 or 1000 ids). PostgreSQL 16 records each length as its own
     statement. Rewrite as `= ANY(%s)` with an array bind, a join to a VALUES list or batches.
  M7 Two correlated scalar subqueries per row in the account-manager report. Rewrite as one
     LEFT JOIN with GROUP BY (or LATERAL).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Iterator

from tests.app_sim.scenarios.base import BOTH, Scenario, skewed

FIRST = ("Ada", "Bo", "Cy", "Di", "Ed", "Flo", "Gil", "Hal", "Ina", "Jem", "Kit", "Lou")
LAST = ("Archer", "Baker", "Carter", "Dalton", "Ellis", "Foster", "Grant", "Hayes", "Irving",
        "Jensen", "Keller", "Lowe")
CITIES = (("Austin", "TX"), ("Denver", "CO"), ("Boston", "MA"), ("Seattle", "WA"),
          ("Miami", "FL"), ("Chicago", "IL"))
STATUSES = ("active",) * 8 + ("prospect", "churned")
TIERS = ("bronze", "silver", "gold", "platinum")
INVOICE_STATUSES = ("paid",) * 7 + ("open", "open", "overdue")
INVOICE_BASE = 10_000_000
IN_SIZES = (250, 500, 1000)


class App(Scenario):
    name = "messy"
    tables = {
        "account_managers": ("id {pk}", "name varchar(80) NOT NULL",
                             "email varchar(120) NOT NULL", "region varchar(16) NOT NULL"),
        "customers": (
            "id {pk}", "account_manager_id bigint NOT NULL", "email varchar(120) NOT NULL",
            "alt_email varchar(120)", "phone varchar(32)", "first_name varchar(40) NOT NULL",
            "last_name varchar(40) NOT NULL", "company varchar(80) NOT NULL",
            "title varchar(60)", "department varchar(60)", "address1 varchar(120)",
            "address2 varchar(120)", "city varchar(60)", "state varchar(8)",
            "postal_code varchar(12)", "country varchar(2) NOT NULL",
            "timezone varchar(40) NOT NULL", "locale varchar(8) NOT NULL",
            "currency varchar(3) NOT NULL", "status varchar(16) NOT NULL",
            "tier varchar(16) NOT NULL", "source varchar(24)", "campaign varchar(40)",
            "referrer varchar(120)", "notes varchar(255)", "score integer NOT NULL",
            "lifetime_value_cents bigint NOT NULL", "credit_limit_cents bigint NOT NULL",
            "discount_pct smallint NOT NULL", "tax_id varchar(24)", "website varchar(120)",
            "linkedin varchar(120)", "twitter varchar(40)", "newsletter smallint NOT NULL",
            "marketing_opt_in smallint NOT NULL", "last_contacted_at {ts}",
            "created_at {ts} NOT NULL", "updated_at {ts} NOT NULL", "deleted_at {ts}",
            "legacy_ref varchar(24)"),
        "invoices": ("id {pk}", "customer_id bigint", "invoice_no varchar(20) NOT NULL",
                     "amount_cents bigint NOT NULL", "status varchar(16) NOT NULL",
                     "created_at {ts} NOT NULL", "due_at {ts} NOT NULL", "paid_at {ts}"),
    }
    indexes = (
        ("customers_email_idx", "customers", ("email",), False, "M2"),
        ("customers_manager_idx", "customers", ("account_manager_id",), False, "M1"),
        ("invoices_customer_idx", "invoices", ("customer_id",), False, "M7"),
        # M3: only a sargable range on created_at can use it.
        ("invoices_created_at_idx", "invoices", ("created_at",), False, "M3"),
        # M4: an integer bind defeats it on MySQL.
        ("invoices_invoice_no_idx", "invoices", ("invoice_no",), False, "M4"),
    )
    base_sizes = {"account_managers": 40, "customers": 80000, "invoices": 300000}
    queries = {
        # M1
        "customers_for_manager": ("SELECT * FROM customers WHERE customers.account_manager_id = "
                                  "%(account_manager_id_1)s ORDER BY customers.created_at DESC "
                                  "LIMIT %(param_1)s"),
        # M2
        "customer_by_any_email": ("SELECT customers.id, customers.first_name, "
                                  "customers.last_name, customers.company FROM customers "
                                  "WHERE customers.email = %(email_1)s OR "
                                  "customers.alt_email = %(alt_email_1)s"),
        # M3
        "invoices_for_day": ("SELECT invoices.id, invoices.invoice_no, invoices.amount_cents, "
                             "invoices.status FROM invoices WHERE date(invoices.created_at) = "
                             "%(param_1)s ORDER BY invoices.id"),
        # M4
        "invoice_lookup": {
            "postgresql": ("SELECT invoices.id, invoices.customer_id, invoices.amount_cents, "
                           "invoices.status FROM invoices WHERE invoices.id = %(id_1)s"),
            "mysql": ("SELECT invoices.id, invoices.customer_id, invoices.amount_cents, "
                      "invoices.status FROM invoices WHERE invoices.invoice_no = "
                      "%(invoice_no_1)s"),
        },
        # M5
        "clean_customers_count": ("SELECT count(*) AS count_1 FROM customers "
                                  "WHERE customers.account_manager_id = "
                                  "%(account_manager_id_1)s AND customers.id NOT IN "
                                  "(SELECT invoices.customer_id FROM invoices "
                                  "WHERE invoices.status = %(status_1)s)"),
        # M6
        "customers_by_ids": ("SELECT customers.id, customers.first_name, customers.last_name, "
                             "customers.email, customers.tier FROM customers "
                             "WHERE customers.id IN ({values})"),
        # M7
        "manager_report": ("SELECT customers.id, customers.company, (SELECT max(invoices.created_at) "
                           "FROM invoices WHERE invoices.customer_id = customers.id) AS last_invoice_at, "
                           "(SELECT sum(invoices.amount_cents) FROM invoices "
                           "WHERE invoices.customer_id = customers.id AND invoices.status = "
                           "%(status_1)s) AS open_amount FROM customers "
                           "WHERE customers.account_manager_id = %(account_manager_id_1)s "
                           "ORDER BY customers.company LIMIT %(param_1)s"),
        "customer_get": "SELECT * FROM customers WHERE customers.id = %(id_1)s",
        "invoices_for_customer": ("SELECT invoices.id, invoices.invoice_no, invoices.amount_cents, "
                                  "invoices.status, invoices.due_at FROM invoices "
                                  "WHERE invoices.customer_id = %(customer_id_1)s "
                                  "ORDER BY invoices.id DESC"),
        "invoice_insert": ("INSERT INTO invoices (customer_id, invoice_no, amount_cents, status, "
                           "created_at, due_at) VALUES (%(customer_id)s, %(invoice_no)s, "
                           "%(amount_cents)s, %(status)s, %(created_at)s, %(due_at)s)"),
        "invoice_pay": ("UPDATE invoices SET status=%(status)s, paid_at=%(paid_at)s "
                        "WHERE invoices.id = %(id_1)s"),
    }
    journeys = {"customer_list": 20, "customer_lookup": 10, "invoice_lookup": 22,
                "day_invoices": 8, "bulk_fetch": 6, "customer_view": 22, "create_invoice": 8,
                "pay_invoice": 4}
    jobs = (("manager_report", 10.0), ("clean_customers", 30.0))
    default_rps = 3.0
    default_threads = 3

    def rows(self, table: str, seed: int = 7) -> Iterator[tuple[Any, ...]]:
        rng = random.Random(f"messy:{table}:{seed}")
        n, anchor = self.n, self.anchor
        if table == "account_managers":
            for i in range(1, n[table] + 1):
                yield (i, f"Manager {i}", f"am{i}@billing.example",
                       ("east", "west", "central")[i % 3])
        elif table == "customers":
            for i in range(1, n[table] + 1):
                first, last = rng.choice(FIRST), rng.choice(LAST)
                city, state = rng.choice(CITIES)
                created = anchor - timedelta(seconds=rng.randrange(1500 * 86400))
                alt = f"{first.lower()}.{last.lower()}{i}@alt.example" if rng.random() < 0.3 \
                    else None
                yield (i, 1 + rng.randrange(n["account_managers"]), f"customer{i}@example.com",
                       alt, f"+1-555-{i % 10000:04d}", first, last,
                       f"{last} {('Holdings', 'Group', 'Partners', 'Co')[i % 4]}",
                       ("CFO", "Controller", "AP Clerk", None)[i % 4],
                       ("Finance", "Operations", None)[i % 3],
                       f"{rng.randrange(1, 9999)} Main St", None if i % 3 else "Suite 200",
                       city, state, f"{rng.randrange(10000, 99999)}", "US", "America/Chicago",
                       "en-US", "USD", STATUSES[rng.randrange(len(STATUSES))],
                       TIERS[rng.randrange(4)], ("web", "partner", "event", None)[i % 4],
                       f"campaign-{i % 37}" if i % 2 else None,
                       "https://search.example/?q=billing" if i % 5 == 0 else None,
                       "Prefers email. Net 30." if i % 7 == 0 else None, rng.randrange(100),
                       rng.randrange(10 ** 7), rng.randrange(10 ** 6), rng.randrange(20),
                       f"TX-{i:08d}", f"https://{last.lower()}{i}.example",
                       None, None, int(rng.random() < 0.5), int(rng.random() < 0.3),
                       created + timedelta(days=rng.randrange(400)), created,
                       created + timedelta(days=rng.randrange(400)), None, f"LEG-{i:07d}")
        elif table == "invoices":
            for i in range(1, n[table] + 1):
                created = anchor - timedelta(seconds=rng.randrange(365 * 86400))
                status = INVOICE_STATUSES[rng.randrange(len(INVOICE_STATUSES))]
                # Invoices of deleted customers keep a NULL customer_id (M5's NULL trap).
                customer = None if rng.random() < 0.005 else skewed(rng, n["customers"], 1.3)
                yield (i, customer, str(INVOICE_BASE + i), rng.randrange(1000, 500000), status,
                       created, created + timedelta(days=30),
                       created + timedelta(days=rng.randrange(1, 40)) if status == "paid"
                       else None)
        else:
            raise KeyError(table)

    def _manager(self, rng: random.Random) -> int:
        return 1 + rng.randrange(self.n["account_managers"])

    def _customer(self, rng: random.Random) -> int:
        return skewed(rng, self.n["customers"], 1.3)

    def j_customer_list(self, db: Any, rng: random.Random) -> None:
        db.all("customers_for_manager", account_manager_id_1=self._manager(rng), param_1=50)

    def j_customer_lookup(self, db: Any, rng: random.Random) -> None:
        typed = f"customer{self._customer(rng)}@example.com"
        db.all("customer_by_any_email", email_1=typed, alt_email_1=typed)

    def j_invoice_lookup(self, db: Any, rng: random.Random) -> None:
        invoice = 1 + rng.randrange(self.n["invoices"])
        if self.engine == "postgresql":
            db.one("invoice_lookup", id_1=float(invoice))
        else:
            db.one("invoice_lookup", invoice_no_1=INVOICE_BASE + invoice)

    def j_day_invoices(self, db: Any, rng: random.Random) -> None:
        day = (self.anchor - timedelta(days=1 + rng.randrange(360))).date()
        db.all("invoices_for_day", param_1=day)

    def j_bulk_fetch(self, db: Any, rng: random.Random) -> None:
        size = rng.choice(IN_SIZES)
        ids = rng.sample(range(1, self.n["customers"] + 1), size)
        db.listed("customers_by_ids", ids)

    def j_customer_view(self, db: Any, rng: random.Random) -> None:
        customer = self._customer(rng)
        db.one("customer_get", id_1=customer)
        db.all("invoices_for_customer", customer_id_1=customer)

    def j_create_invoice(self, db: Any, rng: random.Random) -> None:
        now = datetime.now()
        db.insert("invoice_insert", customer_id=self._customer(rng),
                  invoice_no=str(9 * INVOICE_BASE + rng.randrange(INVOICE_BASE)),
                  amount_cents=rng.randrange(1000, 500000), status="open", created_at=now,
                  due_at=now + timedelta(days=30))

    def j_pay_invoice(self, db: Any, rng: random.Random) -> None:
        db.execute("invoice_pay", status="paid", paid_at=datetime.now(),
                   id_1=1 + rng.randrange(self.n["invoices"]))

    def b_manager_report(self, db: Any, rng: random.Random) -> None:
        db.all("manager_report", status_1="open", account_manager_id_1=self._manager(rng),
               param_1=100)

    def b_clean_customers(self, db: Any, rng: random.Random) -> None:
        db.one("clean_customers_count", account_manager_id_1=self._manager(rng),
               status_1="overdue")


EXPECTED: tuple[dict[str, Any], ...] = (
    {"plant": "M1", "type": "rewrite_query", "engines": BOTH, "table": "customers",
     "statements": ["customers_for_manager", "customer_get"], "query_contains": "SELECT *",
     "priority": "medium", "rewrite": "name the columns the page shows",
     "acceptable_also": [{"type": "add_index", "columns": ["account_manager_id", "created_at"]}],
     "why": "The list view reads all 40 columns of 50 rows to show four."},
    {"plant": "M2", "type": "add_index", "engines": BOTH, "table": "customers",
     "columns": ["alt_email"], "priority": "high", "statements": ["customer_by_any_email"],
     "acceptable_alternatives": {"both": "rewrite as UNION of two indexed lookups, with the "
                                         "alt_email index"},
     "why": "One side of the OR is unindexed, so every lookup scans all of customers."},
    {"plant": "M3", "type": "rewrite_query", "engines": BOTH, "table": "invoices",
     "statements": ["invoices_for_day"], "query_contains": "date(invoices.created_at)",
     "priority": "high",
     "rewrite": "invoices.created_at >= :day AND invoices.created_at < :day + 1 day",
     "acceptable_alternatives": {"postgresql": "expression index on date(created_at)",
                                 "mysql": "functional index on (date(created_at))"},
     "why": "The function on the column defeats invoices_created_at_idx; every call scans "
            "invoices."},
    {"plant": "M4", "type": "rewrite_query", "engines": ("mysql",), "table": "invoices",
     "statements": ["invoice_lookup"], "query_contains": "invoices.invoice_no =",
     "priority": "high", "rewrite": "bind invoice_no as a string",
     "why": "varchar compared to an integer casts every row; invoices_invoice_no_idx is never "
            "used."},
    {"plant": "M4", "type": "rewrite_query", "engines": ("postgresql",), "table": "invoices",
     "statements": ["invoice_lookup"], "query_contains": "invoices.id =", "priority": "high",
     "rewrite": "bind the id as an integer, not a float",
     "why": "bigint = numeric casts the column, so a primary-key lookup becomes a full scan. "
            "The normalized text looks fine; mean time and rows read give it away."},
    {"plant": "M5", "type": "rewrite_query", "engines": BOTH, "table": "customers",
     "statements": ["clean_customers_count"], "query_contains": "NOT IN", "priority": "medium",
     "rewrite": "NOT EXISTS (SELECT 1 FROM invoices WHERE invoices.customer_id = customers.id "
                "AND invoices.status = ?)",
     "why": "NOT IN over a nullable column blocks the anti-join and, with NULLs present, "
            "returns nothing."},
    {"plant": "M6", "type": "rewrite_query", "engines": BOTH, "table": "customers",
     "statements": ["customers_by_ids"], "query_contains": "IN (", "priority": "low",
     "rewrite": "= ANY(%s) with an array bind (PostgreSQL), a join to a VALUES list, or "
                "batches of ~100",
     "why": "Hundreds of literals per call; on PostgreSQL every list length is its own "
            "statement."},
    {"plant": "M7", "type": "rewrite_query", "engines": BOTH, "table": "invoices",
     "statements": ["manager_report"], "query_contains": "(SELECT max(invoices.created_at)",
     "priority": "medium",
     "rewrite": "LEFT JOIN invoices ... GROUP BY customers.id (or LATERAL)",
     "why": "Two dependent subqueries per customer row."},
)

MUST_NOT: tuple[dict[str, Any], ...] = (
    {"type": "drop_index", "index": "invoices_created_at_idx", "plant": "M3",
     "why": "Looks unused only because M3 defeats it; the rewrite uses it."},
    {"type": "drop_index", "index": "invoices_invoice_no_idx", "engines": ("mysql",),
     "plant": "M4", "why": "Looks unused only because of the implicit cast."},
    {"type": "add_index", "table": "invoices", "columns": ["created_at"], "plant": "M3",
     "why": "Already exists."},
    {"type": "add_index", "table": "invoices", "columns": ["invoice_no"], "plant": "M4",
     "why": "Already exists."},
    {"type": "drop_index", "index": "invoices_customer_idx", "plant": "M7"},
)

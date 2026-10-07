"""healthy: a small, well-run helpdesk app. The negative control.

Every hot query has the index it needs, every index is used, nothing sorts or groups enough to
spill, and the data fits in memory. Columns that are updated (status, updated_at) are not
indexed except where a hot read needs status, so PostgreSQL keeps most updates HOT.

Plants:
  H0 Nothing important to change. The right output is an all-clear: no high or medium index,
     query or setting recommendations for this database. Low-priority notes are tolerable.

Caveat: settings are instance-wide and the shop database on the same containers plants a
work_mem / tmp_table_size spill. A setting change justified by this database's statements is
wrong; one justified by shop's is not this scenario's concern. Likewise MySQL's 128M buffer
pool is shared by every scenario and shop, so raising innodb_buffer_pool_size is defensible at
the instance level and is not a failure of this control.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Iterator

from tests.app_sim.scenarios.base import BOTH, Scenario, skewed

PLANS = ("free", "pro", "pro", "enterprise")
STATUSES = ("closed",) * 8 + ("open", "pending")
SUBJECTS = ("Cannot log in", "Invoice question", "Export is slow", "Feature request",
            "Password reset", "Bug in reports", "API rate limit", "Billing address change")


class App(Scenario):
    name = "healthy"
    tables = {
        "agents": ("id {pk}", "name varchar(80) NOT NULL", "email varchar(120) NOT NULL"),
        "customers": ("id {pk}", "email varchar(120) NOT NULL", "name varchar(80) NOT NULL",
                      "plan varchar(16) NOT NULL", "created_at {ts} NOT NULL"),
        "tickets": ("id {pk}", "customer_id bigint NOT NULL", "assignee_id bigint NOT NULL",
                    "status varchar(16) NOT NULL", "priority smallint NOT NULL",
                    "subject varchar(120) NOT NULL", "created_at {ts} NOT NULL",
                    "updated_at {ts} NOT NULL"),
        "comments": ("id {pk}", "ticket_id bigint NOT NULL", "author_kind varchar(8) NOT NULL",
                     "author_id bigint NOT NULL", "body varchar(400) NOT NULL",
                     "created_at {ts} NOT NULL"),
    }
    indexes = (
        ("customers_email_key", "customers", ("email",), True, "H0"),
        ("tickets_customer_created_idx", "tickets", ("customer_id", "created_at"), False, "H0"),
        ("tickets_assignee_status_created_idx", "tickets",
         ("assignee_id", "status", "created_at"), False, "H0"),
        ("comments_ticket_created_idx", "comments", ("ticket_id", "created_at"), False, "H0"),
    )
    base_sizes = {"agents": 50, "customers": 20000, "tickets": 100000, "comments": 200000}
    queries = {
        "customer_by_email": ("SELECT customers.id, customers.name, customers.plan FROM customers "
                              "WHERE customers.email = %(email_1)s"),
        "customer_get": ("SELECT customers.id, customers.email, customers.name, customers.plan "
                         "FROM customers WHERE customers.id = %(pk_1)s"),
        "agent_get": "SELECT agents.id, agents.name FROM agents WHERE agents.id = %(pk_1)s",
        "tickets_for_customer": ("SELECT tickets.id, tickets.status, tickets.subject, "
                                 "tickets.created_at FROM tickets WHERE tickets.customer_id = "
                                 "%(customer_id_1)s ORDER BY tickets.created_at DESC "
                                 "LIMIT %(param_1)s"),
        "ticket_get": ("SELECT tickets.id, tickets.customer_id, tickets.assignee_id, "
                       "tickets.status, tickets.priority, tickets.subject, tickets.created_at "
                       "FROM tickets WHERE tickets.id = %(pk_1)s"),
        "ticket_comments": ("SELECT comments.id, comments.author_kind, comments.author_id, "
                            "comments.body, comments.created_at FROM comments "
                            "WHERE comments.ticket_id = %(ticket_id_1)s "
                            "ORDER BY comments.created_at LIMIT %(param_1)s"),
        "agent_queue": ("SELECT tickets.id, tickets.priority, tickets.subject, tickets.created_at "
                        "FROM tickets WHERE tickets.assignee_id = %(assignee_id_1)s AND "
                        "tickets.status = %(status_1)s ORDER BY tickets.created_at "
                        "LIMIT %(param_1)s"),
        "ticket_insert": ("INSERT INTO tickets (customer_id, assignee_id, status, priority, "
                          "subject, created_at, updated_at) VALUES (%(customer_id)s, "
                          "%(assignee_id)s, %(status)s, %(priority)s, %(subject)s, "
                          "%(created_at)s, %(updated_at)s)"),
        "comment_insert": ("INSERT INTO comments (ticket_id, author_kind, author_id, body, "
                           "created_at) VALUES (%(ticket_id)s, %(author_kind)s, %(author_id)s, "
                           "%(body)s, %(created_at)s)"),
        "ticket_touch": ("UPDATE tickets SET status=%(status)s, updated_at=%(updated_at)s "
                         "WHERE tickets.id = %(id_1)s"),
    }
    journeys = {"customer_portal": 30, "view_ticket": 30, "agent_queue": 20, "reply": 12,
                "new_ticket": 5, "login": 3}
    default_rps = 6.0
    default_threads = 3

    def rows(self, table: str, seed: int = 7) -> Iterator[tuple[Any, ...]]:
        rng = random.Random(f"healthy:{table}:{seed}")
        n, anchor = self.n, self.anchor
        if table == "agents":
            for i in range(1, n[table] + 1):
                yield (i, f"Agent {i}", f"agent{i}@helpdesk.example")
        elif table == "customers":
            for i in range(1, n[table] + 1):
                yield (i, f"customer{i}@example.com", f"Customer {i}", PLANS[rng.randrange(4)],
                       anchor - timedelta(seconds=rng.randrange(700 * 86400)))
        elif table == "tickets":
            for i in range(1, n[table] + 1):
                created = anchor - timedelta(seconds=rng.randrange(365 * 86400))
                yield (i, skewed(rng, n["customers"], 1.5), 1 + rng.randrange(n["agents"]),
                       STATUSES[rng.randrange(len(STATUSES))], 1 + rng.randrange(4),
                       rng.choice(SUBJECTS), created,
                       created + timedelta(hours=rng.randrange(1, 200)))
        elif table == "comments":
            for i in range(1, n[table] + 1):
                ticket = 1 + (i - 1) * n["tickets"] // n[table]
                kind = "agent" if i % 2 else "customer"
                yield (i, ticket, kind, 1 + rng.randrange(n["agents"] if kind == "agent"
                                                          else n["customers"]),
                       "Thanks, looking into this now." * (1 + i % 4),
                       anchor - timedelta(seconds=rng.randrange(365 * 86400)))
        else:
            raise KeyError(table)

    def _ticket(self, rng: random.Random) -> int:
        return 1 + rng.randrange(self.n["tickets"])

    def j_customer_portal(self, db: Any, rng: random.Random) -> None:
        customer = skewed(rng, self.n["customers"], 1.5)
        db.one("customer_get", pk_1=customer)
        db.all("tickets_for_customer", customer_id_1=customer, param_1=20)

    def j_view_ticket(self, db: Any, rng: random.Random) -> None:
        ticket = db.one("ticket_get", pk_1=self._ticket(rng))
        if ticket:
            db.one("agent_get", pk_1=ticket[2])
            db.all("ticket_comments", ticket_id_1=ticket[0], param_1=50)

    def j_agent_queue(self, db: Any, rng: random.Random) -> None:
        agent = 1 + rng.randrange(self.n["agents"])
        db.one("agent_get", pk_1=agent)
        db.all("agent_queue", assignee_id_1=agent, status_1=rng.choice(("open", "pending")),
               param_1=25)

    def j_reply(self, db: Any, rng: random.Random) -> None:
        ticket = self._ticket(rng)
        now = datetime.now()
        with db.transaction():
            db.insert("comment_insert", ticket_id=ticket, author_kind="agent",
                      author_id=1 + rng.randrange(self.n["agents"]),
                      body="Following up on this ticket.", created_at=now)
            db.execute("ticket_touch", status=rng.choice(("pending", "closed", "open")),
                       updated_at=now, id_1=ticket)

    def j_new_ticket(self, db: Any, rng: random.Random) -> None:
        now = datetime.now()
        with db.transaction():
            ticket = db.insert("ticket_insert", customer_id=skewed(rng, self.n["customers"], 1.5),
                               assignee_id=1 + rng.randrange(self.n["agents"]), status="open",
                               priority=1 + rng.randrange(4), subject=rng.choice(SUBJECTS),
                               created_at=now, updated_at=now)
            db.insert("comment_insert", ticket_id=ticket, author_kind="customer",
                      author_id=1, body="Please help with this.", created_at=now)

    def j_login(self, db: Any, rng: random.Random) -> None:
        customer = skewed(rng, self.n["customers"], 1.5)
        db.one("customer_by_email", email_1=f"customer{customer}@example.com")


EXPECTED: tuple[dict[str, Any], ...] = (
    {"plant": "H0", "type": "no_action", "engines": BOTH, "max_priority": "low",
     "why": "Every hot query is served by an existing index, every index is used, and nothing "
            "spills. An all-clear is the right answer."},
) + tuple(
    {"plant": "H0", "type": "keep_index", "engines": BOTH, "table": table, "index": name,
     "why": "Serves a hot query."}
    for name, table, _cols, _unique, _plant in App.indexes
)

MUST_NOT: tuple[dict[str, Any], ...] = (
    {"type": "add_index", "priority": "high", "plant": "H0"},
    {"type": "add_index", "priority": "medium", "plant": "H0"},
    {"type": "rewrite_query", "priority": "high", "plant": "H0"},
    {"type": "change_setting", "statements_from": "healthy", "plant": "H0"},
) + tuple({"type": "drop_index", "index": name, "plant": "H0"}
          for name, _table, _cols, _unique, _plant in App.indexes)

"""saas: a multi-tenant B2B CRM. tenant_id is on every table and tenants are heavily skewed.

The biggest tenant owns about 15% of all rows and a long tail owns a handful each. Every request
loads its tenant and user (auth middleware), then renders one page.

Plants:
  S1 Contact list paginates with deep OFFSET (up to tens of thousands of rows skipped). The
     (tenant_id, updated_at) index already exists; only keyset pagination fixes it.
  S2 Contact search is `name ILIKE '%term%'` (MySQL: lower(name) LIKE lower('%term%')). A btree
     can't serve a leading wildcard; a trigram GIN (pg_trgm) or FULLTEXT index is the honest
     answer, or nothing.
  S3 Segment view filters on a JSON attribute (attrs->>'stage'), rare stages walk most of a
     tenant. An expression index (PostgreSQL) or an indexed generated column (MySQL) helps.
  S4 Dashboard tile runs count(*) over the tenant's activities on every load. After S6 it is
     still a full tenant range; a counter or a cached tile is the real fix.
  S5 ORM N+1: the activity feed lazy-loads each row's user with `SELECT ... WHERE users.id = ?`.
     It is the most frequent statement and each call is a fast PK lookup. The fix is eager
     loading (one IN query or a join), not an index.
  S6 activities has (created_at, tenant_id), the wrong order for the hot feed
     `WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?`. Add (tenant_id, created_at) and
     drop the wrong-order index, which nothing else needs. On MySQL the feed and count read
     hundreds of thousands of index entries; PostgreSQL walks the index backward with a
     tenant filter, cheap per call but visible in EXPLAIN as rows removed by filter.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from typing import Any, Iterator

from tests.app_sim.scenarios.base import BOTH, Scenario, unit

PLANS = ("free", "starter", "team", "business", "enterprise")
ROLES = ("admin", "member", "member", "member", "viewer")
FIRST = ("Ann", "Ben", "Cara", "Dev", "Eli", "Fay", "Gus", "Hana", "Ivan", "Jo", "Kai", "Lena",
         "Max", "Nia", "Omar", "Pia", "Raj", "Sara", "Tom", "Uma", "Vic", "Wen", "Yara", "Zed")
LAST = ("Anderson", "Brooks", "Chen", "Diaz", "Evans", "Fischer", "Garcia", "Hughes", "Ibsen",
        "Johnson", "Kowalski", "Larsen", "Morrison", "Nakamura", "Olsen", "Patel", "Quinn",
        "Robinson", "Silva", "Thompson", "Ueda", "Vargas", "Watson", "Young")
COMPANIES = ("Acme", "Globex", "Initech", "Umbrella", "Hooli", "Vandelay", "Wonka", "Stark",
             "Wayne", "Tyrell", "Soylent", "Cyberdyne", "Pied Piper", "Dunder")
SEARCH = ("son", "ann", "chen", "pat", "ar", "olsen", "acme", "lee", "ri", "garc", "wat", "kai")
# Stage weights: a few common values and rare ones that walk most of a tenant (S3).
STAGES = (("lead", 50), ("qualified", 25), ("customer", 15), ("churned", 8), ("vip", 2))
SOURCES = ("web", "referral", "event", "import", "partner")
COUNTRIES = ("US", "DE", "GB", "FR", "IN", "BR", "JP", "CA")
KINDS = ("call", "email", "meeting", "note", "task")
PAGE = 25
FEED = 15


def tenant_for(key: int, n_tenants: int, salt: int) -> int:
    """Rows go to tenants with a cube skew; ids below n_tenants seed one row per tenant."""
    if key <= n_tenants:
        return key
    return 1 + min(n_tenants - 1, int(n_tenants * unit(key, salt) ** 3))


class App(Scenario):
    name = "saas"
    tables = {
        "tenants": ("id {pk}", "name varchar(80) NOT NULL", "plan varchar(16) NOT NULL",
                    "created_at {ts} NOT NULL"),
        "users": ("id {pk}", "tenant_id bigint NOT NULL", "email varchar(120) NOT NULL",
                  "name varchar(80) NOT NULL", "role varchar(16) NOT NULL",
                  "created_at {ts} NOT NULL", "last_active_at {ts}"),
        "contacts": ("id {pk}", "tenant_id bigint NOT NULL", "owner_id bigint NOT NULL",
                     "name varchar(120) NOT NULL", "email varchar(120) NOT NULL",
                     "company varchar(120) NOT NULL", "phone varchar(32) NOT NULL",
                     "attrs {json} NOT NULL", "created_at {ts} NOT NULL",
                     "updated_at {ts} NOT NULL"),
        "activities": ("id {pk}", "tenant_id bigint NOT NULL", "contact_id bigint NOT NULL",
                       "user_id bigint NOT NULL", "kind varchar(16) NOT NULL",
                       "note varchar(200) NOT NULL", "created_at {ts} NOT NULL"),
    }
    indexes = (
        ("users_tenant_email_key", "users", ("tenant_id", "email"), True, None),
        # S1: the list view's index exists; deep OFFSET still reads every skipped row.
        ("contacts_tenant_updated_idx", "contacts", ("tenant_id", "updated_at"), False, "S1"),
        ("activities_contact_idx", "activities", ("contact_id",), False, None),
        # S6: wrong column order for the feed and the dashboard count.
        ("activities_created_tenant_idx", "activities", ("created_at", "tenant_id"), False,
         "S6"),
    )
    base_sizes = {"tenants": 300, "users": 15000, "contacts": 150000, "activities": 450000}
    queries = {
        "tenant_get": ("SELECT tenants.id, tenants.name, tenants.plan FROM tenants "
                       "WHERE tenants.id = %(pk_1)s"),
        # S5: the current user, and then once per feed row (N+1).
        "user_get": ("SELECT users.id, users.tenant_id, users.email, users.name, users.role "
                     "FROM users WHERE users.id = %(pk_1)s"),
        "user_touch": "UPDATE users SET last_active_at=%(last_active_at)s WHERE users.id = %(id_1)s",
        # S1
        "contact_list": ("SELECT contacts.id, contacts.name, contacts.email, contacts.company, "
                         "contacts.updated_at FROM contacts WHERE contacts.tenant_id = "
                         "%(tenant_id_1)s ORDER BY contacts.updated_at DESC "
                         "LIMIT %(param_1)s OFFSET %(param_2)s"),
        # S2
        "contact_search": {
            "postgresql": ("SELECT contacts.id, contacts.name, contacts.email, contacts.company "
                           "FROM contacts WHERE contacts.tenant_id = %(tenant_id_1)s AND "
                           "contacts.name ILIKE %(name_1)s ORDER BY contacts.name "
                           "LIMIT %(param_1)s"),
            "mysql": ("SELECT contacts.id, contacts.name, contacts.email, contacts.company "
                      "FROM contacts WHERE contacts.tenant_id = %(tenant_id_1)s AND "
                      "lower(contacts.name) LIKE lower(%(name_1)s) ORDER BY contacts.name "
                      "LIMIT %(param_1)s"),
        },
        # S3
        "contact_segment": {
            "postgresql": ("SELECT contacts.id, contacts.name, contacts.email, contacts.updated_at "
                           "FROM contacts WHERE contacts.tenant_id = %(tenant_id_1)s AND "
                           "(contacts.attrs ->> %(attrs_1)s) = %(param_1)s "
                           "ORDER BY contacts.updated_at DESC LIMIT %(param_2)s"),
            "mysql": ("SELECT contacts.id, contacts.name, contacts.email, contacts.updated_at "
                      "FROM contacts WHERE contacts.tenant_id = %(tenant_id_1)s AND "
                      "JSON_UNQUOTE(JSON_EXTRACT(contacts.attrs, %(attrs_1)s)) = %(param_1)s "
                      "ORDER BY contacts.updated_at DESC LIMIT %(param_2)s"),
        },
        # S6
        "activity_feed": ("SELECT activities.id, activities.contact_id, activities.user_id, "
                          "activities.kind, activities.note, activities.created_at "
                          "FROM activities WHERE activities.tenant_id = %(tenant_id_1)s "
                          "ORDER BY activities.created_at DESC LIMIT %(param_1)s"),
        # S4
        "activity_count": ("SELECT count(*) AS count_1 FROM activities "
                           "WHERE activities.tenant_id = %(tenant_id_1)s"),
        "contact_get": ("SELECT contacts.id, contacts.name, contacts.email, contacts.company, "
                        "contacts.phone, contacts.attrs, contacts.updated_at FROM contacts "
                        "WHERE contacts.id = %(pk_1)s AND contacts.tenant_id = %(tenant_id_1)s"),
        "contact_activities": ("SELECT activities.id, activities.contact_id, activities.user_id, "
                               "activities.kind, activities.note, activities.created_at "
                               "FROM activities WHERE activities.contact_id = %(contact_id_1)s "
                               "ORDER BY activities.created_at DESC LIMIT %(param_1)s"),
        "contact_update": ("UPDATE contacts SET attrs=%(attrs)s, updated_at=%(updated_at)s "
                           "WHERE contacts.id = %(id_1)s AND contacts.tenant_id = %(tenant_id_1)s"),
        "activity_insert": ("INSERT INTO activities (tenant_id, contact_id, user_id, kind, note, "
                            "created_at) VALUES (%(tenant_id)s, %(contact_id)s, %(user_id)s, "
                            "%(kind)s, %(note)s, %(created_at)s)"),
    }
    journeys = {"activity_feed": 24, "contact_list": 22, "contact_detail": 18,
                "contact_search": 12, "segment": 8, "dashboard": 8, "update_contact": 5,
                "log_activity": 3}
    default_rps = 4.0
    default_threads = 4

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        n = self.n
        self.tenant_users: dict[int, list[int]] = {}
        for user in range(1, n["users"] + 1):
            self.tenant_users.setdefault(self.user_tenant(user), []).append(user)
        self.tenant_contacts = [0] * (n["tenants"] + 1)
        for contact in range(1, n["contacts"] + 1):
            self.tenant_contacts[self.contact_tenant(contact)] += 1

    def user_tenant(self, user: int) -> int:
        return tenant_for(user, self.n["tenants"], 1)

    def contact_tenant(self, contact: int) -> int:
        return tenant_for(contact, self.n["tenants"], 2)

    @staticmethod
    def attrs(rng: random.Random) -> str:
        stage = rng.choices([s for s, _ in STAGES], weights=[w for _, w in STAGES], k=1)[0]
        return json.dumps({"stage": stage, "source": rng.choice(SOURCES),
                           "country": rng.choice(COUNTRIES), "score": rng.randrange(100)},
                          separators=(",", ":"))

    def rows(self, table: str, seed: int = 7) -> Iterator[tuple[Any, ...]]:
        rng = random.Random(f"saas:{table}:{seed}")
        n, anchor = self.n, self.anchor
        if table == "tenants":
            for i in range(1, n[table] + 1):
                yield (i, f"{COMPANIES[i % len(COMPANIES)]} {i}", PLANS[rng.randrange(5)],
                       anchor - timedelta(days=rng.randrange(1, 900)))
        elif table == "users":
            for i in range(1, n[table] + 1):
                created = anchor - timedelta(seconds=rng.randrange(400 * 86400))
                yield (i, self.user_tenant(i), f"user{i}@tenant{self.user_tenant(i)}.example",
                       f"{FIRST[i % 24]} {LAST[(i // 24) % 24]}", ROLES[rng.randrange(5)],
                       created, created + timedelta(days=rng.randrange(1, 30)))
        elif table == "contacts":
            for i in range(1, n[table] + 1):
                tenant = self.contact_tenant(i)
                name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
                created = anchor - timedelta(seconds=rng.randrange(700 * 86400))
                yield (i, tenant, rng.choice(self.tenant_users[tenant]), name,
                       f"{name.replace(' ', '.').lower()}{i}@mail.example",
                       f"{rng.choice(COMPANIES)} {rng.choice(('Inc', 'LLC', 'GmbH', 'Ltd'))}",
                       f"+1-555-{i % 10000:04d}", self.attrs(rng), created,
                       anchor - timedelta(seconds=rng.randrange(365 * 86400)))
        elif table == "activities":
            for i in range(1, n[table] + 1):
                contact = 1 + rng.randrange(n["contacts"])
                tenant = self.contact_tenant(contact)
                kind = KINDS[rng.randrange(5)]
                yield (i, tenant, contact, rng.choice(self.tenant_users[tenant]), kind,
                       f"{kind} logged about the {rng.choice(SOURCES)} follow-up #{i % 997}",
                       anchor - timedelta(seconds=rng.randrange(365 * 86400)))
        else:
            raise KeyError(table)

    # Traffic

    def _begin(self, db: Any, rng: random.Random) -> tuple[int, int]:
        user = 1 + rng.randrange(self.n["users"])
        tenant = self.user_tenant(user)
        db.one("tenant_get", pk_1=tenant)
        db.one("user_get", pk_1=user)
        if rng.random() < 0.3:
            db.execute("user_touch", last_active_at=datetime.now(), id_1=user)
        return tenant, user

    def _load_users(self, db: Any, rows: list[tuple[Any, ...]]) -> None:
        # S5: lazy loading, one query per distinct user (the identity map dedupes repeats).
        for user in dict.fromkeys(row[2] for row in rows):
            db.one("user_get", pk_1=user)

    def j_activity_feed(self, db: Any, rng: random.Random) -> None:
        tenant, _user = self._begin(db, rng)
        self._load_users(db, db.all("activity_feed", tenant_id_1=tenant, param_1=FEED))

    def j_contact_list(self, db: Any, rng: random.Random) -> None:
        tenant, _user = self._begin(db, rng)
        pages = max(1, self.tenant_contacts[tenant] // PAGE)
        # Users jump around, often deep: "last page", sorting by oldest, bulk exports by page.
        page = int(pages * rng.random() ** 0.7)
        db.all("contact_list", tenant_id_1=tenant, param_1=PAGE, param_2=page * PAGE)

    def j_contact_detail(self, db: Any, rng: random.Random) -> None:
        self._begin(db, rng)
        contact = 1 + rng.randrange(self.n["contacts"])
        tenant = self.contact_tenant(contact)
        db.one("contact_get", pk_1=contact, tenant_id_1=tenant)
        self._load_users(db, db.all("contact_activities", contact_id_1=contact, param_1=10))

    def j_contact_search(self, db: Any, rng: random.Random) -> None:
        tenant, _user = self._begin(db, rng)
        db.all("contact_search", tenant_id_1=tenant, name_1=f"%{rng.choice(SEARCH)}%",
               param_1=20)

    def j_segment(self, db: Any, rng: random.Random) -> None:
        tenant, _user = self._begin(db, rng)
        stage = rng.choice([s for s, _ in STAGES])
        path = "stage" if self.engine == "postgresql" else '$."stage"'
        db.all("contact_segment", tenant_id_1=tenant, attrs_1=path, param_1=stage, param_2=50)

    def j_dashboard(self, db: Any, rng: random.Random) -> None:
        tenant, _user = self._begin(db, rng)
        db.one("activity_count", tenant_id_1=tenant)
        self._load_users(db, db.all("activity_feed", tenant_id_1=tenant, param_1=5))

    def j_update_contact(self, db: Any, rng: random.Random) -> None:
        self._begin(db, rng)
        contact = 1 + rng.randrange(self.n["contacts"])
        db.execute("contact_update", attrs=self.attrs(rng), updated_at=datetime.now(),
                   id_1=contact, tenant_id_1=self.contact_tenant(contact))

    def j_log_activity(self, db: Any, rng: random.Random) -> None:
        tenant, user = self._begin(db, rng)
        contact = 1 + rng.randrange(self.n["contacts"])
        kind = rng.choice(KINDS)
        db.insert("activity_insert", tenant_id=self.contact_tenant(contact), contact_id=contact,
                  user_id=user, kind=kind, note=f"{kind} from the web app",
                  created_at=datetime.now())


EXPECTED: tuple[dict[str, Any], ...] = (
    {"plant": "S1", "type": "rewrite_query", "engines": BOTH, "table": "contacts",
     "statements": ["contact_list"], "query_contains": "OFFSET", "priority": "medium",
     "rewrite": "keyset pagination: WHERE tenant_id = ? AND (updated_at, id) < (?, ?) "
                "ORDER BY updated_at DESC, id DESC LIMIT 25",
     "why": "Deep pages skip tens of thousands of rows through contacts_tenant_updated_idx. "
            "The index is already right; only keyset pagination removes the skipped reads."},
    {"plant": "S2", "type": "special_index", "engines": BOTH, "table": "contacts",
     "columns": ["name"], "statements": ["contact_search"], "priority": "low",
     "acceptable_skip": True,
     "kind": {"postgresql": "GIN trigram index (pg_trgm, gin_trgm_ops) on name",
              "mysql": "FULLTEXT (ngram) index on name with MATCH ... AGAINST"},
     "why": "A leading-wildcard LIKE/ILIKE can't use a btree. A trigram or full-text index is "
            "the only index answer; saying nothing is also honest."},
    {"plant": "S3", "type": "add_index", "engines": BOTH, "table": "contacts",
     "columns": ["tenant_id", "attrs->>'stage'"], "expression": True, "priority": "medium",
     "statements": ["contact_segment"],
     "acceptable_alternatives": {
         "postgresql": "expression index (tenant_id, (attrs->>'stage')), or GIN on attrs with "
                       "the filter rewritten to attrs @> '{\"stage\": ...}'",
         "mysql": "generated column for attrs->>'$.stage' with an index (tenant_id, stage), or "
                  "a functional index"},
     "why": "Rare stages walk most of a big tenant through (tenant_id, updated_at), reading "
            "and parsing attrs for every row."},
    {"plant": "S4", "type": "app_change", "engines": BOTH, "table": "activities",
     "statements": ["activity_count"], "priority": "low",
     "fix": "per-tenant counter or a cached dashboard tile",
     "why": "count(*) per dashboard load reads every activity of the tenant. After S6 it is an "
            "index-only range, still proportional to tenant size."},
    {"plant": "S5", "type": "app_change", "engines": BOTH, "table": "users",
     "statements": ["user_get"], "priority": "medium",
     "fix": "eager-load feed users (one IN query, selectinload, or a join)",
     "why": "The most frequent statement is a PK lookup issued once per feed row. Each call is "
            "fast; the cost is round trips. No index can help."},
    {"plant": "S6", "type": "add_index", "engines": BOTH, "table": "activities",
     "columns": ["tenant_id", "created_at"], "priority": "high",
     "statements": ["activity_feed", "activity_count"],
     "why": "The feed filters tenant_id and sorts created_at DESC; (created_at, tenant_id) "
            "makes small tenants walk the whole recent history."},
    {"plant": "S6", "type": "drop_index", "engines": BOTH, "table": "activities",
     "index": "activities_created_tenant_idx", "reason": "wrong_order",
     "replaced_by": ["tenant_id", "created_at"], "priority": "medium",
     "why": "Once (tenant_id, created_at) exists nothing filters on created_at first."},
)

MUST_NOT: tuple[dict[str, Any], ...] = (
    {"type": "add_index", "table": "users", "plant": "S5",
     "why": "users.id is the primary key; N+1 is an app fix."},
    {"type": "add_index", "table": "contacts", "columns": ["name"], "index_kind": "btree",
     "plant": "S2", "why": "A btree can't serve '%term%'."},
    {"type": "add_index", "table": "contacts", "columns": ["tenant_id", "name"],
     "index_kind": "btree", "plant": "S2"},
    {"type": "add_index", "table": "contacts", "columns": ["tenant_id", "updated_at"],
     "plant": "S1", "why": "Already exists."},
    {"type": "drop_index", "index": "contacts_tenant_updated_idx", "plant": "S1"},
    {"type": "drop_index", "index": "users_tenant_email_key", "why": "Unique constraint."},
    {"type": "add_index", "table": "activities", "columns": ["tenant_id"], "plant": "S6",
     "why": "Should be (tenant_id, created_at)."},
)

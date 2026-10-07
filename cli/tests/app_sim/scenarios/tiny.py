"""tiny: a brand-new to-do app. 3 tables, a few hundred rows, 7 statements, ~1.5 requests/s.

It tests the first-run experience: little history, little data, nothing slow.

Plants:
  T0 Nothing worth changing. Every table fits in a page or two, so every plan is cheap. The
     right output says so, ideally noting that the data is too new to judge.
  T1 items.list_id has no index. At a few hundred rows a scan costs the same as an index probe;
     suggesting it at low priority ("once items grows") is fine, high priority is not.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Iterator

from tests.app_sim.scenarios.base import BOTH, Scenario

TITLES = ("Buy milk", "Call mom", "Fix bike", "Pay rent", "Book flights", "Water plants",
          "Renew passport", "Write blog post", "Clean garage", "Plan sprint")


class App(Scenario):
    name = "tiny"
    tables = {
        "users": ("id {pk}", "email varchar(120) NOT NULL", "name varchar(80) NOT NULL",
                  "created_at {ts} NOT NULL"),
        "lists": ("id {pk}", "user_id bigint NOT NULL", "title varchar(80) NOT NULL",
                  "created_at {ts} NOT NULL"),
        "items": ("id {pk}", "list_id bigint NOT NULL", "title varchar(120) NOT NULL",
                  "done smallint NOT NULL", "position integer NOT NULL",
                  "created_at {ts} NOT NULL"),
    }
    indexes = (
        ("users_email_key", "users", ("email",), True, None),
        ("lists_user_idx", "lists", ("user_id",), False, None),
    )
    base_sizes = {"users": 12, "lists": 30, "items": 250}
    queries = {
        "user_by_email": ("SELECT users.id, users.name FROM users "
                          "WHERE users.email = %(email_1)s"),
        "lists_for_user": ("SELECT lists.id, lists.title FROM lists "
                           "WHERE lists.user_id = %(user_id_1)s ORDER BY lists.id"),
        # T1: no index on items.list_id.
        "items_for_list": ("SELECT items.id, items.title, items.done, items.position FROM items "
                           "WHERE items.list_id = %(list_id_1)s ORDER BY items.position"),
        "item_insert": ("INSERT INTO items (list_id, title, done, position, created_at) "
                        "VALUES (%(list_id)s, %(title)s, %(done)s, %(position)s, "
                        "%(created_at)s)"),
        "item_toggle": "UPDATE items SET done=%(done)s WHERE items.id = %(id_1)s",
        "item_delete": "DELETE FROM items WHERE items.id = %(id_1)s",
        "list_insert": ("INSERT INTO lists (user_id, title, created_at) "
                        "VALUES (%(user_id)s, %(title)s, %(created_at)s)"),
    }
    journeys = {"open_list": 60, "add_item": 15, "toggle_item": 15, "delete_item": 8,
                "login": 1.5, "new_list": 0.5}
    default_rps = 1.5
    default_threads = 1

    def rows(self, table: str, seed: int = 7) -> Iterator[tuple[Any, ...]]:
        rng = random.Random(f"tiny:{table}:{seed}")
        n, anchor = self.n, self.anchor
        if table == "users":
            for i in range(1, n[table] + 1):
                yield (i, f"user{i}@todo.example", f"User {i}",
                       anchor - timedelta(hours=rng.randrange(1, 72)))
        elif table == "lists":
            for i in range(1, n[table] + 1):
                yield (i, 1 + (i - 1) % n["users"], f"List {i}",
                       anchor - timedelta(hours=rng.randrange(1, 72)))
        elif table == "items":
            for i in range(1, n[table] + 1):
                yield (i, 1 + rng.randrange(n["lists"]), rng.choice(TITLES),
                       int(rng.random() < 0.4), i, anchor - timedelta(minutes=rng.randrange(4000)))
        else:
            raise KeyError(table)

    def _list(self, db: Any, rng: random.Random) -> int | None:
        user = 1 + rng.randrange(self.n["users"])
        lists = db.all("lists_for_user", user_id_1=user)
        return int(rng.choice(lists)[0]) if lists else None

    def j_open_list(self, db: Any, rng: random.Random) -> None:
        list_id = self._list(db, rng)
        if list_id:
            db.all("items_for_list", list_id_1=list_id)

    def j_add_item(self, db: Any, rng: random.Random) -> None:
        list_id = self._list(db, rng)
        if list_id:
            db.insert("item_insert", list_id=list_id, title=rng.choice(TITLES), done=0,
                      position=rng.randrange(10 ** 6), created_at=datetime.now())

    def _item(self, db: Any, rng: random.Random) -> int | None:
        list_id = self._list(db, rng)
        items = db.all("items_for_list", list_id_1=list_id) if list_id else []
        return int(rng.choice(items)[0]) if items else None

    def j_toggle_item(self, db: Any, rng: random.Random) -> None:
        item = self._item(db, rng)
        if item:
            db.execute("item_toggle", done=int(rng.random() < 0.7), id_1=item)

    def j_delete_item(self, db: Any, rng: random.Random) -> None:
        item = self._item(db, rng)
        if item:
            db.execute("item_delete", id_1=item)

    def j_login(self, db: Any, rng: random.Random) -> None:
        db.one("user_by_email", email_1=f"user{1 + rng.randrange(self.n['users'])}@todo.example")

    def j_new_list(self, db: Any, rng: random.Random) -> None:
        db.insert("list_insert", user_id=1 + rng.randrange(self.n["users"]),
                  title=f"List {rng.randrange(1000)}", created_at=datetime.now())


EXPECTED: tuple[dict[str, Any], ...] = (
    {"plant": "T0", "type": "no_action", "engines": BOTH, "max_priority": "low",
     "why": "A few hundred rows and a handful of cheap statements. Nothing is worth changing "
            "yet; saying the history is too short to judge is ideal."},
    {"plant": "T1", "type": "skip_index", "engines": BOTH, "table": "items",
     "columns": ["list_id"], "statements": ["items_for_list"], "max_priority": "low",
     "why": "A scan of a few hundred rows is as cheap as an index probe. Low priority at most."},
)

MUST_NOT: tuple[dict[str, Any], ...] = (
    {"type": "add_index", "priority": "high", "plant": "T0"},
    {"type": "add_index", "table": "items", "columns": ["list_id"], "priority": "high",
     "plant": "T1"},
    {"type": "change_setting", "statements_from": "tiny", "plant": "T0"},
    {"type": "drop_index", "index": "users_email_key", "why": "Unique constraint."},
    {"type": "drop_index", "index": "lists_user_idx", "plant": "T0"},
)

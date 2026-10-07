"""App-sim scenarios: very different applications, each in its own database on both engines.

Each module defines `App` (schema, indexes, statements, seed, workload), `EXPECTED` and
`MUST_NOT` in the record format of tests/app_sim/expected.py. Record types beyond that file's:
`app_change` (fix belongs in application code, not the database), `special_index` (only a
non-btree index such as trigram or FULLTEXT helps), and `no_action` (the right answer is an
all-clear; `max_priority` bounds what is tolerable). `MUST_NOT` records with only a type and a
priority forbid any recommendation of that type at that priority for the scenario database.
"""

from __future__ import annotations

from types import ModuleType

from tests.app_sim.scenarios import healthy, iot, messy, saas, tiny

SCENARIOS: dict[str, ModuleType] = {
    "saas": saas, "iot": iot, "healthy": healthy, "tiny": tiny, "messy": messy}

RECORD_TYPES = ("add_index", "drop_index", "keep_index", "skip_index", "rewrite_query",
                "change_setting", "app_change", "special_index", "no_action")

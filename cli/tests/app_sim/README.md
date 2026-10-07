# App simulator

A small online shop that talks to a Docker PostgreSQL and a Docker MySQL the way a real app does:
a data-access layer that renders SQLAlchemy-shaped SQL, a connection pool, many short
autocommit statements and a few multi-statement transactions, a mix of reads and writes, and
background jobs. RDST watches it through pg_stat_statements and performance_schema, so the
statement shapes and their frequencies are what matter.

Every planted problem has a known right answer in `expected.py`.

## Usage

Run from `rdst/`:

```bash
.venv/bin/python -m tests.app_sim up        # start both containers, create users
.venv/bin/python -m tests.app_sim seed      # ~1.5M rows per engine, then zero the stats
.venv/bin/python -m tests.app_sim run --duration 600       # both engines at once
.venv/bin/python -m tests.app_sim run --engine pg --rps 20 --threads 8 --reset
.venv/bin/python -m tests.app_sim report    # top statements, verbs, table and index stats
.venv/bin/python -m tests.app_sim status
.venv/bin/python -m tests.app_sim down      # removes only these containers and volumes
```

`--engine` takes `pg`, `mysql` or `both` (default). `run` defaults to 12 requests/s per engine
(about 55 statements/s), 6 worker threads, seed 20261007, background jobs on. `seed` refuses to
start with under 12 GB free on `/mnt/c`.

| | PostgreSQL | MySQL |
|---|---|---|
| Container | `rdst-appsim-pg` (`postgres:16-alpine`) | `rdst-appsim-mysql` (`mysql:8.0`) |
| Host port | 15490 | 13330 |
| Database | `shop` | `shop` |
| App user | `shop_app` / `shop_app_pw` (owns the tables) | `shop_app` / `shop_app_pw` |
| Monitor user | `rdst_monitor` / `rdst_monitor_pw` | `rdst_monitor` / `rdst_monitor_pw` |
| Admin | `postgres` / `appsim_admin` | `root` / `appsim_admin` |

The monitor user is read only. On PostgreSQL it has `pg_read_all_stats`, CONNECT and SELECT on
the shop tables. On MySQL it has PROCESS, SELECT on `performance_schema.*`, `shop.*`,
`mysql.innodb_index_stats` and `mysql.innodb_table_stats`.

## Workload

Each request touches its session row, runs the page's statements and (mostly) logs an event.
Journey weights are in `traffic.JOURNEYS`: category browsing, product pages, search, add to cart,
view cart, checkout (inventory reserve, order, order lines, payment, cart convert in one
transaction), order history, order detail, returns and logins. Background jobs (`traffic.JOBS`)
ship orders and settle payments, restock, purge expired carts every 20s, run admin reports every
30s, run a rare support lookup and a "nightly" settlement batch every 2 minutes.

## Planted problems

| Id | Problem | Expected action |
|---|---|---|
| P1 | Product page looks products up by `slug`, which has no index (read-mostly table, hottest read) | Add index `products(slug)` |
| P2 | Two hot order-history reads: `count(*) WHERE customer_id = ?` and `WHERE customer_id = ? ORDER BY created_at DESC LIMIT 20` | Add one index `orders(customer_id, created_at)`, not a separate `(customer_id)` |
| P3 | `order_items_order_idx (order_id)` is a leading prefix of `order_items_order_product_idx (order_id, product_id)` | Drop `order_items_order_idx` |
| P4 | `events_kind_idx` is never used, and events takes an insert on most requests | Drop `events_kind_idx` |
| P5 | A support lookup on `events.session_id` (unindexed) runs about once a minute | Skip: don't add `events(session_id)`, or rank it low |
| P6 | `UPDATE sessions SET last_seen_at = ? WHERE token = ?` on every request; `token` has no index | Add index `sessions(token)` (unique is fine) |
| P7 | Cart cleanup `DELETE ... WHERE status = 'open' AND expires_at < ?` every 20s; `expires_at` has no index | Add index `carts(expires_at)`, medium priority |
| P8 | Login uses `lower(customers.email) = lower(?)`, which defeats the unique email index | Rewrite to `email = ?` with a lowered bind (an expression index is an acceptable alternative) |
| P9 | Admin reports spill: PostgreSQL `work_mem` at its 4MB default, MySQL `tmp_table_size` and `max_heap_table_size` at 1M | Raise `work_mem` on PostgreSQL, `tmp_table_size` on MySQL |
| P10 | `payments_created_at_idx` is used only by the bursty nightly settlement batch | Keep it |

`expected.MUST_NOT` lists the wrong answers, such as dropping the unique email index or the
batch's index.

More applications (multi-tenant SaaS, IoT ingest, a healthy control, a tiny new app and an
ORM-anti-pattern app), each in its own database on the same containers, live in `scenarios/`:
`.venv/bin/python -m tests.app_sim.scenarios <saas|iot|healthy|tiny|messy> setup|run|status|drop`.

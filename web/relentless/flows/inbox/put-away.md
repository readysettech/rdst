# Flow: inbox/put-away

Capability: inbox
Areas: inbox, monitoring
Modes: desktop, browser
Minutes: 20
Covers: web-apps/apps/rdst/src/features/inbox rdst/features/monitor/delivery.py rdst/features/monitor/attention.py

Every Inbox item must report something that really happened, appear once,
and keep the promise of each way of putting it away. This flow covers items
other than a lost connection, which monitoring/outage-alert covers.

## Mission

Connect PostgreSQL, turn on monitoring, and make monitoring notice
something: a query that gets much slower (`rt db exec postgres -- SQL`
repeatedly, then the same query made slower, for example by dropping an
index it uses), and a statement that runs for minutes (`SELECT pg_sleep(...)`
in a second session). Wait for the items.

1. For each item, check its claim against what you did and against
   `rt api GET /api/monitor/inbox`. Follow every link it offers: does it open
   the right database and query?
2. Resolve one (and undo it from the toast), resolve another and reopen it,
   and mute alerts with each mute choice the item's menu offers, for each
   duration. Reload and `rt restart`: does each state, the Mute rules list,
   the queue counts and the sidebar badge agree with what you did? Unmute a
   rule: do its items come back?
3. End the slow statement and undo the slowdown. Do the items that should
   clear do so, without changing what they recorded about when things
   happened?

## Prove

- Each item is true, appears once, and links to what it names.
- Every put-away state and the queue counts survive reload and restart.

## Cleanup

Recreate anything you dropped, end your sessions, remove mute rules, and
turn monitoring off.

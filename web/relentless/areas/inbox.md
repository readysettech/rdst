# Area: inbox

The Inbox (`/inbox`) is where monitoring tells a user something needs them:
a database that cannot be reached, a query that got worse, a fix ready to
apply. Test that what it reports really happened, that each alert appears
once, and that every way of putting one away keeps its promise. Name what you
find by what the app shows; when a step below has no matching control in this
build, cover the closest thing it offers and say so.

Inbox items come only from monitoring. Connect PostgreSQL, turn monitoring
on from the Database page, and run real traffic with `rt db exec postgres --
SQL` before you start. Never fabricate an item; when you cannot produce one,
record that check as not covered. `rt api GET /api/monitor/inbox` and
`/api/monitor/mute-rules` can corroborate the screen.

Cover at least:

- The empty Inbox before monitoring has seen anything, and the counts in the
  sidebar as items arrive.
- A real outage: `rt db pause postgres`, wait for scheduled checks to fail,
  and inspect the item that appears (urgency, time, what it says to do, and
  where its link goes). Resume the database: does the item close by itself
  rather than stay urgent?
- A second outage of the same database: one item or two, and what the
  repeated failure does to the counts.
- Every way the Inbox offers to put items away and bring them back
  (resolving, reopening, muting one database or all, undoing), and its mute
  rules. Each state after a reload and after `rt restart`, and the API
  agreeing with the screen.
- Open and closed items, and each section or filter the Inbox offers.
- Two databases (add MySQL): filtering by database, and an item's link
  opening the right database's page.
- Notification settings: each choice including quiet hours, saved state after
  a restart, the test notification, and on desktop whether an item raises a
  system notification when the setting says it will.
- What a query regression, a fix ready to apply, or the daily report looks
  like here when monitoring produces one; leave it untested when the run
  cannot cause it.
- Alerts must work signed out: local monitoring raises connection alerts
  without a Readyset account.

Resume every paused database, remove the mute rules you created, and turn
monitoring off when finished.

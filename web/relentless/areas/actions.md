# Area: actions

Actions (`/actions`) is the standing plan of changes worth making to the
user's databases, ranked across them by the database time each saves, with
its history of what was applied. Nothing on the page may change a database by
itself: the user runs the SQL, then tells RDST they did. Test that the plan is
true to its evidence, that every way of setting a change aside or reporting it
done is kept, and that nothing is written behind the user's back.

A plan needs AI monitoring, and AI monitoring needs a Readyset account: when
`rt status` says mail is available, sign in by email. Connect PostgreSQL, turn
on monitoring with deep analysis, and run real traffic that would gain from
indexes (repeated selective filters and joins on unindexed columns, some
slow) with `rt db exec postgres -- SQL`. The sample tables are too small for
any index to matter, so first create your own table of a few million rows
with `generate_series` and aim that traffic at it. Wait for scheduled readings and the
first plan; never seed data or call collect-now. Before and after each action
below, note `SELECT indexname FROM pg_indexes WHERE schemaname = 'public'`.

Cover at least:

- How the plan reaches the user: Home, the Inbox's item announcing fixes and
  where it leads, and whether that item closes once the plan is opened.
- Each change: what it claims (time saved, risk, effort, priority) against
  its evidence and the analysis behind it; the SQL and rollback SQL are valid
  for this database; groupings by kind and by priority keep its rank.
- Copying a change, including as a migration for each framework offered: the
  copied text says the same as the SQL shown, with the query's own parameters.
- Setting a change aside (with and without a date and reason), dismissing it
  with a reason, and bringing it back: each survives reload and `rt restart`.
- Applying a change yourself with `rt db exec`, then reporting it done: the
  Applied view, what it measures before and after, and undoing it (run the
  rollback yourself, then report it).
- A second database (MySQL): the database filter, the address keeping it,
  and changes staying with their own database.
- What the page says while there is no plan yet, while signed out, and while
  a database cannot be reached.

Turn monitoring off on every target at the end and restore anything you
changed in the databases.

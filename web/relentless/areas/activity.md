# Area: activity

Activity (`/agent-activity`) is RDST's own account of what it did on the
user's databases: each analysis, Ask question, report and check, with what it
ran, kept for a fixed period, and, at the top, the work running right now
with a way to stop it. Test that the account is complete and true, and that
running work can be followed and stopped from here.

Connect PostgreSQL and MySQL and an AI key, then cause activity you can
account for: questions in Ask, analyses from the Database page, a report run
on request, and monitoring if it is on. Note what each step should have run
and when. Generate real traffic with `rt db exec`; never seed data.

Cover at least:

- Every action you caused appears once, with the right database, time and
  kind; nothing appears that you did not cause. What an entry says it ran is
  what ran (compare with `rt logs --grep` and the database's statistics).
- Work in progress: start a slow analysis and a report, find them as running,
  stop one, and confirm it stopped (`rt api GET
  /api/monitor/targets/TARGET/analyze-jobs`). Leave and come back, reload and
  `rt restart` while work runs: what remains, and does the page say what
  happened to the rest?
- Each entry's link: does it open what it names, on the right database? A
  stopped or failed entry: does it say why, without calling a read a write?
- Filters by database and kind, alone and together, the address keeping them
  across reload and restart, and paging through older entries.
- Export: the file matches the filtered list, including text with commas,
  quotes and newlines; the stated retention agrees with Settings.
- Removing a database in Settings: its entries say so and lead nowhere false.
- The empty log on a fresh install, and the page while the backend is down
  (`rt kill-backend`) and after it returns.

Turn monitoring off on every target at the end and leave no work running.

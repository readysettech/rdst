# Area: home

Home answers, for every watched database, what needs the user, since when,
and how the day is going, and leads a new user through getting started: sign
in, connect a database, turn on monitoring. Test that it tells the truth at
every stage and that every link opens what it names, on the right database.

Start from a fresh install with the first-run guidance shown. Generate real
traffic with `rt db exec`; never seed data.

Cover at least:

- A fresh install: what opens first, each getting-started step, and where
  each leads. Follow them in order, then again skipping or closing each one:
  does Home still show the next step, and does it come back after reload and
  `rt restart` only when it should?
- One database, before and after monitoring is on; then a second database
  (MySQL). What Home says about each, against its Database page, the Inbox
  and Actions.
- Every link and button on Home: the database it opens and the view.
- A database that cannot be reached (`rt db pause postgres`): does Home say
  which one and since when, and recover after resume? The backend gone
  (`rt kill-backend`): what Home says and whether it comes back.
- The sidebar from Home: each database's mark and its tooltip against what
  RDST is doing there, the counts against the Inbox, adding a database.
- Removing the selected database in Settings, then coming back to Home;
  reload and `rt restart`.
- 900x620 window, keyboard only, Escape on every dialog.

Turn monitoring off on every target at the end.

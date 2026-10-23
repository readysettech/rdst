# Area: onboarding-connect

Connecting a database is the first thing every user does. Test getting from
an empty installation to a watched database, and every way that can go wrong.
Name what you find by what the app shows; when a step below has no matching
control in this build, cover the closest thing it offers and say so.

Start from a fresh install and add a database from wherever the app offers it
(Home, the sidebar, Settings, or a `?add=` link). Cover at least:

- The first-run pop-up opening on "Find your database": the provider tiles,
  the folded paste prompt (copying it, the waiting status) and "Connect
  manually instead" leading to the form.
- "Search for databases with Claude" (the stand-in Claude Code) shows the
  scan's steps and Claude's trace, then only its short list. "Just search this
  computer" is RDST's own scan, with no agent. The four projects
  under ~/work (see the run's dependencies) should be found in seconds, grouped
  by project, each saying what pointed at it. shop comes picked; only one
  database can be picked at a time. With its URL in .env, "Connect shop" adds it
  with no agent and opens it. "Set it up here with Claude Code" (the stand-in
  Claude Code) shows
  numbered steps, adds shop, checks it and asks to turn monitoring on, which
  opens shop's Turn on monitoring screen; turning it on there finishes the
  agent's run. Also: "Connect manually" on billing (the form comes filled in),
  the Supabase one's sign-in, leaving the view and coming back (the results
  stay), and importing again after adding (names mustn't clash).
- Adding the disposable PostgreSQL described below, both from its fields and
  as a connection string, and landing on its Database page with it selected
  and the turn-on step for monitoring offered. Turning monitoring on belongs to
  the monitoring area; here, leave it off and check the database stays
  unmonitored.
- Testing before adding, and what a database whose query statistics monitoring
  cannot read yet looks like: the steps shown, and adding it with monitoring
  off.
- Each field wrong in turn: host that does not resolve, closed port, wrong
  database, wrong user, wrong password, empty required fields. Every failure
  should say what is wrong and keep what the user typed.
- Names: duplicates of an existing database, whitespace, very long, unicode,
  characters that are awkward in URLs and file names (`/`, `..`, `%`, `#`).
- Double-clicking the add button, Escape or closing mid-test, going back,
  reloading mid-test.
- After adding: the database appears in Settings with the right details, the
  password is never shown back, and `rt api GET /api/configure/targets`
  agrees with the UI. Editing it there and saving.
- Removing the database from Settings, then what Home and the add flow show.
- `rt restart` after adding: the database, the selection, and anything the UI
  claimed to remember should still be there.
- The MySQL database as a second one, and switching between the two.
- Connecting through the SSH jump host from the advanced options: the
  database address is then `postgres:5432` or `mysql:3306`. Try a wrong
  jump-host port, a wrong user, and a missing key path, then test the tunnel
  from Settings.
- The database going away mid-test (`rt db pause postgres` while a test runs,
  then resume) and the jump host going away (`rt db pause bastion`) for a
  tunneled database.

Out of scope: cloud provider discovery needs accounts this run does not have.
Note it as not covered rather than blocked.

Leads:

- An invalid port has been reported as missing host, database and user.

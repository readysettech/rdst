# Area: onboarding-connect

Connecting a database is the first thing every user does. Test getting from
an empty installation to a watched database, and every way that can go wrong.
Name what you find by what the app shows; when a step below has no matching
control in this build, cover the closest thing it offers and say so.

Start from a fresh install and add a database from wherever the app offers it
(Home, the sidebar, Settings, or a `?add=` link). Cover at least:

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

# Area: settings-targets

Settings is one page where a user manages their account, AI, databases,
notifications, retained data and local data. Test that every setting does
what it says, is kept, and agrees with the rest of the app. Name what you find
by what the app shows; when a step below has no matching control in this
build, cover the closest thing it offers and say so.

Connect AI and the PostgreSQL database first, then open Settings from the
sidebar. Cover at least:

- Databases: what each row says about reachability, the default, and stored
  passwords, and each row's own checks. Pause the database (`rt db pause
  postgres`) and check again: does its row say it cannot be reached and why?
  Resume it.
- Adding a second database (the MySQL database), then making it the default,
  editing it, and removing it. After each, compare with
  `rt api GET /api/configure/targets` and with the database the sidebar and
  other pages select. Removing the default or the selected database: what
  becomes selected?
- A database through the SSH jump host: testing the tunnel with the jump host
  up and with `rt db pause bastion`.
- Importing from a CSV file: `rt upload TARGET FILE` with `targets.csv` from
  the run's fixtures (`rt db list` shows the path). It has two good rows, one
  without a host, and a duplicate. Do the counts and the log match the file?
  Then set credentials for the imported rows: for all at once, with a wrong
  and a right password, and leaving them for later. Rows left without a
  password: how does a user set one, and does the row recover?
- Links from other pages that open Settings or the database editor: does
  saving or cancelling return to the page that sent you?
- AI: the current provider, testing and updating the key with an empty value
  and with the real key (`rt fill-secret`), and switching to the included AI
  while signed out.
- Notifications and retained readings: each choice, its saved state after a
  restart, and whether the rest of the app follows it.
- Clearing local data. Afterward the app should behave as a fresh install:
  check what opens first, `rt api GET /api/configure/targets`, and whether the
  keychain entry is gone (`rt restart` and see if the key is still there).
- Provider integrations (AWS, Supabase, Neon, DigitalOcean) in the add flow:
  open each one signed out and try what needs no account, such as a rejected
  Neon API key and cancelling a sign-in. Mark sign-in and discovery as not
  covered.
- Offline: `rt restart --offline` blocks the internet but not the local
  databases. What do the key test, provider panels, and connectivity checks
  say? Then `rt restart --online`.

Leads:

- A non-ASCII Neon API key has produced an internal server error.
- Provider sign-in while offline has shown raw Python network errors.
- Sign-out confirms from the sidebar but may not from Settings.

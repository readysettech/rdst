# Flow: actions/plan-follow-through

Capability: actions
Areas: actions, monitoring
Modes: desktop, browser
Minutes: 25
Covers: web-apps/apps/rdst/src/features/actions rdst/features/monitor/actions.py rdst/features/monitor/action_stories.py

A plan on Actions is only worth following if each change is true to its
evidence and every way of dealing with it is kept. Nothing may touch the
database except what the user runs.

## Mission

A plan needs AI monitoring, and AI monitoring needs a Readyset account: when
`rt status` says mail is available, sign in by email; otherwise say so and
stop. Connect PostgreSQL, turn on monitoring with deep analysis, and run
real traffic that would gain from an index and from a rewrite (repeated
slow SELECTs with selective filters) until Actions shows a plan.

1. For every change in the plan, compare what it claims (the queries it
   speeds up, their share of database time, the evidence, the SQL) with
   `rt db exec postgres -- SQL` and the Database page. Copy its SQL, and copy
   it as a migration where offered: does what you copied run?
2. Set one change aside, bring it back, and set another aside; reload and
   `rt restart`. Do both choices hold?
3. Run one change's SQL yourself, then tell Actions you did it. Does it move
   to what was applied, and does RDST later confirm it from the database?
   Report one done without running it: what does RDST say when it checks?
4. Throughout, check with `rt db exec postgres -- SQL` (indexes, tables) that
   nothing changed in the database that you did not run.

## Prove

- Every change's numbers and SQL match its evidence; copied SQL runs.
- Set-aside and done states survive reload and restart, and nothing is
  written to the database behind the user's back.

## Cleanup

Drop the indexes you created, turn monitoring off, and sign out.

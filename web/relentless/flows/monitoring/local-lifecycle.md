# Flow: monitoring/local-lifecycle

Capability: monitoring
Areas: monitoring, desktop-shell
Modes: desktop
Minutes: 20
Covers: rdst/features/monitor/loop.py web-apps/apps/rdst/src/features/database/settings web-apps/apps/rdst-desktop/src/main/window-lifecycle.ts

A user turns on monitoring without a Readyset account and expects it to keep
working: on other pages, with the window closed, and across restarts. It
must collect on its own schedule and never claim AI results it cannot have.

## Mission

Start from a fresh install with no Readyset sign-in. Then:

1. Add PostgreSQL. On the Database page's turn-on step, read the setup
   checks, leave monitoring off once and confirm it stays off, then turn on
   local monitoring.
2. Run several different SELECTs with `rt db exec postgres -- SQL` and wait
   for scheduled readings: the chart and the Queries ranking should reflect
   what you ran. `rt api GET /api/monitor/targets/TARGET/health` may
   corroborate. Do not call collect-now.
3. Go to Inbox and Settings while traffic continues, then come back: did
   readings continue? Close the window, confirm `rt status` reports it
   hidden, run more traffic, wait, and `rt restart`. History, settings, and
   the selection survive, and a reading taken while hidden is there.
4. Turn monitoring off (cancel once, then confirm), run traffic, and check
   no new readings arrive while history stays. Turn it back on.

## Prove

- Scheduled readings, not forced ones, reach the page, including while the
  page is not open and the window is hidden.
- Signed out, the page says what AI monitoring would add without implying it
  ran.
- Off means off; history survives off, on, and restart.

## Cleanup

Turn monitoring off. Record anything the driver cannot observe as not
covered.

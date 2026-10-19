# Flow: activity/server-run-reload

Capability: activity
Areas: activity, monitoring
Modes: desktop, browser
Minutes: 15
Covers: web-apps/apps/rdst/src/features/agent-activity/RunningNow.tsx web-apps/apps/rdst/src/lib/backgroundRuns.ts rdst/shared/run_registry.py rdst/shared/api/routes/runs.py

Long operations the backend owns (database setup, analyses, reports run on
request) show at the top of Activity as running work and must survive
everything the page does not.

## Mission

Connect the PostgreSQL database (its setup is a run) and start one more
server-owned run: an analysis of a slow query from the Database page. Then:

1. Open Activity while it runs: what it shows running must match the page
   that started it.
2. Leave through the sidebar, come back to Activity, then `rt reload` and
   `rt restart`. Each time, does the run reattach with its real state
   (`rt api GET /api/runs/RUN_ID`)?
3. Stop it from Activity. The page and `rt api` must both settle as stopped,
   exactly once.
4. Start another, let it finish, and check the finished entry: its link, and
   whether it survives a reload and a restart.

## Prove

- Activity, the originating page, and `/api/runs/...` agree after every
  navigation, reload, and restart.
- Stopping reaches the backend; nothing keeps running or stays "Stopping".

## Cleanup

No run left in progress.

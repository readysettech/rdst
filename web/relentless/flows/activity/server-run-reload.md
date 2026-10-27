# Flow: activity/server-run-reload

Capability: activity
Areas: activity, monitoring
Modes: desktop, browser
Minutes: 15
Covers: web-apps/apps/rdst/src/features/agent-activity/RunningNow.tsx web-apps/apps/rdst/src/lib/backgroundRuns.ts rdst/shared/run_registry.py rdst/shared/api/routes/runs.py

Long operations the backend owns show at the top of Activity as running work
and must survive everything the page does not.

## Mission

Connect the PostgreSQL database and start work the backend carries on its own
past a page change: whatever this build shows at the top of Activity as
running, such as a database's setup, an analysis, or a report run on request.
Find which ones it is by starting them; do not assume. Work that finishes
before you can watch it does not settle the flow: start another kind, and
report blocked only after each kind has finished too fast. Then:

1. Open Activity while it runs: what it shows running must match the page
   that started it.
2. Leave through the sidebar, come back to Activity, then `rt reload` and
   `rt restart`. Each time, does the work reattach with its real state? Ask
   the backend too: find the request the page polls for it in
   `rt evidence --kinds api` and repeat it with `rt api`.
3. Stop it from Activity. The page and the backend must both settle as
   stopped, exactly once.
4. Start another, let it finish, and check the finished entry: its link, and
   whether it survives a reload and a restart.

## Prove

- Activity, the originating page, and the backend agree after every
  navigation, reload, and restart.
- Stopping reaches the backend; nothing keeps running or stays "Stopping".

## Cleanup

No run left in progress.

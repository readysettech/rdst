# Flow: server/browser-start

Capability: server
Areas: app-lifecycle
Modes: browser
Minutes: 10
Covers: rdst/shared/api/app.py
Direct: web-apps/apps/rdst/src/routes/__root.tsx web-apps/apps/rdst/src/main.tsx

`rdst web` is how browser users run RDST: the server starts, serves the
bundle and the API, and must recover or say so when it dies.

## Mission

1. From a fresh start, confirm the first load is complete: the AI gate or
   the app, no failed requests (`rt evidence --kinds request_failed,api`),
   and `rt api GET /api/status` healthy.
2. Use the app for one real step (connect PostgreSQL), then
   `rt kill-backend`. Use the page without restarting: what does every
   visible part say? It should blame the local server, not AI or the
   database.
3. `rt restart`. Everything saved before must be back; nothing is
   duplicated.
4. `rt logs` for start-up warnings or errors a person never sees.

## Prove

- Start-up is clean; the dead-server state is named as such; restart
  restores state.

## Cleanup

App running.

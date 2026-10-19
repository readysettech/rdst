# Flow: platform/demo-lifecycle

Capability: platform
Areas: platform
Modes: desktop, browser
Minutes: 25
Covers: web-apps/apps/rdst/src/features/platform web-apps/apps/rdst/src/lib/useDemo.ts rdst/features/qpdemo

The Readyset Platform page runs a caching demo in Docker on this machine. At
every moment it must say truthfully what state the demo is in, through
setup, interruptions, use and shut down.

## Mission

1. Open Platform and start the demo. Follow its setup to the end; it can
   take several minutes, so wait. Compare each step it reports with
   `docker ps` (via `rt api GET /api/demo/status` where the screen and the
   containers disagree).
2. Start it again from scratch and interrupt setup three ways, one at a
   time: leave for another page and come back, reload, and `rt restart`.
   After each, does the page say what really happened, and can you get to a
   working demo?
3. With the demo running, use what it offers: watch its traffic start, cache
   a query by hand and remove it, turn QueryPilot on and off, switch the
   policy. Does each promise on the screen happen, and does a failed action
   say so?
4. Shut the demo down. Are its containers gone, and does the page, after a
   reload and `rt restart`, agree?

## Prove

- The page never shows a demo as running that is not, or hides a failure.
- Shutting down leaves nothing running.

## Cleanup

Shut the demo down and confirm its containers are gone.

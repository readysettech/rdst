# Area: app-lifecycle

How the application behaves across reloads, restarts, and backend crashes.
In desktop mode this exercises the Electron shell, its bundled backend, and
the local server between them; in browser mode, `rdst web` restarts.

Cover at least:

- Persistence across `rt reload` and `rt restart`. Before restarting, change
  things the UI offers to remember: connect a target and select it, dismiss
  or advance the setup guide, accept a consent prompt, change display
  options. Afterwards, check each one on screen and compare with `rt api`.
  Compare `rt status` before and after: in desktop mode note whether the
  page origin changed, and what that did to anything the page keeps in
  `localStorage` (`rt storage get`).
- `rt kill-backend` while idle, while a page is loading, and while a
  streaming operation (a connection test, a schema load) is running. The UI
  should say the backend is gone rather than hang or show stale data as
  fresh. Then `rt restart` and check that it recovers fully.
- The database going away while the app is open (`rt db pause postgres`):
  what each page says, whether anything claims success, and how it recovers
  after `rt db resume postgres` without a restart.
- Starting a background job, navigating to other pages and back, then
  reloading: the job should either still be shown or the UI should be honest
  that it lost track of it.
- The smallest window (`rt viewport 900 620`) and a large one, on every page
  you visit: nothing clipped, overlapping, or unreachable.

Distinguish what the product promises from what it does not. Server-side
background runs can reattach after navigation; a job the browser itself was
running does not promise to survive a reload; nothing promises that a job
survives a backend restart. Report broken promises, and report silence or
misleading UI in the cases without a promise.

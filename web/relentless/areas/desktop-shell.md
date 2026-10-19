# Area: desktop-shell

Modes: desktop

The Electron shell around the app: the window, its bundled backend, and
what a desktop user expects from a native app.

Cover at least:

- Window controls drawn by the app on Linux: minimize, maximize and restore
  (does the icon change?), close. Resize to the minimum size
  (`rt viewport 900 620`) and to a very large window on every main page.
- Zoom: Ctrl+=, Ctrl+-, Ctrl+0 (`rt press Control+Equal`, and so on), and
  the native menu's View > Zoom In, Zoom Out, and Actual Size
  (`rt menu "View > Actual Size"`). Compare `window.devicePixelRatio`
  (`rt eval`) after each: does every way back to actual size agree with the
  size the app opened at?
- The rest of the native menu (`rt menu` lists it): View > Reload and Force
  Reload mid-operation, Toggle Full Screen, Window > Minimize.
- Links to Docs, Discord, and external sites: they should open outside the
  app, never inside the window.
- `rt restart` after changing things the app offers to remember: selected
  target, dismissed setup guide, EXPLAIN ANALYZE consent, display options,
  finished jobs. Compare the page origin before and after (`rt status`).
- `rt kill-backend` while idle and mid-operation, then use the app without
  restarting: does the app bring its backend back on its own, and what does
  it tell the user meanwhile? Then `rt restart`.
- Closing the window with monitoring on: `rt status` should report the window
  hidden rather than gone, monitoring should keep taking scheduled readings
  (run traffic with `rt db exec postgres -- SQL` and compare after), and
  reopening it (`rt restart`) shows that history.
- The tray icon's menu (`rt tray` lists it, `rt tray "A > B"` clicks): "Open
  RDST" with the window hidden, the Inbox and Monitoring entries against what
  the app shows, "Send test notification", and "Quit RDST" stopping the app
  and its backend. Opening the menu re-reads the databases: turn monitoring on
  for a second database and list the menu again.
- The setting that pushes alerts to this computer: what the app says when the
  system blocks notifications, and whether an Inbox alert raises one when the
  setting says it will.

Leads:

- Every restart gets a new origin, so everything kept in localStorage
  resets.
- Ctrl+0 resets zoom to 100 percent while the menu's Actual Size uses 80.

# Flow: server/desktop-sidecar-start

Capability: server
Areas: app-lifecycle, desktop-shell
Modes: desktop
Minutes: 12
Covers: web-apps/apps/rdst-desktop/src/main rdst/shared/api/app.py
Direct: web-apps/apps/rdst-desktop/scripts/build-sidecar.mjs web-apps/apps/rdst-desktop/scripts/lock-sidecar.mjs web-apps/apps/rdst-desktop/scripts/requirements-sidecar.lock web-apps/apps/rdst-desktop/electron-builder.yml web-apps/apps/rdst-desktop/package.json web-apps/apps/rdst-desktop/resources rdst/.buildkite/build_sidecar_linux.sh

The desktop app starts its bundled Python sidecar and a local static
server, and has to handle the sidecar dying under it.

## Mission

1. From a fresh start, confirm the window loads fully with no failed
   requests and a healthy `/api/status`; note how long start-up took
   (`rt status`, `rt logs`).
2. Connect PostgreSQL, then `rt kill-backend` while idle, and again while
   a request is in flight (start an Ask question first). What does the
   window show, and does anything recover without `rt restart`?
3. `rt restart`: the target and key are back, and the origin is new
   (`rt status`); note what that resets (selection, dismissed guides).
4. `rt menu "View > Reload"` after the backend is gone: does the window
   come back to a usable state or a blank one?

## Prove

- Each failure is shown as the local backend being gone, with a way out.
- Restart restores saved state.

## Cleanup

App running.

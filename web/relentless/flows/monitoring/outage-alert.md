# Flow: monitoring/outage-alert

Capability: monitoring
Areas: inbox, monitoring
Modes: desktop, browser
Minutes: 20
Covers: rdst/features/monitor/connection.py rdst/features/monitor/delivery.py web-apps/apps/rdst/src/features/inbox

When a monitored database goes down, the user hears about it once, in the
Inbox, with a way to check it, and the alert clears when the database comes
back. Everything here must come from a real outage.

## Mission

1. Connect PostgreSQL, turn on monitoring, and run some traffic. Open Inbox
   and note the unread count.
2. `rt db pause postgres` and wait for scheduled checks to fail. What appears
   in Inbox and the sidebar, when, and with what urgency? Follow the item's
   link: does it open the right database and say what to check?
3. `rt db resume postgres`. Does the item move to Recovered, and the unread
   count follow? Pause and resume again: one item or a new one, and is that
   what the screen claims?
4. Resolve one item, reopen it, mute another, then reload and `rt restart`.
   Each state must hold, and `rt api GET /api/monitor/inbox` must agree.

## Prove

- Each outage produces one truthful item, created by the schedule.
- Recovery clears urgency; put-away states survive what they promise to.

## Cleanup

Resume every paused database, unmute and remove mute rules you created, and
turn monitoring off.

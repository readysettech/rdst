# Area: shell-navigation

The chrome around every page: sidebar, header, account menu, feedback, error
pages, and how the app behaves when the environment around it breaks. Test
what every page shares, not any one feature.

Connect AI and PostgreSQL first. Cover at least:

- Every sidebar item and footer link, and the header breadcrumb from a
  nested view (a database report, an Inbox item). Does the current item stay
  highlighted? Docs and Join Discord must open outside the app.
- A path that does not exist (`rt goto /no-such-page`) and a real route with
  bad parameters (`/database?target=missing`, `/inbox?item=nope`,
  `/configure?panel=nope`). Is there a way back?
- Give feedback: empty, very long, unicode and HTML text, with and without a
  query selected, and Escape mid-typing. The app runs with telemetry off:
  does it still say "Thank you for your feedback!", and did anything reach
  the backend (`rt evidence --kinds api`)?
- Keyboard only: from the first Tab, "Skip to content", then every sidebar
  item, dialog, and menu. Focus must be visible, must not get trapped, and
  must return to the trigger when a dialog closes.
- Small windows. In browser mode, a narrow one (`rt viewport 700 800`)
  may swap the sidebar for a menu button: open it, close it with the button
  and Escape, and navigate from it. In desktop mode, use the minimum
  window (`rt viewport 900 620`).
- Account menu and sign-out, when `rt status` says mail is available: sign
  in with `rt mail address`, then sign out from the sidebar and from
  Settings > AI. What does the gate show afterward with an Anthropic key
  still saved?
- Offline: `rt restart --offline` blocks the internet but not the local
  databases, as on a plane. The AI gate, Ask, Docs links, and feedback
  should fail in ways that say so; local pages should keep working. Then
  `rt restart --online`.
- The backend going away: `rt kill-backend` on several pages, then use the
  app. Every page should say it lost the backend, not blame AI or the
  database. Then `rt restart`.
- Desktop only: `rt crash-renderer` crashes the page, as a GPU or memory
  fault would. Its reply shows whether the window recovered by itself. Then
  `rt restart`. `rt deeplink rdst://...` opens a link as the OS does for a
  second instance; the running window should come forward and the second
  instance should exit.

Leads:

- Feedback is sent through telemetry, so with telemetry off it shows the
  thank-you toast while nothing is sent.
- A renderer crash leaves a blank window: nothing in the app reloads it.
- A deep link focuses the window but drops the URL.

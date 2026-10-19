# Flow: ask/across-databases

Capability: ask
Areas: ask, monitoring
Modes: desktop, browser
Minutes: 20
Covers: web-apps/apps/rdst/src/features/ask web-apps/apps/rdst/src/routes/-ask-page.tsx rdst/features/ask/history

Ask is global, with the database chosen in the page and kept in the URL, and
it connects to the rest of the app. Every answer, history entry and link must
stay with the database it was asked on.

## Mission

Connect PostgreSQL and MySQL. When `rt status` says mail is available, sign
in to Readyset by email once (answers then come from Readyset's hosted AI);
if the email does not arrive within three minutes, say so and continue with
your own Anthropic key.

1. Which database a new question goes to, switching mid-session, the
   database in the URL, coming back to Ask from another page, reload and
   `rt restart`. Ask the same question on both databases.
2. Earlier questions: every answer appears at once, per database, and
   survives a restart; re-asking from history; the same SQL on two databases
   stays two entries.
3. From an answer, every link to another page: does the Database page open
   that query, does an analysis run on the right database and finish?
4. Into Ask from other pages (a query on the Database page, Home, Actions)
   where they link to it: is the question and database carried over?
5. Hosted AI against your own key: what Settings, Ask and Home say about
   which AI answers, switching between them, and what a failure of each says.
6. A 900x620 window, long answers and wide tables, keyboard-only use of the
   whole page, Escape in every dialog.

## Prove

- Nothing lands on the wrong database, and no answer or history entry is
  lost or replaced by another database's.
- What the app says about which AI answered matches the setting.

## Cleanup

Leave both databases connected only if they were before; sign out if you
signed in.

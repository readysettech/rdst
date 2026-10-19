# Area: monitoring

Continuous monitoring watches a connected database in the background: it
reads query statistics on a schedule, ranks the queries that cost the most,
and, when AI monitoring is on, analyzes problem queries on its own. The
Database page (`/database`) is where a user turns it on, watches it live,
reads its reports and its queries, and changes its settings. Test that it
really runs, says the truth about what it has and has not done, and survives
reloads, restarts, and turning it off. Name what you find by what the app
shows; when a step below has no matching control in this build, cover the
closest thing it offers and say so.

Monitoring reads from the database on a timer. Generate real traffic with
`rt db exec postgres -- SQL` (several different SELECTs, some slow, some
repeated) and wait for scheduled readings; a reading you forced does not prove
the schedule works. Read-only calls such as
`rt api GET /api/monitor/targets/TARGET/health` and `.../queries` can
corroborate the screen.

Cover at least:

- Turning it on right after adding PostgreSQL (adding lands on the Database
  page with the turn-on step offered): the setup checks and their fixes, the
  choices it offers about analysis, leaving monitoring off, then turning it
  on.
- The first readings: what the live view charts against the traffic you ran,
  the ranking of queries, each range and chart choice, and what the page says
  before it has enough history.
- Readings continuing while you are on other pages (Inbox, Settings, Ask),
  then after `rt reload` and `rt restart`: settings, history, and the
  selected database survive, and a fresh scheduled reading arrives.
- Monitoring settings: each control, saved state against
  `rt api GET /api/monitor/targets/TARGET/settings`, and reopening after a
  restart.
- Analysis: analyzing a query on request, navigating away and back while it
  runs, and the finished result's plan, numbers, and where its parameters
  came from. With automatic analysis off, analysis on request still works.
  Does any suggested rewrite return the same rows as the original query?
- Reports: they arrive on the schedule the page states, and nothing in the
  app generates one on demand. Check what the empty state promises against
  the saved report time, open any report that exists, and check old report
  links (`/db/TARGET`, `/db/TARGET/report`) land on the same database.
- MySQL as a second database: the setup check asks for a grant the fixture
  user lacks; apply the app's own fix as an administrator
  (`rt db exec mysql --admin -- SQL`), check again, and turn monitoring on.
  Then its own settings and queries, no leakage between the two, and
  switching databases repeatedly from the sidebar.
- Turning monitoring off: cancel once, then confirm. No new readings arrive
  while it is off, history stays, and turning it back on does not replay the
  first-run introduction.
- AI monitoring without Readyset sign-in: the page must say what it cannot do
  rather than imply analyses it did not run. When `rt status` says mail is
  available, sign in and check that automatic analysis starts from the
  schedule, without a request from you.

A personal Anthropic key alone does not turn on AI monitoring. Never seed
monitoring data, backdate history, or call collect-now to prove the schedule.
Leave long baselines, midnight rollover, and daily-limit resets untested unless
you actually observe them.

Turn monitoring off on every test database and leave no analysis running.

Leads:

- A suggested MySQL rewrite has put a sampled literal in place of the
  query's parameter, changing its results.

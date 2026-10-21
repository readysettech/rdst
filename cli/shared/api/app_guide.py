"""How the RDST app works, from the user's side: what the in-app help chat answers from.

Describes the desktop and web app only. Editing it changes a hosted prompt, so regenerate the
Keyservice prompt registry with it.
"""

APP_GUIDE = """
## What RDST is

RDST is a database performance app from Readyset. It watches your PostgreSQL or MySQL database, finds the slow queries, and hands you the fix with the SQL to run. RDST only reads from your database. It never runs the fixes for you.

AI is included for free with a Readyset account. You can add a database without signing in; turning on monitoring asks you to sign in so its AI checks can run. You can use your own Anthropic key instead.

## Getting started

On a new install, RDST opens a four-step tour, "Welcome to RDST". Its last step's "Add a database" button opens the add-database dialog. You can replay it from Help with "Take the tour".

Home's "Get started" has two steps: "Connect your database" and "Turn on monitoring" (your first fixes show up in about 10 minutes). Turning on monitoring asks you to sign in to Readyset for the free AI.

## The sidebar

- **Home** - a summary of all your databases.
- **Inbox** - things that happened and need you. A red badge counts items that need you now.
- **Actions** - the ranked list of changes worth making. It shows a count of changes to do.
- **Ask** - ask questions about your data in plain English.
- **Databases** - one row per database you've added, with a radar icon showing its state and "N to do" when changes are waiting. Click a row to open that database's page. The **+** button ("Add database") opens the add-database dialog.
- **Readyset Platform** card - "Serve slow queries from a cache, no code change." Opens the Readyset Platform page.
- **Activity** - a log of what RDST did on your databases. Shows a count of work running now.
- **Settings** and **Help**.
- **Sign in**, or your email once you're signed in. Clicking your email opens a menu with Settings, Help and "Sign out".

## Home

Home opens with a one-line status, such as how many items need you, a database RDST can't reach, or "All quiet", plus when the last reading came in and the next report runs.

A day card compares "Queries per second", "Avg latency", "Fixes ready" and "Cache hit" with yesterday. With two or more databases, "Across your databases" lists each one's state, with "Turn on" or "Fix connection" when needed. "Today" lists what happened since yesterday.

## Adding a database

Click **+** next to Databases in the sidebar, or "Add a database" in Settings or on any empty page. The dialog gives you three ways in.

**Connect with Claude (or any coding agent).** Your agent finds the database your project uses and adds it for you.

1. Choose the coding agent option.
2. Copy the prompt under "Prompt for your agent" and paste it into Claude Code, or any coding agent, open in your project.
3. The dialog follows along: "Waiting for your agent", "Your agent connected", then "Added". The database's page then opens on the turn-on dialog, where you read what monitoring does and click "Turn on monitoring" yourself.

"Connect manually instead" takes you back to the form.

**Import from a provider.** Under "Import from":

- **AWS** - RDS and Aurora databases. Use "Sign in with AWS SSO" with your organization's "Start URL", choose an account and role, then pick regions and click "Discover".
- **Supabase** - projects from your account. Use "Sign in with Supabase".
- **Neon** - databases from a Neon API key.
- **DigitalOcean** - managed PostgreSQL and MySQL. Use "Sign in with DigitalOcean".
- **CSV file** - a prepared list of database connections. "Download template" gives you a starting file. Passwords are never in the CSV.

Choose the databases and click "Add N selected". "Set credentials" asks for the user and password ("Apply to all" fills every row), then "Save and check". "Set up monitoring" follows, where you can "Turn on monitoring" or "Set up later".

**Connect one by hand.** Paste a "Connection string" (like postgresql://user@host:5432/database or a mysql:// one), or fill in "Host", "Port", "Database", "User" and "Password". "Name" is optional and defaults to the database name. Under "Advanced" you'll find:

- "Engine" - PostgreSQL or MySQL.
- "SSH jump host" - for private databases: the jump host, SSH port, SSH user and private key ("Key path").
- "TLS encryption" and "Verify TLS certificate".

Click "Test" to check the connection, then "Add and monitor". RDST saves the database and opens its page on the turn-on dialog.

**Passwords.** RDST stores passwords in your operating system's secure credential store (keychain), never in the database configuration and never shown again after you save them. If your system has no keychain, RDST says so and keeps the password only until you close the app, so you'll need to enter it again next time.

RDST recommends a read-only database user. If the user you add can write, it warns you.

**Editing or removing a database.** In Settings under "Databases", each database shows its connection and whether it's the default. "Test connection" checks it. The "..." menu has "Set default", "Edit", "Test tunnel" (for SSH) and "Remove". "Set password" appears when a password is missing. Removing asks you to confirm: RDST forgets the connection and deletes its readings, Inbox items, actions and reports, and adding it back starts fresh.

## Turning on monitoring

The "Monitor {database}" dialog opens after you add a database, or from "Turn on monitoring" on its page. It first checks what monitoring needs: pg_stat_statements on PostgreSQL, or performance_schema on MySQL, plus permission to read every user's statements. Each check shows "Passed" or "Needs a fix" and re-checks every few seconds. "Here's how to fix it" shows the steps and SQL to copy for your provider, and says if a restart is needed.

Under "Advanced" you can choose:

- "Analyze automatically" - busiest queries first, new ones as they appear.
- "Measure real timings" - runs read-only SELECTs with a time limit (EXPLAIN ANALYZE). Off, RDST reads plans with EXPLAIN only and your queries never run.

Click "Turn on monitoring". Signed out, the dialog asks you to sign in first, because monitoring's AI is free with a Readyset account; after you sign in, monitoring turns on. "Turn on without AI" is there too, but then monitoring only charts the database. If you're not ready, "Finish later" saves the database so you can finish from its page.

**What monitoring collects.** RDST reads your database's query statistics: which queries ran, how often, and how long they took, plus health numbers like connections, cache hit ratio, and rollbacks and deadlocks. It reads every minute, and every 10 seconds for the first few minutes after you turn it on. Detailed readings are kept for a while and then rolled up. "Keep readings" in Settings sets how long (7, 30 or 90 days).

**Turning monitoring off.** Open the database's "Monitoring settings" (gear button) and click "Stop monitoring", or switch off "Monitor this database". RDST asks you to confirm. It stops reading the database and frees one of your AI monitoring spots. Charts keep the history recorded so far.

## A database's page

Click a database in the sidebar. The header shows its name, its state and the engine, and the gear button opens "Monitoring settings". There are two tabs: **Live** and **Report**.

**Live** shows:

- A banner with the cause and a "Fix connection" button if RDST can't reach the database.
- First-run progress, and a pointer to your first plan on Actions.
- "Waiting for queries" if nothing has run yet. Point your app or test suite at the database and queries show up within seconds.
- Health numbers: "Queries per second", "Connections", "Cache hit", "Rollbacks and deadlocks". Click one to chart it.
- A chart of "Queries/s", "Latency" or "Time spent", with ranges from "Last 15 minutes" to "Last 30 days".
- The **Queries** list. Filter by "Status" (All, Fix suggested, Healthy, Analyzing, Stuck), "Tables", or "Search" (press /).

Click a query to open its detail: its numbers, the suggested "Fix", the analysis and the SQL. Buttons include "Full analysis", "Analyze" or "Analyze again", "Estimate fix gain" and "Send to Ask". Press Escape to close it, and j and k to move through the list.

**Monitoring settings** for each database include "Monitor this database", "Analyze automatically", "Measure real timings", "Nightly report" and its hour (default 12 AM), how many queries to analyze a day, and "Check for changes" (every 5 min, 15 min, or hourly; less often uses less AI). An AI panel shows whether AI monitoring is on.

## Analyzing a query

RDST analyzes busy queries on its own when "Analyze automatically" is on. To analyze one yourself:

- Click "Analyze" in a query's detail, or
- Click "Analyze a query" above the Queries list and paste one statement. It takes 10 to 30 seconds.

RDST only analyzes reads and refuses statements that change data or the schema. If the query has placeholders, it asks for values.

"Full analysis" opens the complete result: a plan score, the "Fix" (with "Copy SQL"), "Rewrites tested", the plan, and a "Readyset" card that says whether the query can be cached. Before running EXPLAIN ANALYZE there, RDST asks "Run EXPLAIN ANALYZE?" because it executes your query once. You can tick "Don't ask again".

**Benchmark** above the Queries list (or "Estimate fix gain" on one query) compares each query's current time with the planner's estimate for its fix. Nothing runs on the database.

There's no separate saved-queries page: every query RDST sees or you analyze lives in the database's Queries list.

## Reports

Each monitored database gets a daily report, written at its "Nightly report" hour and filed in the Inbox. Open it on the database's **Report** tab. Switch between "Quick" and "Detailed". Quick covers the day's numbers, "Fix first", "What to change" and "Health". Detailed adds spikes, what changed, fixes confirmed, a settings audit and index changes.

The day button picks an earlier report, and the report menu offers "Print", "Download CSV" and "Copy as Markdown".

## Inbox

The Inbox lists what RDST noticed on your monitored databases: stuck queries, queries that got slower, traffic spikes, plan changes, new heavy queries, a lost connection, changes ready on Actions, fixes confirmed, daily reports, and monitoring problems.

Items are grouped as "Act now" (hurting the database now) and "Watch" (worth a look today). Use the "Open" / "Closed" switch and the database filter. Closed items stay 7 days.

Click an item to see what happened and "What to do", with SQL to copy. The main button opens the right place, like the query. "Resolve" closes it, and closed items can be reopened.

From an item's "..." menu you can mute that kind of alert for the query, the database or all databases, for 1 day, 1 week or "Until I unmute". "Mute rules" lists them with "Unmute".

Keyboard: j and k move, e resolves, o opens.

**Notifications.** The bell next to the Inbox heading opens "Notifications". Turn on "Push to this computer" for desktop notifications, then choose per database: "Act now items", "Fixes ready" and "Daily report". You can also send a "Test notification" and set "Quiet hours". If your system blocks RDST's notifications, a banner offers "Turn on notifications".

## Actions

An action is a change worth making to a database, ranked by how much database time it saves. Kinds are "Indexes", "Server settings", "Maintenance", "Query rewrites" and "Caching". RDST builds an index plan from the analyses of your busiest queries, weighing each index against the writes it costs, and also lists indexes to drop or keep.

Actions are grouped into priority tiers: "Do first" (the most database time per change), "Then", and "Later" (small wins). Use "Group by" to sort by Category, Priority or Database.

Click an action to see what it saves, which queries it speeds up, a "What if" before-and-after, "How to do it", the SQL and the rollback. Buttons: "Copy SQL", "Copy as migration" (Alembic, Prisma, Rails or Flyway) and "Copy rollback". "Copy all SQL" copies every index and maintenance change for one database as one script.

**RDST doesn't apply changes.** You run the SQL yourself, through your normal change process. Then:

- "I did this" marks it applied, and RDST starts measuring.
- "Set aside" hides it until a date, with an optional reason.
- "Dismiss" removes it, with an optional reason. "Bring back" restores set-aside or dismissed actions.

The **Applied** tab shows changes you marked and ones RDST detected, with the time saved and a status: "Verified", "Measuring", "No change yet" or "Undone". "Undo" shows the rollback SQL, then "I undid this".

## Ask

Ask answers questions about your data in plain English. Pick a database with the filter, type a question, and click "Ask" (or press Enter).

RDST reads the schema, writes the SQL, and runs it read-only. If your question is ambiguous, it asks you to choose. The answer shows a short explanation, a results table ("Download CSV"), and "SQL that ran". Buttons include "Analyze query" and "Ask again with today's data". "Earlier" lists your past questions with "Ask again".

Ask is read-only. It only runs SELECT queries, returns 100 rows unless you ask for more (up to 1,000), and stops queries that run too long.

## Activity

Activity is a log of what RDST did on your databases: analyses, change checks, query classification, daily reports, Ask answers, action explanations and benchmarks. Filter by database and event type. "Running now" shows work in progress, each with "Cancel". The "..." menu has "Export CSV", which never includes result rows or sampled values.

## Connecting AI coding agents

Claude Code and other coding agents can read what RDST finds and fix it with you. In Settings, "Use RDST from your coding agent" shows RDST's MCP server address. "Copy Claude Code command" copies the one-time setup command that adds RDST to Claude Code. Other agents connect to the same address.

You can also let your agent add a database for you: see "Connect with Claude" under Adding a database.

## Readyset Platform

The Readyset Platform page shows how Readyset caches queries and serves them from memory, fresh as the data changes, with no code changes. "Start the demo" runs a local demo on your computer using Docker: an orders database, the Readyset cache engine, a router, and a workload. It downloads images first, then runs for up to an hour and removes itself.

While it runs, you can cache queries, "Turn on QueryPilot", and compare the Readyset path with the database direct. "Shut down demo" removes it. Without Docker, the page offers "Get Docker Desktop".

## Settings

- **Account** - shows your email and "Sign out", or "Sign in".
- **AI** - "Hosted by Readyset" uses the included AI. "Use my own key" lets you "Add key" or "Update key" for an Anthropic API key, billed by Anthropic, and "Test key" checks it. "What each key covers" explains which work uses which key.
- **Notifications** - "Push to this computer".
- **Data** - "Keep readings" (7, 30 or 90 days) and "Clear local data", which deletes RDST's local configuration, history, schema descriptions and stored RDST keys. It can't be undone.
- **Databases** and **Use RDST from your coding agent** - covered above.
- **About** - version, app updates, "Docs", "Give feedback" and "Discord".

## AI access and signing in

AI is free with a Readyset account. Signed out, AI pages ask you to "Sign in" or "Use my own Anthropic key instead".

Turning on monitoring asks you to sign in, because its checks, deep analysis and nightly reports use AI. You can turn it on without AI, but then RDST only charts the database and alerts on connection problems; deep analysis and Actions wait until you sign in.

To sign in, choose "Sign in", then "Continue with Google" or enter your email for a sign-in link. Your databases, history and settings carry over.

Quick checks and monitoring alerts always go through Readyset, even with your own Anthropic key, which covers Analyze, Ask, deep analysis and nightly reports. The number of databases that can use AI monitoring is limited per account, and the app shows "AI monitoring on N of M databases". A database without an AI slot can still use local monitoring, which charts it and raises locally measured alerts. To free a slot, turn off monitoring on another database. If your account runs out of AI usage, the app says "You've run out of AI usage for now" with "Contact us".

## Help

- "Take the tour" - the four-step tour.
- "Docs" - setup, monitoring, and what each check means.
- "Keyboard shortcuts" - j/k to move, e to resolve, o to open, / to search, ? for this list, and g then h, i, a or d to jump to a page.
- "Ask or give feedback" - a chat that answers how-to questions and lets you send a feature request, problem or feedback to the team. Nothing is sent until you click "Send to the team".
- "Join Discord" - ask other RDST users and the team.

## Privacy

RDST runs on your computer and connects directly to your database. Your settings, readings, reports and analyses stay on your computer. Database passwords stay in your operating system's keychain.

RDST only reads from your database. Monitoring reads query statistics. Deep analysis runs EXPLAIN, or with "Measure real timings" it runs read-only SELECTs with a time limit.

AI features (analysis, Ask, reports, change checks) send what the AI needs to reason, such as SQL text, query plans, schema details and measured numbers. According to RDST's privacy page, the rows your queries return and your database credentials aren't sent. With your own Anthropic key, Analyze, Ask and reports go straight to Anthropic. Activity logs RDST's AI work. RDST also collects usage statistics, which don't include SQL, host names or database names. For details, see "How your data is handled", linked in the add-database dialog and the tour.

## Troubleshooting

**RDST can't connect to my database.** Click "Fix connection" or "Test connection".

- "The database rejected the password." or "Password needed" - set the password again.
- "The connection timed out." or "The server refused the connection." - check the database is running and this computer can reach its host and port, including any VPN.
- "The server doesn't allow connections from this machine." - allow this computer in the server's access rules. For Supabase, Neon and DigitalOcean, RDST can offer to add your IP to the allowlist.
- "The host name didn't resolve." or "The TLS connection failed." - check those settings.
- SSH errors - use "Test tunnel" and check the jump host, SSH user and key.

Monitoring resumes by itself once the database answers.

**Monitoring isn't collecting.**

- "Query stats are off, so monitoring can't start" - turn on pg_stat_statements (PostgreSQL) or performance_schema (MySQL) using the fix steps shown, then check again.
- "Waiting for queries" - nothing has run yet. Send your app's traffic to the database.
- Only RDST's own queries show up - the user needs permission to read every user's statistics. The checks show the SQL to grant it.
- "Monitoring off" - turn it on from the database's page.

**AI isn't working.**

- "AI off: signed out" or "Sign in to run AI checks" - monitoring's AI needs a Readyset sign-in, even with your own Anthropic key. Sign in from the sidebar.
- "Key rejected by Anthropic" - update the key in Settings.
- "AI reconnecting" - RDST retries every minute.
- "Local monitoring only" - all your AI monitoring slots are in use. Use "Manage databases" to free one.
- "You've run out of AI usage for now" - use "Contact us".
- "The hosted model didn't answer in time" - try again.

**"RDST stopped. Restarting..."** - the app's background service stopped. Pages keep what they showed and update when it's back. If it keeps happening, use "Open logs" in the desktop app, and share what you find through "Ask or give feedback" or Discord.
"""

"""The paste-ready request a user hands their coding agent to connect a database to RDST."""

from __future__ import annotations

from pathlib import Path

from features.agent_connect import discover

DOCS_URL = "https://readyset.io/docs/readyset-ai/rdst/desktop/privacy"

PROMPT_TEMPLATE = """\
RDST is a database monitoring app from Readyset that I installed on this computer. It \
watches the PostgreSQL or MySQL database an app uses and suggests fixes for slow queries. \
It runs locally at {url}. Please connect it to the database I'm working with.

RDST runs on this computer and keeps the connection here. What it stores and sends: \
{docs_url}

Steps:
1. Talk to RDST by sending JSON-RPC requests to its local endpoint with curl. Nothing \
needs installing or registering. For example:
   curl -s "{mcp_url}" -H "Content-Type: application/json" -H "Accept: application/json, \
text/event-stream" -d '{{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": \
{{"name": "rdst_status", "arguments": {{}}}}}}'
   A "tools/list" request shows every tool and its arguments.{wsl_note}
2. Work out which database this project uses, the same way you would to run the app \
locally. Show me what you found (engine, host, port, database name and where it's \
configured) and ask: "Is this the database you want RDST to watch?" If there's more than \
one, list them and ask which; prefer dev or staging over production. If you can't find \
one, ask me: "Which PostgreSQL or MySQL database do you want RDST to watch?"
3. Once I confirm, add it with rdst_add_database and a short name, like the project's \
name. If its settings are in a file RDST can open (not a file inside WSL while RDST runs \
on Windows), pass env_file and env_var so RDST reads them itself.
4. Run rdst_check_prerequisites with that name. If ready_to_monitor is false, show me \
RDST's instructions as it gives them. Offer to run its in-database statements (like \
CREATE EXTENSION) only after I say yes, and don't change server settings or restart a \
database unless I ask. Then check again. Don't turn on monitoring until it reports \
ready_to_monitor: true.
5. Then call rdst_enable_monitoring. The first time, RDST answers waiting_for_user: true \
and shows me its Turn on monitoring screen. Tell me what its message says (read the \
screen, sign in to Readyset if it asks, and click Turn on monitoring in the RDST app), \
then call rdst_wait_for_monitoring, again each time it says not yet, for up to 10 \
minutes, until it reports monitoring: true.
6. {quiet}
7. Tell me the name RDST saved the database under and whether monitoring is on.

Only read the project's folder and ~/.ssh. Don't search my home folder or open \
Desktop, Documents, Downloads, Pictures, Music, Movies or iCloud Drive unless the project is \
in one: my computer asks me about each of those. If you need a file you can't find there, \
like the key for a jump host, ask me where it is.

Keep connection details out of your replies.

Afterwards you can read what RDST finds with rdst_list_inbox, rdst_list_actions, \
rdst_get_index_plan, rdst_top_queries, rdst_query_timeseries and rdst_latest_report. \
After applying a fix, call rdst_action with did_it so RDST updates right away.
"""

FIX_PROMPT_TEMPLATE = """\
RDST is a database monitoring app from Readyset that I installed on this computer. It runs \
locally at {url}. The database {target} is already saved in RDST, but RDST can't monitor \
it yet. Please get it ready.

Steps:
1. Talk to RDST by sending JSON-RPC requests to its local endpoint with curl. Nothing \
needs installing or registering. For example:
   curl -s "{mcp_url}" -H "Content-Type: application/json" -H "Accept: application/json, \
text/event-stream" -d '{{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": \
{{"name": "rdst_check_prerequisites", "arguments": {{"name": "{target}"}}}}}}'
   A "tools/list" request shows every tool and its arguments.{wsl_note}
2. Run rdst_check_prerequisites with name {target} and follow RDST's instructions. Never \
change a production database: tell me what it needs instead. On a development database, \
run each statement it lists in sql (like CREATE EXTENSION) with rdst_run_setup_statement. \
If RDST can't reach the database, find out why. Ask me before you start, restart or move a \
database or change a server setting (like shared_preload_libraries for \
pg_stat_statements), and if I'd rather do it myself, tell me exactly what to change. Then \
check again.
3. Once it's ready_to_monitor, call rdst_enable_monitoring. If it answers waiting_for_user: \
true, RDST is showing me its Turn on monitoring screen: call rdst_wait_for_monitoring \
until it says monitoring is on.
4. {quiet}
5. Tell me in one line where {target} stands.

Only read the project's folder and ~/.ssh. Don't search my home folder or open \
Desktop, Documents, Downloads, Pictures, Music, Movies or iCloud Drive unless the project is \
in one: my computer asks me about each of those. If you need a file you can't find there, \
like the key for a jump host, ask me where it is.

Keep connection details and passwords out of your replies.
"""

ASK_PROMPT_TEMPLATE = """\
RDST is a database monitoring app from Readyset that I installed on this computer. It runs \
locally at {url} and watches my PostgreSQL and MySQL databases, including {target}: it reads \
query statistics every minute, analyzes the slowest queries and suggests fixes (Actions), \
index plans and a daily report. I want to ask you about {target} using what RDST found.

Talk to RDST by sending JSON-RPC requests to its local endpoint with curl. Nothing needs \
installing or registering. For example:
   curl -s "{mcp_url}" -H "Content-Type: application/json" -H "Accept: application/json, \
text/event-stream" -d '{{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": \
{{"name": "rdst_list_actions", "arguments": {{"target": "{target}"}}}}}}'
A "tools/list" request shows every tool. The useful ones: rdst_list_inbox (what needs me), \
rdst_list_actions (suggested fixes), rdst_get_index_plan, rdst_top_queries, \
rdst_query_timeseries and rdst_latest_report.{wsl_note}

Start by telling me in a few lines what needs attention on {target} and why, then answer my \
questions. Don't change the database unless I ask you to; when I do, show me the exact \
change first, never change a production database without my say-so, and after applying a \
fix call rdst_action with did_it so RDST updates. Keep connection details and passwords out \
of your replies.
"""

WSL_NOTE = """
   If {url} isn't reachable (for example you're running inside WSL and RDST runs on \
Windows), use {wsl_url} instead of {url} everywhere in these steps."""


def mcp_url(url: str, attempt: str | None = None) -> str:
    """RDST's MCP endpoint; ``attempt`` names the prompt it came from, for telemetry."""
    return f"{url}/mcp?a={attempt}" if attempt else f"{url}/mcp"


def claude_command(mcp_url: str) -> str:
    """The Claude Code command that registers RDST's MCP server."""
    return f'claude mcp add --transport http rdst "{mcp_url}"'


def render_fix_prompt(url: str, target: str, attempt: str | None = None,
                      wsl_url: str | None = None) -> str:
    """The prompt that has the user's own agent get ``target``, already in RDST, ready to
    monitor."""
    endpoint = mcp_url(url, attempt)
    wsl_note = WSL_NOTE.format(url=url, wsl_url=wsl_url) if wsl_url else ""
    return FIX_PROMPT_TEMPLATE.format(url=url, mcp_url=endpoint, target=target,
                                      quiet=QUIET_DATABASE,
                                      wsl_note=wsl_note)


def render_ask_prompt(url: str, target: str, attempt: str | None = None,
                      wsl_url: str | None = None) -> str:
    """The prompt that has the user's own agent answer questions about ``target`` from what
    RDST found."""
    endpoint = mcp_url(url, attempt)
    wsl_note = WSL_NOTE.format(url=url, wsl_url=wsl_url) if wsl_url else ""
    return ASK_PROMPT_TEMPLATE.format(url=url, mcp_url=endpoint, target=target,
                                      wsl_note=wsl_note)


def render_prompt(url: str, attempt: str | None = None, wsl_url: str | None = None) -> str:
    """The prompt for ``url``, carrying the ``attempt`` in its MCP URL."""
    endpoint = mcp_url(url, attempt)
    wsl_note = WSL_NOTE.format(url=url, wsl_url=wsl_url) if wsl_url else ""
    return PROMPT_TEMPLATE.format(docs_url=DOCS_URL, url=url, mcp_url=endpoint,
                                  quiet=QUIET_DATABASE,
                                  wsl_note=wsl_note)


# A database nothing runs against gives RDST nothing to show: the user's own conversation with
# their agent offers it a workload.
QUIET_DATABASE = """\
Give RDST a minute or two, then call rdst_top_queries for it. If it still has no queries, \
nothing is running against this database yet, so RDST has nothing to analyze. Unless it's \
production, ask me whether you should run some queries against it: read-only SELECTs you \
write from its schema, like the lookups, joins and filters an app on it would run. If I say \
yes, run each a few times with the database's own client (psql or mysql, through docker \
exec when it's in a container), then call rdst_top_queries again to show me RDST picked \
them up. Never write to it."""

DISCOVER_PROMPT = """\
RDST is a database monitoring app from Readyset that I installed on this computer. It \
watches PostgreSQL and MySQL databases and suggests fixes for slow queries. Its tools are \
connected to this session as the rdst MCP server.

Find the databases I've been working with, so I can pick which ones RDST should watch. Only \
read: don't change files, start apps or connect to databases. I'm watching RDST show what you \
do, so before each step say in a few words what you're looking at. Work quickly: aim to \
finish in about three minutes, and send the clearest databases first.

{start}2. For each project folder (at most 12, most recent first), work out the PostgreSQL or \
MySQL databases it uses, the way you would to run it locally: .env files, docker-compose \
files, settings modules, ORM configs, Prisma schemas and the like. Look at the top of the \
project first and only go deeper where those point. Stay inside the project folders and any \
place I named: never search a whole drive or home folder, and skip node_modules, .git, \
virtualenvs, build output and data folders.
3. Name the provider from the host: supabase.co or supabase.com is supabase, neon.tech is \
neon, ondigitalocean.com is digitalocean, rds.amazonaws.com is aws, localhost or 127.0.0.1 \
is local. For a database in a Docker container, use localhost and the port it publishes, \
provider docker, and name the container in note; leave the port out if you can't tell.
4. Call rdst_propose_databases as soon as you find each database, with just the new ones: \
RDST shows them to me as they arrive. Most recently used first. Never pass passwords or \
whole connection URLs. When a URL is in a .env-style file, pass env_file (its absolute \
path) and env_var so RDST reads it itself; when only the password is in one, pass host, \
port, database and user with password_file and password_var. Use note for anything that \
helps me recognize it, in a few words. Skip SQLite and other engines. Mark a production \
database's environment as production.
5. Then reply with one short line saying what you found.
"""

LIST_SESSIONS = """\
1. List my recent coding agent sessions from the last 60 days, newest first. Claude Code \
keeps them in ~/.claude/projects/<folder>/<session>.jsonl and Codex in \
~/.codex/sessions/<year>/<month>/<day>/*.jsonl. Their JSON lines carry a cwd field: the \
project each session ran in. Read only the first lines of each, for cwd.{also_note}
"""

ALSO_NOTE = " My sessions from the other side of this computer are in {folders} as well."

KNOWN_PROJECTS = """\
1. RDST already read my recent sessions. These are the projects they ran in, most recent \
first; don't read the sessions again:
{projects}{share_note}
"""

SHARE_NOTE = {
    "claude": """
   Shells can't open the \\\\wsl.localhost folders: use your Read, Glob and Grep tools on \
them.""",
    "codex": """
   Read the \\\\wsl.localhost folders as files, or list and search them inside WSL with \
wsl.exe as described at the end.""",
}

HINT = """\
I told RDST where to look: "{hint}". Start there, and give it priority.

"""

HINT_LIMIT = 300


KNOWN_NOTE = """\
RDST already found these, so don't propose them again. Look for the ones it missed: a \
database a project reaches through a secret manager (like Infisical or Doppler), one its \
infrastructure files declare, or one whose URL the code builds from parts.
{known}

"""


TRIAGE_PROMPT = """\
RDST: find your database

RDST is a database monitoring app from Readyset that I installed on this computer. It \
watches PostgreSQL and MySQL databases and suggests fixes for slow queries. Its tools are \
connected to this session as the rdst MCP server.

RDST already looked through this computer (my recent projects, my agent and shell history, \
Docker, open ports and saved connections) and checked which databases answer. Here's what it \
found, one numbered line each: name; engine; where it runs; whether it answers from here; \
which project; what pointed at it; and what my latest coding session in that project was \
about, when RDST could tell.
{found}

RDST lists them best first by what it could check itself: online, a password on disk, a \
recent project. Keep that order unless you find a clear reason not to, and leave out the \
ones marked already in RDST, since RDST shows those itself.

Help me pick the one RDST should watch. Work fast: aim to finish in about a minute. Only \
read, with Read, Grep and Glob: there's no shell here, and don't change files, start \
anything, or connect to databases yourself. Use the files and paths listed above. Say in a few \
words what you're checking as you go, since RDST shows it to me.

1. For each project that has more than one candidate, or where it isn't clear which database \
the app really uses, look at that project's own config (its .env, compose file, settings or \
ORM config) to tell the app's database from leftovers. Only open the project folders listed \
above; don't search my home folder or open Desktop, Documents, Downloads, Pictures, Music, \
Movies or iCloud Drive, because my computer asks me about each of those.{protected}
2. Decide every numbered line: propose it, or leave it out with a reason. Leave out what \
isn't worth watching: test and benchmark databases, examples, copies of the same database, \
anything that doesn't answer unless a project clearly depends on it (then propose it and \
say it needs a jump host or an allowlist), and folders that aren't my own projects, like a \
repository I only asked an agent to look at, install or build. Judge that from what my \
sessions there were about and what the folder is, not from its location alone. A line you \
neither propose nor leave out still shows me, so go through all of them.
3. Call rdst_propose_databases once. In databases, the ones to watch, best first: the \
database each project's app uses for development first. Pass each one's found (its line \
number), name (a short name, like the project's), engine, host, port, database, user, \
provider, environment and project, and where its password is: env_file and env_var for a \
postgres:// or mysql:// URL in a .env-style file (never an https address like \
SUPABASE_URL), or password_file and password_var. Never pass a password or a whole URL. \
Give each a note: one short line that tells me what it is, like "shop's local database, \
from its compose file". In skip, each line you leave out: its found number and the reason \
in a few words.
4. Finish with one line saying what you proposed and what you left out.
"""


def _found_line(line: int, item: dict, requests: dict[str, str] | None = None) -> str:
    where = item.get("host") or item.get("instance") or "unknown"
    if item.get("port"):
        where += f":{item['port']}"
    if item.get("database"):
        where += f"/{item['database']}"
    reach = {"answering": "answers", "unreachable": "doesn't answer from here"}.get(
        item.get("reach") or "", "not checked")
    if item.get("deferred"):
        reach = "not read yet (in a folder my computer asks about)"
    if item.get("saved_as"):
        reach += f", already in RDST as {item['saved_as']}"
    if item.get("wsl"):
        where += " (in WSL)"
    project = item.get("project") or "no project"
    guarded = discover.protected_folder(item.get("project"))
    if guarded:
        # The agent never gets a guarded folder's full path, so it can't open it by mistake;
        # where it sits inside that folder helps tell a project from a scratch folder.
        inside = str(item.get("project")).split(f"/{guarded}/", 1)[-1] \
            if f"/{guarded}/" in str(item.get("project")) else ""
        project = f"a project in {guarded}{f' ({inside})' if inside else ''}, not read yet"
    pointers = "; ".join(pointer for pointer in (item.get("found_by") or [])[:3]
                         if not guarded or str(Path.home()) not in pointer)
    asked = (requests or {}).get(str(item.get("project") or ""))
    about = f'; my latest session there: "{asked}"' if asked else ""
    return (f"{line}. {item.get('name') or 'database'}; "
            f"{item.get('engine') or 'unknown engine'}; {where}; {reach}; {project}; "
            f"{pointers}")[:400] + about


def render_triage_prompt(found: list[dict], requests: dict[str, str] | None = None) -> str:
    """The prompt that has the agent make sense of what RDST's scan found and propose the
    short list the user picks from. ``requests`` says, by project folder, what the user's
    latest session there was about."""
    protected = any(item.get("deferred") or discover.protected_folder(item.get("project"))
                    for item in found)
    return TRIAGE_PROMPT.format(
        found="\n".join(_found_line(line, item, requests)
                        for line, item in enumerate(found[:60])) or "- nothing",
        protected=(" A project marked not read yet is in one of those folders: don't open "
                   "it or look for it, leave it for me to pick, and judge it from what RDST "
                   "found.") if protected else "")


def render_describe_prompt(text: str) -> str:
    """The prompt that has the agent find and set up a database the user described."""
    described = " ".join(text.split())[:HINT_LIMIT].replace('"', "'")
    return SETUP_PROMPT.format(
        opening="I want RDST to watch a database I'll describe. Find it and set it up in RDST",
        databases=f'- I described it as: "{described}". Work out which database that is from '
                  "my recent projects, ~/.ssh/config and how my sessions connected to it. If "
                  "more than one fits, ask me which with rdst_ask_user. Save it under a short "
                  "name, like its project's.")


def render_discover_prompt(also: list[str] | None = None, projects: list[str] | None = None,
                           hint: str | None = None, agent: str = "claude",
                           known: list[str] | None = None) -> str:
    """The prompt RDST runs an agent on to find the user's databases. ``projects`` lists the
    folders the user's recent sessions ran in; without them the agent lists its sessions
    itself, and ``also`` names more home folders whose .claude and .codex sessions count
    (Windows and WSL share a computer). ``hint`` is what the user said to look for, and
    ``agent`` which agent reads the prompt."""
    if projects:
        share_note = SHARE_NOTE[agent] if any(p.startswith("\\\\wsl") for p in projects) \
            else ""
        start = KNOWN_PROJECTS.format(projects="\n".join(f"   - {path}" for path in projects),
                                      share_note=share_note)
    else:
        note = ALSO_NOTE.format(folders=" and ".join(also)) if also else ""
        start = LIST_SESSIONS.format(also_note=note)
    prompt = DISCOVER_PROMPT.format(start=start)
    if known:
        head, sep, steps = prompt.partition("\n1. ")
        listed = "\n".join(f"   - {line}" for line in known[:40])
        prompt = f"{head}\n{KNOWN_NOTE.format(known=listed)}{sep.lstrip()}{steps}"
    hint = " ".join((hint or "").split())[:HINT_LIMIT].replace('"', "'")
    if hint:
        head, sep, steps = prompt.partition("\n1. ")
        prompt = f"{head}\n{HINT.format(hint=hint)}{sep.lstrip()}{steps}"
    return prompt


SETUP_PROMPT = """\
{opening}, using the rdst MCP tools. I'm watching RDST show what you do, so before each step \
say in a few words what you're doing.

{databases}

For each one:
1. Work out anything missing, like the password or the port on this computer, from where you \
found it: the project's files, docker-compose, or `docker port` and `docker inspect` for a \
container. Never put a password in your replies. Never start, create or move a database on \
your own, and never set one up on a different port. If it isn't running, or RDST can't \
reach it, find out why, then ask me with rdst_ask_user whether to start it the way the \
project does (like docker compose up), on its usual port. A database in a private network, like an \
RDS instance only a bastion can reach, needs its SSH jump host: find it in ~/.ssh/config, \
the project's scripts or docs, or how my sessions connected to it. Only read the project's \
folder and ~/.ssh: don't search my home folder or open Desktop, Documents, Downloads, \
Pictures, Music, Movies or iCloud Drive unless the project is in one, because my computer \
asks me about each of those. If you need a file you can't find there, like the key for a \
jump host, ask me where it is with rdst_ask_user.
2. Add it with rdst_add_database under the name given. When its URL or password is in a \
.env-style file, pass env_file and env_var (or password_file and password_var) so RDST reads \
it; otherwise pass the fields, password included. RDST saves it only once it connects, and \
reaches a Supabase project through its pooler by itself, so never guess a host, region or \
pooler: if it can't connect, fix what its reply says, and pass replace: true to correct a \
database you already added. Pass ssh for a jump host: a Host alias from ~/.ssh/config as profile, \
or host, user, port and key_path. I've already picked the database, so don't ask \
which one. If you need me to decide how to reach or use it, like which jump host or which \
of two logins, call rdst_ask_user with the question and 2 to 4 likely answers, then stop: \
my pick comes back as your next message.
3. Run rdst_check_prerequisites. If ready_to_monitor is false, follow RDST's instructions. \
Never change a production database: say in one line what it needs instead. On a development \
database, run each statement it lists in sql (like CREATE EXTENSION) with \
rdst_run_setup_statement. When a fix needs a server setting or a restart (like \
shared_preload_libraries for pg_stat_statements), ask me first with rdst_ask_user, saying \
what you'd change and that the database restarts, with choices like "Yes, apply and \
restart" and "I'll do it myself". If I say yes, apply it the way this database is run (its \
Compose file or container settings, ALTER SYSTEM, or a managed provider's own CLI if I'm \
signed in to it), restart it, and wait until it answers again. If I'd rather do it myself, \
tell me exactly what to change and stop. Then check again.
4. Once it's ready_to_monitor, call rdst_enable_monitoring. If it answers \
waiting_for_user: true, I'm looking at RDST's Turn on monitoring screen: call \
rdst_wait_for_monitoring, again each time it says not yet, for up to 10 minutes, without \
commenting in between. Then go on.
5. Finish with one short line per database saying where it stands.
"""


def render_setup_prompt(picked: list[tuple[str, dict | None]],
                        problem: str | None = None) -> str:
    """The prompt that resumes a search's session to set up the databases the user picked,
    each with the name RDST will save it under; ``None`` marks one already in RDST."""
    lines = []
    for name, found in picked:
        if found is None:
            lines.append(f"- Already in RDST as {name}: skip adding it")
            continue
        where = ", ".join(f"{key} {found[key]}" for key in (
            "engine", "host", "port", "database", "user", "provider", "environment", "project",
            "source", "env_file", "env_var", "password_file", "password_var", "url_file",
            "instance", "note")
            if found.get(key) not in (None, ""))
        if found.get("secrets"):
            where += f", its secrets are in {found['secrets']} (use its CLI if I'm signed in)"
        if found.get("incomplete"):
            where += ", RDST couldn't find where it runs: work that out from the project"
        if found.get("found_by"):
            where += f". RDST found it from: {'; '.join(found['found_by'][:4])}"
        lines.append(f"- Save as {name}: {where}")
    one = len(picked) == 1
    if all(found is None for _, found in picked):
        names = ", ".join(name for name, _ in picked)
        opening = (f"{names} {'is' if one else 'are'} in RDST but not ready to monitor yet. "
                   f"Get {'it' if one else 'them'} ready now")
    else:
        opening = (f"I picked {'this database' if one else 'these databases'} from what you "
                   f"found. Set {'it' if one else 'them'} up in RDST now")
    if problem:
        # RDST tried the connection it found and it failed: the agent starts from why.
        lines.append(f"RDST already tried to connect with what it found and got: "
                     f"{' '.join(problem.split())[:300]}. Work out why and fix it, or ask me.")
    names = ", ".join(name for name, _ in picked)
    return f"RDST: set up {names}\n\n" + SETUP_PROMPT.format( opening=opening,
                                                             databases="\n".join(lines))

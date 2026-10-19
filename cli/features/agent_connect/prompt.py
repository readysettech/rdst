"""The paste-ready request a user hands their coding agent to connect a database to RDST."""

from __future__ import annotations

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
and shows me its Turn on monitoring screen. Tell me to read it and click Turn on \
monitoring in the RDST app, then check rdst_status every 10 seconds or so, for up to 10 \
minutes, until the database shows monitoring: true.
6. Tell me the name RDST saved the database under and whether monitoring is on.

Keep connection details out of your replies.

Afterwards you can read what RDST finds with rdst_list_inbox, rdst_list_actions, \
rdst_get_index_plan, rdst_top_queries, rdst_query_timeseries and rdst_latest_report. \
After applying a fix, call rdst_action with did_it so RDST updates right away.
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


def render_prompt(url: str, attempt: str | None = None, wsl_url: str | None = None) -> str:
    """The prompt for ``url``, carrying the ``attempt`` in its MCP URL."""
    endpoint = mcp_url(url, attempt)
    wsl_note = WSL_NOTE.format(url=url, wsl_url=wsl_url) if wsl_url else ""
    return PROMPT_TEMPLATE.format(docs_url=DOCS_URL, url=url, mcp_url=endpoint,
                                  wsl_note=wsl_note)

"""Provider-specific fixes for failed monitoring prerequisite checks.

Each fix names the SQL to run (copyable, one statement per entry) and the console
or CLI steps for the provider RDST detected. Steps follow each provider's
current documentation (links in `docs`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from features.configure.prerequisites import Context

PROVIDER_NAMES = {
    "self_hosted": "Self-hosted",
    "rds": "Amazon RDS",
    "aurora": "Amazon Aurora",
    "cloudsql": "Google Cloud SQL",
    "azure": "Azure Database",
    "supabase": "Supabase",
    "neon": "Neon",
    "digitalocean": "DigitalOcean",
}

DOCS = {
    ("postgresql", "rds"): "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PerfInsights.UsingDashboard.AnalyzeDBLoad.AdditionalMetrics.PostgreSQL.html",
    ("postgresql", "aurora"): "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/USER_PerfInsights.UsingDashboard.AnalyzeDBLoad.AdditionalMetrics.PostgreSQL.html",
    ("postgresql", "cloudsql"): "https://cloud.google.com/sql/docs/postgres/extensions",
    ("postgresql", "azure"): "https://learn.microsoft.com/en-us/azure/postgresql/extensions/how-to-allow-extensions",
    ("postgresql", "supabase"): "https://supabase.com/docs/guides/database/extensions/pg_stat_statements",
    ("postgresql", "neon"): "https://neon.com/docs/extensions/pg_stat_statements",
    ("postgresql", "digitalocean"): "https://docs.digitalocean.com/products/databases/postgresql/details/supported-extensions/",
    ("postgresql", "self_hosted"): "https://www.postgresql.org/docs/current/pgstatstatements.html",
    ("mysql", "rds"): "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PerfInsights.EnableMySQL.html",
    ("mysql", "aurora"): "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/USER_PerfInsights.EnableMySQL.html",
    ("mysql", "cloudsql"): "https://cloud.google.com/sql/docs/mysql/flags",
    ("mysql", "azure"): "https://learn.microsoft.com/en-us/azure/mysql/flexible-server/concepts-server-parameters",
    ("mysql", "digitalocean"): "https://docs.digitalocean.com/products/databases/mysql/how-to/modify-user-privileges/",
    ("mysql", "self_hosted"): "https://dev.mysql.com/doc/refman/8.4/en/performance-schema-startup-configuration.html",
}


@dataclass
class Fix:
    summary: str
    sql: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    # Steps that follow the SQL and commands (for example "restart").
    after: list[str] = field(default_factory=list)
    restart: bool = False
    note: Optional[str] = None
    docs_url: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def plain(self) -> str:
        parts = [self.summary, *self.steps, *self.sql, *self.commands, *self.after]
        return " ".join(part for part in parts if part)


def provider_name(provider: Optional[str], engine: str) -> Optional[str]:
    if not provider:
        return None
    if provider == "self_hosted":
        return "Self-hosted PostgreSQL" if engine == "postgresql" else "Self-hosted MySQL"
    if provider == "azure":
        return "Azure Database for " + ("PostgreSQL" if engine == "postgresql" else "MySQL")
    return PROVIDER_NAMES.get(provider, provider)


def _pg_ident(name: str) -> str:
    if name.replace("_", "").isalnum() and name == name.lower() and not name[:1].isdigit():
        return name
    return '"' + name.replace('"', '""') + '"'


def _mysql_user(name: str) -> str:
    return "'" + name.replace("'", "''") + "'@'%'"


def fix_for(check_id: str, ctx: "Context") -> Optional[Fix]:
    builder = (_POSTGRES if ctx.engine == "postgresql" else _MYSQL).get(check_id)
    if builder is None:
        return None
    fix = builder(ctx)
    if fix is not None and fix.docs_url is None:
        fix.docs_url = DOCS.get((ctx.engine, ctx.provider)) or DOCS.get((ctx.engine, "self_hosted"))
    return fix


# PostgreSQL ----------------------------------------------------------------


def _pg_preload(ctx: "Context") -> Fix:
    p = ctx.provider
    if p in ("rds", "aurora"):
        default_from = "11" if p == "rds" else "10"
        return Fix(
            summary=f"{PROVIDER_NAMES[p]} preloads pg_stat_statements on PostgreSQL "
            f"{default_from} and later. If it was removed, add it back in the parameter group.",
            steps=[
                "In the RDS console, open Parameter groups and edit the custom DB parameter "
                "group attached to this database (the default group can't be edited).",
                "Add pg_stat_statements to shared_preload_libraries, keeping any libraries "
                "already listed, and save.",
                "Reboot the instance" + (" (the writer)" if p == "aurora" else "")
                + " to apply it.",
            ],
            restart=True,
        )
    if p == "azure":
        return Fix(
            summary="Azure preloads pg_stat_statements on every flexible server. If it was "
            "removed, add it back in Server parameters.",
            steps=[
                "In the Azure portal, open the server, then Settings > Server parameters.",
                "Find shared_preload_libraries, tick PG_STAT_STATEMENTS and save.",
                "Restart the server from its Overview page.",
            ],
            restart=True,
        )
    if p in ("cloudsql", "supabase", "neon", "digitalocean"):
        return Fix(
            summary=f"{PROVIDER_NAMES[p]} loads pg_stat_statements for you, so there's no "
            "setting to change. Create the extension below; if it still reports this, "
            f"contact {PROVIDER_NAMES[p]} support.",
        )
    current = str(ctx.extras.get("preload") or "").strip()
    libraries = ", ".join(part for part in [current, "pg_stat_statements"] if part)
    return Fix(
        summary="Add pg_stat_statements to shared_preload_libraries, then restart PostgreSQL.",
        sql=[f"ALTER SYSTEM SET shared_preload_libraries = '{libraries}';"],
        after=[
            "Or set shared_preload_libraries in postgresql.conf, keeping any libraries "
            "already listed.",
            "Restart PostgreSQL. A reload isn't enough for this setting.",
        ],
        restart=True,
    )


def _pg_create(ctx: "Context") -> Fix:
    p = ctx.provider
    db = ctx.database
    create = "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
    if p == "supabase":
        return Fix(
            summary="Turn on pg_stat_statements in the extensions schema.",
            sql=["create extension if not exists pg_stat_statements with schema extensions;"],
            steps=["Or in the Supabase dashboard, open Database > Extensions and turn on "
                   "pg_stat_statements."],
        )
    if p == "azure":
        return Fix(
            summary="Allow the extension on the server, then create it.",
            steps=[
                "In the Azure portal, open the server, then Settings > Server parameters.",
                "Find azure.extensions, tick PG_STAT_STATEMENTS and save. No restart is needed.",
                f"Connect to {db} as the server admin and run the SQL below.",
            ],
            sql=[create],
        )
    who = {
        "rds": "the master user (a member of rds_superuser)",
        "aurora": "the master user (a member of rds_superuser)",
        "cloudsql": "the postgres user or another member of cloudsqlsuperuser",
        "neon": "the role Neon created for you (a member of neon_superuser)",
        "digitalocean": "doadmin",
    }.get(p, "a superuser")
    note = None
    if p == "neon":
        note = ("Neon clears these statistics whenever the compute suspends, so idle projects "
                "start over after scale to zero.")
    elif p == "digitalocean":
        note = "Create it in every database you want RDST to monitor."
    elif p in ("rds", "aurora"):
        note = ("If rds.allowed_extensions is set in the parameter group, it must include "
                "pg_stat_statements.")
    steps = [f"Connect to {db} as {who} and run the SQL below."]
    if p == "neon":
        steps = [f"In the Neon console, open SQL Editor, choose the {db} database and run "
                 "the SQL below."]
    if p == "self_hosted" and not ctx.extras.get("available", True):
        steps.insert(0, "Install the PostgreSQL contrib package for your version "
                        "(postgresql-contrib on Debian and Ubuntu).")
    return Fix(summary=f"Create pg_stat_statements in {db}.", sql=[create], steps=steps, note=note)


def _pg_grant(ctx: "Context") -> Fix:
    p = ctx.provider
    user = _pg_ident(ctx.user)
    # Neon, Cloud SQL and Azure admins can grant pg_monitor (which includes
    # pg_read_all_stats); DigitalOcean's doadmin can grant only pg_read_all_stats.
    role = "pg_monitor" if p in ("neon", "cloudsql", "azure") else "pg_read_all_stats"
    who = {
        "rds": "the master user",
        "aurora": "the master user",
        "cloudsql": "the postgres user",
        "azure": "the server admin",
        "supabase": "postgres (the SQL editor works)",
        "neon": "the role Neon created for you",
        "digitalocean": "doadmin",
    }.get(p, "a superuser")
    return Fix(
        summary=f"Let {ctx.user} read every user's statements and activity.",
        sql=[f"GRANT {role} TO {user};"],
        steps=[f"Run as {who}. It takes effect on RDST's next check, no restart."],
    )


# A new instance uses a default.* parameter group, which can't be edited.
_RDS_PARAMETER_GROUP = [
    "In the RDS console, open the DB parameter group attached to this database.",
    "If it's a default group (named default.<engine>), create a parameter group for the "
    "same engine version, then attach it to the database with Modify.",
]


def _pg_query_size(ctx: "Context") -> Fix:
    p = ctx.provider
    if p in ("rds", "aurora"):
        return Fix(
            summary="Raise track_activity_query_size in the DB parameter group.",
            steps=[
                *_RDS_PARAMETER_GROUP,
                "Set track_activity_query_size to 4096 and save.",
                "Reboot to apply it. The parameter is static.",
            ],
            restart=True,
        )
    if p == "cloudsql":
        return Fix(
            summary="Set the track_activity_query_size flag to 4096.",
            steps=[
                "In the Google Cloud console, edit the instance, open Flags and add "
                "track_activity_query_size = 4096. Or run the command below.",
                "--database-flags replaces every flag already set, so include those too.",
                "Changing this flag restarts the instance.",
            ],
            commands=["gcloud sql instances patch <instance> "
                      "--database-flags=track_activity_query_size=4096"],
            restart=True,
        )
    if p == "azure":
        return Fix(
            summary="Raise track_activity_query_size in Server parameters.",
            steps=[
                "In the Azure portal, open the server, then Settings > Server parameters.",
                "Set track_activity_query_size to 4096 and save.",
                "Restart the server from its Overview page.",
            ],
            restart=True,
        )
    if p == "supabase":
        return Fix(
            summary="Raise track_activity_query_size with the Supabase CLI. It restarts "
            "the database.",
            commands=["supabase postgres-config update --config track_activity_query_size=4096 "
                      "--project-ref <project-ref> --experimental"],
            restart=True,
        )
    if p in ("neon", "digitalocean"):
        return Fix(
            summary=f"{PROVIDER_NAMES[p]} doesn't let you change this setting. RDST still "
            "reads full statement text from pg_stat_statements; only the running-queries "
            "view is cut off.",
        )
    return Fix(
        summary="Raise track_activity_query_size, then restart PostgreSQL.",
        sql=["ALTER SYSTEM SET track_activity_query_size = 4096;"],
        after=["Restart PostgreSQL. A reload isn't enough for this setting."],
        restart=True,
        docs_url="https://www.postgresql.org/docs/current/runtime-config-statistics.html",
    )


def _pg_track(ctx: "Context") -> Fix:
    p = ctx.provider
    if p in ("rds", "aurora"):
        steps = ["In the custom DB parameter group, set pg_stat_statements.track to top "
                 "and save. It applies without a reboot."]
        return Fix(summary="Turn statement tracking back on.", steps=steps)
    if p == "cloudsql":
        return Fix(
            summary="Turn statement tracking back on.",
            steps=["Set the pg_stat_statements.track flag to top. It doesn't restart the "
                   "instance."],
        )
    if p == "azure":
        return Fix(
            summary="Turn statement tracking back on.",
            steps=["In Server parameters, set pg_stat_statements.track to TOP and save."],
        )
    if p in ("supabase", "neon", "digitalocean"):
        return Fix(summary=f"{PROVIDER_NAMES[p]} manages statement tracking. Contact "
                           f"{PROVIDER_NAMES[p]} support if statements aren't recorded.")
    return Fix(
        summary="Turn statement tracking back on and reload the configuration.",
        sql=["ALTER SYSTEM SET pg_stat_statements.track = 'top';",
             "SELECT pg_reload_conf();"],
    )


_POSTGRES = {
    "pg_stat_statements_preload": _pg_preload,
    "pg_stat_statements": _pg_create,
    "read_all_stats": _pg_grant,
    "query_text_size": _pg_query_size,
    "pg_stat_statements_track": _pg_track,
}


# MySQL ---------------------------------------------------------------------


def _my_performance_schema(ctx: "Context") -> Fix:
    p = ctx.provider
    if p in ("rds", "aurora"):
        return Fix(
            summary="Turn on performance_schema in the DB parameter group.",
            steps=[
                *_RDS_PARAMETER_GROUP,
                "Set performance_schema to 1 and save.",
                "Reboot to apply it. Turning on Performance Insights without a reboot "
                "doesn't turn performance_schema on.",
            ],
            restart=True,
        )
    if p == "cloudsql":
        return Fix(
            summary="Set the performance_schema flag to on. It restarts the instance.",
            steps=["--database-flags replaces every flag already set, so include those too."],
            commands=["gcloud sql instances patch <instance> "
                      "--database-flags=performance_schema=on"],
            restart=True,
        )
    if p == "azure":
        return Fix(
            summary="Turn on performance_schema in Server parameters.",
            steps=[
                "In the Azure portal, open the server, then Settings > Server parameters.",
                "Set performance_schema to ON and save.",
                "Restart the server from its Overview page.",
            ],
            restart=True,
        )
    if p == "digitalocean":
        return Fix(summary="DigitalOcean manages performance_schema. Contact DigitalOcean "
                           "support to turn it on.")
    return Fix(
        summary="Turn on performance_schema, then restart MySQL.",
        steps=["In my.cnf under [mysqld], set performance_schema=ON.", "Restart MySQL."],
        restart=True,
    )


def _my_grant(ctx: "Context") -> Fix:
    who = {
        "rds": "the master user",
        "aurora": "the master user",
        "cloudsql": "root or another cloudsqlsuperuser member",
        "azure": "the server admin",
        "digitalocean": "doadmin",
    }.get(ctx.provider, "an admin user")
    return Fix(
        summary=f"Let {ctx.user} read performance_schema.",
        sql=[f"GRANT SELECT ON performance_schema.* TO {_mysql_user(ctx.user)};"],
        steps=[f"Run as {who}. If {ctx.user} connects from a specific host, use that host "
               "instead of '%'."],
    )


def _consumer_fix(ctx: "Context", names: list[str], summary: str) -> Fix:
    p = ctx.provider
    in_list = ", ".join(f"'{name}'" for name in names)
    runtime = ("UPDATE performance_schema.setup_consumers SET ENABLED = 'YES' "
               f"WHERE NAME IN ({in_list});")
    if p == "digitalocean":
        return Fix(summary=summary + " DigitalOcean doesn't allow changes to performance_schema "
                                     "settings. Contact DigitalOcean support.")
    if p in ("rds", "aurora"):
        params = ", ".join(f"performance_schema_consumer_{name}" for name in names)
        return Fix(
            summary=summary,
            sql=[runtime],
            after=[
                "The SQL applies now but resets when the database restarts.",
                f"To keep it, set {params} to 1 in the custom DB parameter group.",
            ],
        )
    if p == "cloudsql":
        return Fix(
            summary=summary,
            sql=[runtime],
            after=["Cloud SQL has no flags for consumers, so run this again after each restart."],
        )
    if p == "azure":
        return Fix(
            summary=summary,
            sql=[runtime],
            after=["The SQL applies now but resets when the server restarts."],
        )
    options = [f"performance-schema-consumer-{name.replace('_', '-')}=ON" for name in names]
    return Fix(
        summary=summary,
        sql=[runtime],
        after=["The SQL applies now but resets when MySQL restarts. To keep it, add these "
               "lines to my.cnf under [mysqld]:", *options],
    )


def _my_consumers(ctx: "Context") -> Fix:
    names = list(ctx.extras.get("consumers_off") or ["global_instrumentation", "statements_digest"])
    return _consumer_fix(ctx, names, "Turn on statement digest collection.")


def _my_history(ctx: "Context") -> Fix:
    names = list(ctx.extras.get("sample_consumers_off") or ["events_statements_history_long"])
    return _consumer_fix(ctx, names, "Keep recent statements for deep analysis.")


def _my_query_size(ctx: "Context") -> Fix:
    p = ctx.provider
    if p in ("rds", "aurora"):
        steps = ["In the custom DB parameter group, set performance_schema_max_digest_length "
                 "and max_digest_length to 4096, then reboot."]
    elif p == "cloudsql":
        steps = ["Set the performance_schema_max_digest_length and max_digest_length flags to "
                 "4096. Changing them restarts the instance."]
    elif p == "azure":
        steps = ["In Server parameters, set performance_schema_max_digest_length and "
                 "max_digest_length to 4096, then restart."]
    elif p == "digitalocean":
        return Fix(summary="DigitalOcean manages this setting. Statements longer than 1024 "
                           "characters are cut off in the digest.")
    else:
        steps = ["In my.cnf under [mysqld], set performance_schema_max_digest_length=4096 and "
                 "max_digest_length=4096.", "Restart MySQL."]
    return Fix(summary="Raise the digest length so long statements are kept whole.",
               steps=steps, restart=True)


def _my_instruments(ctx: "Context") -> Fix:
    return Fix(
        summary="Turn on statement instruments and make sure RDST's sessions are instrumented.",
        sql=[
            "UPDATE performance_schema.setup_instruments SET ENABLED = 'YES', TIMED = 'YES' "
            "WHERE NAME LIKE 'statement/%';",
            "SELECT * FROM performance_schema.setup_actors;",
        ],
        after=["setup_actors needs a row matching every user (HOST '%', USER '%') with "
               "ENABLED = 'YES'."],
    )


_MYSQL = {
    "performance_schema": _my_performance_schema,
    "read_all_stats": _my_grant,
    "statement_consumers": _my_consumers,
    "statement_history": _my_history,
    "query_text_size": _my_query_size,
    "statement_instruments": _my_instruments,
}

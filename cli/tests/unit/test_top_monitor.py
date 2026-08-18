"""The realtime monitor's activity polls exclude RDST's own sessions.

The monitor carries its own copy of the pg_stat_activity / processlist
SQL; these tests pin the self-session exclusion predicates and keep the
MySQL variant textually agreeing with the command-set copy.
"""

from features.top.command_sets import TOP_COMMAND_SETS
from features.top.monitor import ActivityQueryCollector

MYSQL_SELF_SESSION_EXCLUSION = """
    ID NOT IN (
        SELECT PROCESSLIST_ID
        FROM performance_schema.session_account_connect_attrs
        WHERE ATTR_NAME = 'program_name'
          AND ATTR_VALUE LIKE 'rdst/%'
    )
"""


def _normalized(sql: str) -> str:
    return " ".join(sql.split())


def test_pg_monitor_query_excludes_rdst_sessions():
    sql = ActivityQueryCollector.PG_ACTIVITY_QUERY
    assert "COALESCE(application_name, '') NOT LIKE 'rdst/%'" in sql


def test_mysql_monitor_query_excludes_rdst_sessions():
    sql = _normalized(ActivityQueryCollector.MYSQL_ACTIVITY_QUERY)
    assert _normalized(MYSQL_SELF_SESSION_EXCLUSION) in sql


def test_mysql_monitor_exclusion_mirrors_command_sets():
    command_set_sql = _normalized(
        TOP_COMMAND_SETS["rdst_top_mysql_activity"]["commands"][
            "mysql_activity_queries"
        ]["query"]
    )
    assert _normalized(MYSQL_SELF_SESSION_EXCLUSION) in command_set_sql

"""Regression tests for compatibility-lab database initialization."""

from features.cache import target_setup


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_database_ready_waits_for_the_requested_postgres_database(monkeypatch):
    probes = iter(
        [
            _Result(stdout="rdst-lab-test-psql-demo\n"),
            _Result(returncode=2, stderr='FATAL: database "demo" does not exist'),
            _Result(stdout="rdst-lab-test-psql-demo\n"),
            _Result(stdout="1\n"),
        ]
    )
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        return next(probes)

    monkeypatch.setattr(target_setup.subprocess, "run", run)
    monkeypatch.setattr(target_setup.time, "sleep", lambda _seconds: None)

    result = target_setup.wait_for_database_ready(
        container_name="rdst-lab-test-psql-demo",
        database_type="postgresql",
        database="demo",
        user="postgres",
        timeout=5,
    )

    assert result["success"] is True
    postgres_probes = [command for command in commands if "psql" in command]
    assert len(postgres_probes) == 2
    assert postgres_probes[-1][-4:] == [
        "-d",
        "demo",
        "-tAc",
        "SELECT 1",
    ]


def test_schema_restore_treats_psql_fatal_as_failure(monkeypatch):
    results = iter(
        [
            _Result(stdout="CREATE TABLE public.votes (id integer);\n"),
            _Result(
                returncode=1,
                stderr='psql: FATAL: database "demo" does not exist',
            ),
        ]
    )
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        return next(results)

    monkeypatch.setattr(target_setup.subprocess, "run", run)

    result = target_setup.recreate_schema_from_target(
        target_config={
            "engine": "postgresql",
            "host": "localhost",
            "port": 15432,
            "database": "demo",
            "user": "postgres",
            "password": "secret",
        },
        test_container="rdst-lab-test-psql-demo",
        test_database="demo",
    )

    assert result["success"] is False
    assert "FATAL" in result["error"]
    restore_command = commands[1]
    assert restore_command[
        restore_command.index("psql") + 1 : restore_command.index("-U")
    ] == ["-v", "ON_ERROR_STOP=1"]

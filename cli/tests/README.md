# Running Tests

Tests are organized into tiers based on what they need to run.

## Unit tests (no external dependencies)

This is what you want to run most of the time. Everything is mocked, nothing
talks to a real database or API.

```bash
pip install -r tests/requirements.txt
pytest tests/unit/ -v
```

Runs in about 30 seconds. If you're contributing a PR, make sure these pass.

## Integration tests (need PostgreSQL and/or MySQL)

These run actual CLI commands against real databases. The easiest way is with
Docker:

```bash
cd tests/integration
docker compose up -d        # starts postgres + mysql containers
./run_tests.sh postgresql   # or: ./run_tests.sh mysql
docker compose down
```

You can also point them at your own database by exporting the connection string:

```bash
export PSQL_CONNECTION_STRING="postgresql://user:pass@localhost:5432/testdb"
./run_tests.sh postgresql
```

These take a few minutes and require the database to have some test data loaded
(the Docker setup handles this automatically via `init-scripts/`).

## E2E tests (need a terminal + tmux)

These test interactive features like prompts and menus by driving a real tmux
session. They're excluded from default pytest runs.

```bash
pytest tests/e2e/ -v
```

You need tmux installed and a working terminal. These won't run in a headless
CI environment without some setup.

## Ask experimental tests (need a database + LLM API key)

Tests for the natural-language-to-SQL engine. Excluded by default because they
need both a live database and an `ANTHROPIC_API_KEY`.

```bash
export ANTHROPIC_API_KEY="..."
export TPCH_PASSWORD="..."
pytest tests/ask_experimental/ -v
```

## Manual testing

Some interactive features can't be automated. See `MANUAL_TESTING.md` for a
checklist you can walk through before a release.

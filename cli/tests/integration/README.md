# RDST Integration Tests

End-to-end integration tests for RDST. Two suites live here:

- **CLI suite** (`test_*.sh`) — drives the `rdst` CLI against PostgreSQL and MySQL containers. See [Quick Start](#quick-start).
- **API suite** (`test_*_api.py`) — drives the FastAPI app via httpx. The legacy tests mock the service layer; new tests marked `@pytest.mark.realdb` hit the same containers as the CLI suite. See [API realdb tests](#api-realdb-tests).

## Quick Start

### Run Tests with Docker Compose

Tests run against PostgreSQL and MySQL containers with pre-seeded test data:

```bash
# Run all tests (PostgreSQL + MySQL)
./run_tests_containerized.sh

# Run single database
./run_tests_containerized.sh postgresql
./run_tests_containerized.sh mysql
```

This:
1. Starts PostgreSQL and MySQL containers with IMDb test data
2. Waits for health checks
3. Runs the test suite
4. Cleans up containers automatically

**Requirements:**
- Docker and Docker Compose
- `ANTHROPIC_API_KEY` (optional, for LLM-dependent tests)

### Manual Container Management

```bash
# Start containers
docker compose up -d

# Wait for health checks (both should show "healthy")
docker compose ps

# Run tests with connection strings
export PSQL_CONNECTION_STRING="postgresql://testuser:testpassword@localhost:15432/testdb"
export MYSQL_CONNECTION_STRING="mysql://testuser:testpassword@localhost:13306/testdb"
./run_tests.sh postgresql

# Clean up
docker compose down -v
```

### Test Against AL23 Binary

AL23 binaries are Linux binaries. On macOS, they run in a Docker container; on Linux, they run natively.

```bash
# Build AL23 binary
./rdst/orchestrate_rdst.sh al23

# Set up test database containers
docker compose up -d

# Find and export binary path
BUILD_DIR=$(ls -td /tmp/rdst_build_* | head -1)
export RDST_BINARY="$BUILD_DIR/usr/bin/rdst"
export PSQL_CONNECTION_STRING="postgresql://testuser:testpassword@localhost:15432/testdb"

# Run tests
./run_tests.sh postgresql

# Clean up
docker compose down -v
```

## API realdb tests

API tests marked `@pytest.mark.realdb` drive the FastAPI app through `httpx.AsyncClient` (in-process via `ASGITransport`) but route every call to the same Postgres/MySQL containers used by the CLI suite. The service layer is **not** mocked — these are the regression net for `features/`.

Run locally:

```bash
cd rdst/tests/integration
docker compose up -d postgres        # or: mysql

export RDST_TEST_PASSWORD=testpassword
export RDST_TEST_ENGINE=postgresql   # or: mysql
pytest tests/test_*_api.py -v -m realdb

docker compose down -v
```

In CI, `.buildkite/run_api_integration_tests.sh postgresql` does the container management for you. Only PostgreSQL runs in CI for the API suite — dialect divergence is already covered by the CLI suite running against both engines. The `mysql` runner mode remains runnable locally for parity testing.

The in-process API tests still run, gated by `-m "not realdb"` from the same runner script (no-arg mode). They drive the FastAPI app end-to-end too — they just don't require a DB container, and use real services against an isolated `~/.rdst/` (per-test tmp dir).

### Realdb files

- `test_realdb_configure_api.py` — real driver `/configure/.../test`, update/remove round-trips, default-target fallback through `/api/analyze`.
- `test_realdb_configure_analyze_api.py` — configure → analyze flow, full SSE stream against the live DB.
- `test_realdb_top_api.py` — historical `/api/top` reads back a seeded query from `pg_stat_statements` (PG only; MySQL skip — `testuser` lacks `performance_schema` grants), realtime SSE stream pinned via `duration=1`.
- `test_realdb_schema_api.py` — `/api/schema` introspects the live DB and returns `title_basics` / `title_ratings` with their columns; covers PG vs MySQL `information_schema` dialect divergence.
- `test_realdb_query_registry_api.py` — register a query, run `/query-registry/benchmark` for 1s against the DB, assert non-zero successes and per-query timing.
- `test_realdb_init_api.py` — `/init/validate` for the live target reports `success=True` (LLM result not pinned).
- `test_realdb_cache_api.py` — full `/cache` lifecycle (deploy → status → add → run → list → remove) against a real Readyset container. Skipped when `SKIP_READYSET_CACHE_TESTS=true` (default in CI; unset locally to run).
- `test_realdb_readyset_api.py` — `/api/readyset/setup` SSE stream reaches `complete` with `readyset_port` populated. Same skip gate as the cache lifecycle.

The Readyset-gated tests above skip via `SKIP_READYSET_CACHE_TESTS` because the Buildkite agent's docker socket / sibling-container networking can't reach the upstream DB across the compose network in our current shape. They remain runnable locally on demand.

Shared SSE collector is exposed as the `collect_sse_events` fixture in `tests/conftest.py`.

### In-process API files

These are the `-m "not realdb"` tests: real services, real `~/.rdst/`
under `tmp_rdst_home`, no DB container required. Anything pulling on a
system boundary uses a per-test fixture (`inmemory_keyring` for the OS
keychain) — never a service-layer mock.

- `test_configure_api.py` — full configure CRUD + default-target round-trips on disk.
- `test_target_lock_api.py` — `target_guard` lock/unlock behavior.
- `test_query_registry_api.py` — register/list/delete/pagination/dedup.
- `test_init_api.py` — init status/complete on disk; validate-with-targets is realdb (slice 4).
- `test_env_api.py` — env requirements + secret set with the in-memory keyring backend.
- `test_dev_api.py` — clear-keyring path with the in-memory keyring backend.
- `test_ask_api.py` — guard + service error paths; happy path is realdb.
- `test_scan_api.py` — scan against `fixtures/scan/` with `dry_run=true` (no LLM).
- `test_interactive_api.py` — read/delete surface for ConversationRegistry.
- `test_trial_api.py` — status + simulate-exhaust against on-disk trial config.
- `test_status_api.py` — `/api/status` against seeded targets.
- `test_cache_api_inproc.py` — config-only cache paths; deploy/run/remove is realdb (slice 5).

## Test Structure

```
tests/integration/
├── run_tests.sh                  # Main test runner
├── run_tests_containerized.sh    # Docker Compose test runner
├── docker-compose.yml            # Database containers for local dev
├── docker-compose.ci.yml         # CI version with test runner container
├── init-scripts/                 # Database initialization
│   ├── postgres/
│   │   ├── 01-schema.sql
│   │   └── 02-data.sql
│   └── mysql/
│       ├── 01-schema.sql
│       └── 02-data.sql
├── lib/
│   ├── setup.sh                  # Environment setup
│   └── helpers.sh                # Assertions and utility functions
├── tests/
│   ├── test_config.sh            # Configuration command tests
│   ├── test_analyze.sh           # Analyze command tests
│   ├── test_cache.sh             # Cache command tests
│   ├── test_top_and_registry.sh  # Top queries and registry tests
│   ├── test_query_command.sh     # Query management tests
│   ├── test_scan.sh              # ORM scanning tests
│   └── test_errors.sh            # Error handling tests
└── fixtures/
    └── scan/                     # ORM fixture files for scan tests
```

## Test Coverage

### 1. Configuration Commands
- Add/remove/list database targets
- Set default target
- Validate configuration file format

### 2. Analyze Commands
- Inline query analysis
- Hash-based query lookup
- Tag-based query lookup
- File and stdin input
- Hash consistency (normalized structure hashing)
- `--readyset-cache` flag for parallel cacheability analysis

### 3. Cache Commands
- SQL text caching
- Hash-based caching
- JSON output format
- Duplicate query handling
- Readyset container management

### 4. Top & List Commands
- Query listing with limits
- Top slow queries snapshot
- Interactive query selection

### 5. Registry & Files
- Query registry persistence
- Analysis results storage
- Hash and tag lookups

### 6. Error Handling
- Invalid targets
- Malformed SQL
- Unknown hash IDs
- Wrong credentials

## Environment Variables

### Database Configuration (Required)
- `PSQL_CONNECTION_STRING` - PostgreSQL connection string
- `MYSQL_CONNECTION_STRING` - MySQL connection string

### Test Selection
- `TEST_POSTGRESQL` - Enable PostgreSQL tests (default: true)
- `TEST_MYSQL` - Enable MySQL tests (default: true)

### Target Names
- `PG_TARGET_NAME` - RDST target name for PostgreSQL (default: test-db-pg)
- `MYSQL_TARGET_NAME` - RDST target name for MySQL (default: test-db-mysql)

### Binary Selection
- `RDST_BINARY` - Path to compiled RDST binary (optional). When set, tests run against the binary instead of Python source.

### Python
- `PYTHON_BIN` - Python binary to use (default: python3)

### Cache Test Control
- `SKIP_READYSET_CACHE_TESTS` - Skip cache tests (default: false). Set `true` in containerized mode since ReadySet can't reach DBs inside Docker from the host.

## CI/CD Integration

Tests run automatically in Buildkite as part of the build pipeline using Docker Compose:

### Test Suites
1. **PostgreSQL Tests** - Tests Python source (pre-merge)
2. **MySQL Tests** - Tests Python source (pre-merge)
3. **AL23 Integration Tests** - Tests compiled AL23 binary (post-build)

### Pipeline Flow
1. Spin up PostgreSQL/MySQL containers via Docker Compose
2. Run integration tests
3. Clean up containers

All test suites:
- Use local Docker containers (no external dependencies)
- Run full test suite
- Clean up on completion or failure
- 20-minute timeout
- Auto-retry on failure (up to 2 times)

See `rdst/.buildkite/pipeline.yml` for pipeline configuration.

## Local Development

### Running Specific Test Modules

```bash
# Start containers first
docker compose up -d

# Set connection strings
export PSQL_CONNECTION_STRING="postgresql://testuser:testpassword@localhost:15432/testdb"

# Source the setup and helpers
source lib/setup.sh
source lib/helpers.sh

# Source and run specific test
source tests/test_config.sh
setup_upstream_databases
set_db_context postgresql
test_config_commands

# Clean up
docker compose down -v
```

### Debugging

Enable verbose output:
```bash
bash -x ./run_tests_containerized.sh postgresql
```

View container logs:
```bash
docker compose logs postgres
docker compose logs mysql
```

Check container status:
```bash
docker compose ps
```

## Test Database

The test containers are initialized with a subset of IMDb data:
- `title_basics` - Movie/show information (~40 records)
- `title_ratings` - Ratings data

This provides enough data for testing queries without large data volumes.

## Architecture

### Test Isolation
- Each test run uses a temporary HOME directory
- Registry files are isolated per test run
- Containers are cleaned up after each suite

### Container Lifecycle
1. `docker compose up -d` starts PostgreSQL and MySQL
2. Health checks wait for databases to be ready
3. Tests run against the containers
4. `docker compose down -v` removes containers and volumes

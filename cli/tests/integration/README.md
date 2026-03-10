# RDST CLI Integration Tests

End-to-end integration tests for the RDST CLI tool, testing configuration, analysis, caching, query registry, and error handling against both PostgreSQL and MySQL databases.

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

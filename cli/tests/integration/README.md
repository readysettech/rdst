# RDST CLI Integration Tests

End-to-end integration tests for the RDST CLI tool, testing configuration, analysis, caching, query registry, and error handling against both PostgreSQL and MySQL databases.

## Quick Start

### Run All Tests (Both PostgreSQL and MySQL)
```bash
./run_tests.sh
```

### Run Tests for Single Database
```bash
./run_tests.sh postgresql
./run_tests.sh mysql
```

### Use Existing Database (Skip Container Creation)
```bash
PSQL_CONNECTION_STRING="postgresql://user:pass@host:port/db" ./run_tests.sh postgresql
MYSQL_CONNECTION_STRING="mysql://user:pass@host:port/db" ./run_tests.sh mysql
```

### Test Against AL23 Binary Locally

AL23 binaries are Linux binaries. On macOS, they run in a Docker container; on Linux, they run natively.

#### Quick Start (macOS or Linux)
```bash
# Set required environment variables
export API_BASE_URL="https://api-dev01.apps.readyset.cloud"
export ADMIN_API_TOKEN="your-admin-token"

# Test both PostgreSQL and MySQL
./rdst/tests/integration/test_al23_locally.sh

# Or test single database
./rdst/tests/integration/test_al23_locally.sh postgresql
./rdst/tests/integration/test_al23_locally.sh mysql
```

#### Development Workflow (Build Once, Test Multiple Times)
```bash
# 1. Build AL23 binary once
./rdst/orchestrate_rdst.sh al23

# 2. Test repeatedly without rebuilding
SKIP_BUILD=1 ./rdst/tests/integration/test_al23_locally.sh postgresql

# 3. Make code changes, rebuild, test again
./rdst/orchestrate_rdst.sh al23
SKIP_BUILD=1 ./rdst/tests/integration/test_al23_locally.sh postgresql
```

#### What `test_al23_locally.sh` Does
1. **Builds AL23 binary** (unless `SKIP_BUILD=1`) via `orchestrate_rdst.sh`
2. **Extracts binary** from RPM package (automatically skips if already extracted)
3. **Runs tests** inside `amazonlinux:2023` container (on macOS) or natively (on Linux)
4. **Uses host networking** so ReadySet containers are accessible
5. **Cleans up** test containers on completion

#### Testing on Native Linux (Alternative)

If you're already on Linux and want to test the binary directly without Docker:

```bash
# 1. Build the AL23 binary
./rdst/orchestrate_rdst.sh al23

# 2. Find the built binary
BUILD_DIR=$(ls -td /tmp/rdst_build_* | head -1)
export RDST_BINARY="$BUILD_DIR/usr/bin/rdst"

# 3. Run tests directly
./rdst/tests/integration/run_tests.sh postgresql
```

## Test Structure

The test suite is modular for better maintainability:

```
tests/integration/
├── run_tests.sh              # Main test runner
├── lib/
│   ├── setup.sh              # Environment setup and container management
│   └── helpers.sh            # Assertions and utility functions
├── tests/
│   ├── test_config.sh        # Configuration command tests
│   ├── test_analyze.sh       # Analyze command tests (including --readyset flag)
│   ├── test_cache.sh         # Cache command tests
│   ├── test_top_and_registry.sh  # Top, list, and registry tests
│   └── test_errors.sh        # Error handling tests
└── rdst_integration_tests.sh  # DEPRECATED: Old monolithic test file
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
- --readyset flag for parallel cacheability analysis

### 3. Cache Commands
- SQL text caching
- Hash-based caching
- JSON output format
- Duplicate query handling
- ReadySet container management

### 4. Top & List Commands
- Query listing with limits
- Top slow queries snapshot
- Interactive query selection (rdst top --interactive)

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

### Database Configuration
- `PSQL_CONNECTION_STRING` - PostgreSQL connection string (skips container creation if set)
- `MYSQL_CONNECTION_STRING` - MySQL connection string (skips container creation if set)
- `TEST_POSTGRESQL` - Enable PostgreSQL tests (default: true)
- `TEST_MYSQL` - Enable MySQL tests (default: true)

### Target Names
- `PG_TARGET_NAME` - RDST target name for PostgreSQL (default: test-db-pg)
- `MYSQL_TARGET_NAME` - RDST target name for MySQL (default: test-db-mysql)

### Container Management
- `API_BASE_URL` - Admin API URL for container creation (required, no default)
- `ADMIN_API_TOKEN` - Admin API token for container creation (optional, required for creating containers)

### RDST Binary Selection
- `RDST_BINARY` - Path to compiled RDST binary (optional). When set, tests run against the binary instead of Python source. Used for testing AL23/RPM/DEB builds.

### Python
- `PYTHON_BIN` - Python binary to use (default: python3)

## CI/CD Integration

The tests run automatically in Buildkite as part of the build pipeline:

### Test Suites
1. **PostgreSQL Tests** - Tests Python source (pre-merge)
2. **MySQL Tests** - Tests Python source (pre-merge)
3. **AL23 Integration Tests** - Tests compiled AL23 binary (post-build)

### Pipeline Flow
1. Build AL23 binary (`orchestrate_rdst.sh al23`)
2. Run integration tests against the binary
3. Upload binary to S3 (if AWS credentials available)

All test suites:
- Create database containers via admin API
- Run full test suite
- Clean up containers on completion or failure
- Have 30-minute timeout
- Auto-retry on failure (up to 2 times)

See `rdst/.buildkite/pipeline.yml` for pipeline configuration.

## Local Development

### Prerequisites

**For Python Source Testing:**
- Docker (for ReadySet containers created by cache tests)
- Network access to admin API (or provide connection strings)

**For AL23 Binary Testing:**
- Docker (required on macOS; optional on Linux)
- Network access to admin API (or provide connection strings)

### Running Specific Test Modules

The modular structure allows you to run individual test modules during development:

```bash
# Source the setup and helpers
source lib/setup.sh
source lib/helpers.sh

# Source and run specific test
source tests/test_config.sh
setup_upstream_databases
set_db_context postgresql
test_config_commands
```

### Debugging

#### Python Source Tests
Enable verbose output for troubleshooting:
```bash
bash -x ./run_tests.sh postgresql
```

#### AL23 Binary Tests
Check binary and test environment:
```bash
# Verify binary exists and size
BUILD_DIR=$(ls -td /tmp/rdst_build_* | head -1)
ls -lh $BUILD_DIR/usr/bin/rdst

# Test binary directly
$BUILD_DIR/usr/bin/rdst version
$BUILD_DIR/usr/bin/rdst --help

# Check Docker containers
docker ps -a | grep rdst

# View test container logs (if tests are running)
docker logs -f <container-id>
```

#### Common Issues
- **S3 upload failures**: Ignored for local builds (warning only)
- **Network errors**: Ensure `--network host` for AL23 container tests
- **Missing libraries**: Rebuild with updated `build_rdst.sh` if adding dependencies

## AL23 Binary Build Details

The AL23 binary is built using Nuitka to compile Python to a standalone executable:

### Included in Binary
- **RDST CLI code** - All Python source from `rdst/`
- **Workflow JSON files** - Configuration files from `lib/workflows/`
- **Database clients** - `psycopg2` and `pymysql` libraries
- **Dependencies** - All packages from `requirements.txt`

### Build Process
1. **Docker image**: `readyset-rdst-builder-rpm-al23` (Amazon Linux 2023 with Nuitka)
2. **Compilation**: Nuitka converts Python → C → native binary
3. **Optimization**: LTO (Link-Time Optimization) enabled
4. **Packaging**: FPM creates `.rpm.al23` package
5. **Output**: Single 250-300MB executable at `/tmp/rdst_build_*/usr/bin/rdst`

### Testing Strategy
- **Python source**: Fast iteration during development
- **AL23 binary**: Validates production deployment artifact
- Both use the same test suite via `RDST_BINARY` environment variable

## Architecture

### Container Management
1. **Admin API Integration** - Creates PostgreSQL/MySQL containers on demand
2. **Connection String Parsing** - Extracts credentials from connection URLs
3. **Automatic Cleanup** - Removes all created containers on exit (success or failure)

### Test Containers
- **Upstream Database** - Created by admin API or user-provided
- **Test Database** - Created by cache command for testing
- **ReadySet Container** - Created by cache command for performance testing

### Test Isolation
- Each test run uses temporary HOME directory
- Registry files are isolated per test run
- Containers are cleaned up after each suite
- Tests can run in parallel (PostgreSQL and MySQL simultaneously)

## Migration from Old Test File

The original `rdst_integration_tests.sh` (1193 lines) has been split into modular components:

**Before:**
- Single 1193-line file
- Difficult to navigate and maintain
- All functionality in one place

**After:**
- 6 focused modules (< 200 lines each)
- Clear separation of concerns
- Easier to add new tests
- Better code reuse

The old file is kept for reference but should not be modified. All new test development should use the modular structure.

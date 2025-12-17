# Ask/Schema Tests

Tests for the `rdst ask` and `rdst schema` features.

## Status

- **NOT included in regular test runs** - these require a live database connection
- **Manual testing guide** - see `MANUAL_TEST_CASES.md` for functional tests
- **Database required** - most tests need a database (e.g., TPC-H on localhost:5433)

## Test Categories

### Automated Unit Tests

```bash
# Run ask3 engine tests (mocked, no DB needed)
pytest tests/ask_experimental/test_ask3_engine/ -v

# Run SQL validation tests
pytest tests/ask_experimental/ask_validation/ -v

# Run semantic layer tests (needs DB)
pytest tests/ask_experimental/semantic_layer_integration/ -v
```

### Manual Test Cases

See `ask_validation/MANUAL_TEST_CASES.md` for 25+ manual test scenarios covering:
- Basic functionality (5 tests)
- Safety features (6 tests)
- Interactive features (6 tests)
- Error recovery (2 tests)
- Advanced queries (4 tests)
- Validation suite (1 automated)
- Interactive scripts (3 tests)
- Edge cases (3 tests)

### Quick Smoke Test

```bash
# These 4 commands verify basic Ask functionality
python3 tests/ask_experimental/ask_validation/test_limit_injection.py
python3 rdst.py ask "Show me 5 posts" --no-interactive
printf "y\nq\n" | python3 rdst.py ask "Show me 5 users"
python3 rdst.py ask "Delete all posts" --no-interactive 2>&1 | grep -i "validation"
```

## Datasets

Tests were developed against:
- **Stack Overflow** dataset (Gautam's primary test dataset)

You may also want to test against:
- **TPC-H** dataset (your dataset)

## Notes

- Ask requires semantic layer setup for best results: `rdst schema init`
- Agent mode (complex queries) is less tested than linear flow
- Interactive TTY flows cannot be automated

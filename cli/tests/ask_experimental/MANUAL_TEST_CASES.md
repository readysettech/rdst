# Manual Test Cases for `rdst ask`

This guide provides hands-on test cases to verify all features of the `rdst ask` command.

> **Note:** These tests are dataset-agnostic. Choose the appropriate dataset section below.

---

## Dataset Configuration

Before running tests, identify your dataset and set up accordingly:

### Option A: Stack Overflow Dataset

```bash
export TARGET=stackoverflow
export TEST_TABLE=posts
export TEST_TABLE_2=users
export TEST_COLUMN=score
export TEST_RELATION="posts with their user information"
```

**Schema expectations:**
- `posts` table with `score`, `title`, `created_at` columns
- `users` table with `id`, `name` columns
- `comments` table (optional)

### Option B: TPC-H Dataset

```bash
export TARGET=tpch
export TEST_TABLE=orders
export TEST_TABLE_2=customer
export TEST_COLUMN=totalprice
export TEST_RELATION="orders with their customer information"
```

**Schema expectations:**
- `orders` table with `totalprice`, `orderdate` columns
- `customer` table with `custkey`, `name` columns
- `lineitem` table

### Option C: Custom Dataset

```bash
export TARGET=<your-target>
export TEST_TABLE=<main-table>
export TEST_TABLE_2=<related-table>
export TEST_COLUMN=<numeric-column>
export TEST_RELATION="<table1> with their <table2> information"
```

---

## Prerequisites

```bash
# Set required environment variables
export DB_PASSWORD=<your-password>
export ANTHROPIC_API_KEY=<your-key>  # or OPENAI_API_KEY

# Verify target is configured
rdst configure list
```

---

## Category 1: Basic Functionality

### Test 1.1: Simple Query (Non-Interactive)

**Purpose:** Verify basic SQL generation and execution

```bash
rdst ask "Show me 5 ${TEST_TABLE}" --target $TARGET --no-interactive
```

**Expected:**
- [ ] Generates `SELECT * FROM ${TEST_TABLE} LIMIT 5`
- [ ] Executes and displays results
- [ ] No prompts (non-interactive mode)

---

### Test 1.2: Simple Query (Interactive)

**Purpose:** Test interactive confirmation flow

```bash
rdst ask "Show me 10 ${TEST_TABLE_2}" --target $TARGET
```

**User Actions:**
- Type `y` to confirm execution
- Type `q` to quit from next actions menu

**Expected:**
- [ ] Shows SQL preview
- [ ] Asks for confirmation
- [ ] Executes query
- [ ] Displays results
- [ ] Shows next actions menu

---

### Test 1.3: Query with WHERE Clause

**Purpose:** Verify filtering logic

```bash
rdst ask "Show me ${TEST_TABLE} with ${TEST_COLUMN} greater than 5" --target $TARGET --no-interactive
```

**Expected:**
- [ ] Generates query with WHERE clause
- [ ] Applies LIMIT automatically
- [ ] Returns filtered results

---

## Category 2: Safety Features

### Test 2.1: LIMIT Injection

**Purpose:** Verify automatic LIMIT injection

```bash
rdst ask "Show me all ${TEST_TABLE}" --target $TARGET --no-interactive
```

**Expected:**
- [ ] Query generated with `LIMIT 100`
- [ ] Warning: "Added LIMIT 100 to prevent unbounded results"
- [ ] Query executes successfully

---

### Test 2.2: Read-Only Enforcement

**Purpose:** Verify write operations are blocked

```bash
rdst ask "Delete all ${TEST_TABLE}" --target $TARGET --no-interactive
rdst ask "Update ${TEST_TABLE} set ${TEST_COLUMN} = 0" --target $TARGET --no-interactive
```

**Expected:**
- [ ] Query rejected with validation error
- [ ] Error message mentions "Write operation detected"
- [ ] No query executed

---

### Test 2.3: Query Timeout

**Purpose:** Verify timeout protection (30 seconds default)

```bash
# Try a potentially expensive query
rdst ask "Show me all ${TEST_TABLE} with all their related data" --target $TARGET --no-interactive
```

**Expected:**
- [ ] Query runs with timeout protection
- [ ] If exceeds 30s, shows timeout error
- [ ] Does not hang indefinitely

---

## Category 3: Interactive Features

### Test 3.1: Query Refinement

**Purpose:** Test interactive query modification

```bash
rdst ask "Show me ${TEST_TABLE}" --target $TARGET
```

**User Actions:**
1. Type `y` to confirm execution
2. Type `1` to choose "Refine Query"
3. Type "Change LIMIT to 20 and add ORDER BY ${TEST_COLUMN} DESC"
4. Type `y` to confirm refined query
5. Type `q` to quit

**Expected:**
- [ ] Original query executes
- [ ] Refinement prompt appears
- [ ] New query generated with requested changes
- [ ] Refined query executes

---

### Test 3.2: Save Query

**Purpose:** Test query registry integration

```bash
rdst ask "Show me top 10 ${TEST_TABLE} by ${TEST_COLUMN}" --target $TARGET
```

**User Actions:**
1. Type `y` to confirm execution
2. Type `4` to choose "Save Query"
3. Type `top-${TEST_TABLE}` as the query name

**Expected:**
- [ ] Query saved to registry
- [ ] Natural language question stored as metadata

**Verify:**
```bash
rdst query list | grep "top-${TEST_TABLE}"
```

---

### Test 3.3: Command Integration - Analyze

**Purpose:** Test seamless transition to analyze command

```bash
rdst ask "Show me ${TEST_TABLE} with ${TEST_COLUMN} > 10" --target $TARGET
```

**User Actions:**
1. Type `y` to confirm execution
2. Type `2` to choose "Run Analyze"

**Expected:**
- [ ] Query auto-saved to registry
- [ ] Analyze command runs automatically
- [ ] Full analysis output displayed

---

## Category 4: Error Recovery

### Test 4.1: Zero Results Recovery

**Purpose:** Test handling of queries that return no results

```bash
rdst ask "Show me ${TEST_TABLE} from year 3000" --target $TARGET
```

**Expected:**
- [ ] Query executes but returns 0 rows
- [ ] System offers to investigate why
- [ ] Suggests corrected query or explanation

---

### Test 4.2: Invalid Table Recovery

**Purpose:** Test handling of non-existent tables

```bash
rdst ask "Show me data from nonexistent_table_xyz" --target $TARGET
```

**Expected:**
- [ ] Query fails with "table doesn't exist" error
- [ ] System offers recovery
- [ ] Suggests valid table names

---

## Category 5: Advanced Queries

### Test 5.1: JOIN Query

**Purpose:** Test multi-table queries

```bash
rdst ask "Show me ${TEST_RELATION}" --target $TARGET
```

**Expected:**
- [ ] Generates JOIN query
- [ ] Includes necessary tables
- [ ] LIMIT applied
- [ ] Results show combined data

---

### Test 5.2: Aggregation Query

**Purpose:** Test GROUP BY and aggregates

```bash
rdst ask "Show me the count of ${TEST_TABLE} per ${TEST_TABLE_2}" --target $TARGET
```

**Expected:**
- [ ] Generates query with COUNT and GROUP BY
- [ ] Returns aggregated results

---

### Test 5.3: ORDER BY with LIMIT

**Purpose:** Test sorting with limits

```bash
rdst ask "Show me the top 10 ${TEST_TABLE} sorted by ${TEST_COLUMN} descending" --target $TARGET
```

**Expected:**
- [ ] Generates query with ORDER BY ... DESC LIMIT 10
- [ ] LIMIT preserved (not replaced with 100)
- [ ] Returns sorted results

---

## Category 6: Validation Test Suite (Automated)

```bash
pytest tests/ask_experimental/ask_validation/test_limit_injection.py -v
```

**Expected:** All 7 tests pass:
- [ ] LIMIT injection
- [ ] LIMIT reduction
- [ ] LIMIT with ORDER BY
- [ ] Read-only enforcement
- [ ] Dangerous pattern detection
- [ ] Complexity estimation
- [ ] LIMIT with OFFSET

---

## Category 7: Edge Cases

### Test 7.1: Empty Question

```bash
rdst ask "" --target $TARGET
```

**Expected:**
- [ ] Prompts for question (interactive mode)
- [ ] Or shows usage error (non-interactive)

---

### Test 7.2: Very Long Question

```bash
rdst ask "Show me all ${TEST_TABLE} where the ${TEST_COLUMN} is greater than 5 and was created in the last 30 days and has at least 3 related items" --target $TARGET --no-interactive
```

**Expected:**
- [ ] LLM handles long question
- [ ] Generates appropriate query
- [ ] All conditions incorporated

---

### Test 7.3: Ambiguous Question

```bash
rdst ask "Show me the best ones" --target $TARGET
```

**Expected:**
- [ ] LLM may ask for clarification
- [ ] Or makes reasonable assumption and documents it
- [ ] Assumptions displayed to user

---

## Quick Smoke Test

Run these 4 commands to verify basic functionality:

```bash
# 1. Verify validation suite
pytest tests/ask_experimental/ask_validation/test_limit_injection.py

# 2. Verify basic functionality
rdst ask "Show me 5 ${TEST_TABLE}" --target $TARGET --no-interactive

# 3. Verify interactive flow
printf "y\nq\n" | rdst ask "Show me 5 ${TEST_TABLE_2}" --target $TARGET

# 4. Verify safety (should fail validation)
rdst ask "Delete all ${TEST_TABLE}" --target $TARGET --no-interactive 2>&1 | grep -i "validation\|rejected\|write"
```

If all 4 pass, basic Ask functionality is working.

---

## Agent Mode Tests (Advanced)

> **Note:** Agent mode is less tested and may have unpredictable behavior.

### Test: Force Agent Mode

```bash
rdst ask "Find complex patterns in the data" --target $TARGET --agent
```

**Expected:**
- [ ] Skips linear flow
- [ ] Enters agent exploration mode
- [ ] Agent can ask clarifying questions
- [ ] Eventually produces SQL or explains why it can't

---

## Summary Checklist

After running all tests, verify:

- [ ] Basic queries execute successfully
- [ ] LIMIT injection works (default 100)
- [ ] Write operations are blocked
- [ ] Interactive refinement works
- [ ] Query save works
- [ ] Command integration works (analyze)
- [ ] Error recovery provides useful diagnostics
- [ ] JOIN and aggregation queries work
- [ ] All validation tests pass
- [ ] Edge cases handled gracefully

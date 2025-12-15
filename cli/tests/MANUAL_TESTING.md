# RDST Manual Testing Guide

This document lists all interactive/TTY features that **cannot be automated** and must be manually tested before releases.

These tests require a human at a terminal with a configured database target.

---

## Prerequisites

```bash
# 1. Have a database target configured
rdst configure list

# 2. If no targets, set one up first (this is itself a manual test - see below)
rdst init

# 3. Ensure password env var is exported
export <TARGET>_PASSWORD="your-password"
```

---

## 1. First-Time Setup (`rdst init`)

**Purpose:** Verify the first-time wizard flow works end-to-end.

```bash
# Remove existing config to test fresh install
mv ~/.rdst/config.toml ~/.rdst/config.toml.bak

# Run init
rdst init
```

**Test Steps:**
1. [ ] Wizard prompts for LLM provider selection
2. [ ] Can select Claude/OpenAI/LMStudio
3. [ ] Prompts for API key (or base URL for LMStudio)
4. [ ] Wizard prompts to add a database target
5. [ ] Can enter host, port, user, database, password_env
6. [ ] Connection test runs and shows success/failure
7. [ ] Can set target as default
8. [ ] Config saved to `~/.rdst/config.toml`

**Restore config:**
```bash
mv ~/.rdst/config.toml.bak ~/.rdst/config.toml
```

---

## 2. Configuration Wizard (`rdst configure`)

### 2.1 Add Target Interactive

```bash
rdst configure add
```

**Test Steps:**
1. [ ] Prompts for engine selection (PostgreSQL/MySQL)
2. [ ] Prompts for all connection fields
3. [ ] Prompts for TLS, read-only options
4. [ ] Asks to verify connection
5. [ ] Asks to set as default
6. [ ] Shows confirmation before saving

### 2.2 Edit Target Interactive

```bash
rdst configure edit <target-name>
```

**Test Steps:**
1. [ ] Shows current values as defaults
2. [ ] Can modify individual fields
3. [ ] Saves changes correctly

### 2.3 Remove Target with Confirmation

```bash
rdst configure remove <target-name>
```

**Test Steps:**
1. [ ] Prompts for confirmation (y/N)
2. [ ] 'n' or Enter cancels
3. [ ] 'y' removes target

---

## 3. Analyze Interactive Mode

### 3.1 Interactive Q&A After Analysis

```bash
rdst analyze -q "SELECT * FROM users LIMIT 10" --target <target> --interactive
```

**Test Steps:**
1. [ ] Analysis runs and displays results
2. [ ] Enters interactive mode with ">" prompt
3. [ ] Can ask follow-up questions about the analysis
4. [ ] Responses are contextual (remember previous analysis)
5. [ ] Typing 'exit' or 'quit' exits cleanly
6. [ ] Ctrl-C exits without crashing

### 3.2 Continue Existing Conversation

```bash
# First run - creates conversation
rdst analyze -q "SELECT * FROM orders" --target <target> --interactive
# Answer some questions, then exit

# Second run - should offer to continue
rdst analyze -q "SELECT * FROM orders" --target <target> --interactive
```

**Test Steps:**
1. [ ] Detects existing conversation for same query
2. [ ] Prompts: "Continue existing conversation or start new? [c/n]"
3. [ ] 'c' loads previous conversation context
4. [ ] 'n' starts fresh

### 3.3 Ctrl-C Handling in Interactive Mode

```bash
rdst analyze -q "SELECT 1" --target <target> --interactive
```

**Test Steps:**
1. [ ] Enter interactive mode
2. [ ] Press Ctrl-C mid-question
3. [ ] Should exit gracefully (not crash with traceback)
4. [ ] Press Ctrl-C at prompt
5. [ ] Should exit gracefully

---

## 4. Top Command Interactive

### 4.1 Interactive Query Selection

**Setup:** Need queries running against database. In a separate terminal:

```bash
# Terminal 1: Generate slow queries (PostgreSQL example)
while true; do
  psql -h <host> -U <user> -d <db> -c "SELECT pg_sleep(0.5), * FROM large_table LIMIT 1000" &
  sleep 1
done
```

```bash
# Terminal 2: Run top interactive
rdst top --target <target> --interactive
```

**Test Steps:**
1. [ ] Shows live updating query list
2. [ ] Queries have selection numbers
3. [ ] Can enter a number to select query
4. [ ] Selected query goes to analyze
5. [ ] 'q' quits cleanly
6. [ ] Ctrl-C quits cleanly

### 4.2 Top with Duration (Non-Interactive Snapshot)

```bash
rdst top --target <target> --duration 10
```

**Test Steps:**
1. [ ] Runs for exactly 10 seconds
2. [ ] Outputs results after duration
3. [ ] No interactive prompts

---

## 5. Query Registry Interactive

### 5.1 Query List Interactive Browser

```bash
rdst query list
```

**Test Steps:**
1. [ ] Shows paginated query list
2. [ ] Can navigate pages (if multiple)
3. [ ] Can select a query by number
4. [ ] Selected query offers actions (analyze, show, delete)
5. [ ] 'q' quits browser

### 5.2 Query Add with Editor

```bash
rdst query add my-query
# (without -q flag, should open $EDITOR)
```

**Test Steps:**
1. [ ] Opens $EDITOR (vim, nano, etc.)
2. [ ] Can write SQL in editor
3. [ ] Saving and closing adds query to registry
4. [ ] Empty file cancels add

### 5.3 Query Delete Confirmation

```bash
rdst query delete <query-name>
```

**Test Steps:**
1. [ ] Prompts "Delete query X? (y/N)"
2. [ ] 'n' or Enter cancels
3. [ ] 'y' deletes query

---

## 6. Report Command Interactive

```bash
rdst report
```

**Test Steps:**
1. [ ] Prompts for feedback type (positive/negative)
2. [ ] Prompts for feedback text (multiline)
3. [ ] Empty line or Ctrl-D ends input
4. [ ] Optionally prompts for email
5. [ ] Optionally prompts for query hash
6. [ ] Shows confirmation before sending
7. [ ] Displays success/failure message

---

## 7. No Command (Main Menu)

```bash
rdst
# (with no arguments)
```

**Test Steps:**
1. [ ] Shows interactive menu of commands
2. [ ] Can select command by number
3. [ ] Selected command runs with prompts for required args
4. [ ] 'q' or Ctrl-C exits

---

## 8. Analyze Input Resolution

### 8.1 Interactive Query Prompt

```bash
rdst analyze --target <target>
# (no query provided)
```

**Test Steps:**
1. [ ] If saved queries exist, prompts "Browse saved queries? (Y/n)"
2. [ ] 'y' shows query browser
3. [ ] 'n' prompts for SQL input
4. [ ] Multiline SQL input works (empty line to finish)
5. [ ] Ctrl-C cancels

### 8.2 Query Browser from Analyze

```bash
rdst analyze --target <target>
# Select 'y' to browse queries
```

**Test Steps:**
1. [ ] Shows saved queries with numbers
2. [ ] Can select query to analyze
3. [ ] Can navigate pages
4. [ ] 'q' cancels and returns to prompt

---

## 9. Error Handling

### 9.1 Connection Failure During Interactive

```bash
# Stop database, then:
rdst top --target <target> --interactive
```

**Test Steps:**
1. [ ] Shows clear error message
2. [ ] Doesn't crash with traceback
3. [ ] Can exit cleanly

### 9.2 LLM API Failure During Interactive

```bash
# Set invalid API key
export ANTHROPIC_API_KEY="invalid"
rdst analyze -q "SELECT 1" --target <target> --interactive
```

**Test Steps:**
1. [ ] Shows clear error about API key
2. [ ] Doesn't crash
3. [ ] Suggests checking API key

---

## Quick Smoke Test Checklist

Run these 5 tests before any release:

1. [ ] `rdst init` - Complete wizard flow
2. [ ] `rdst analyze -q "SELECT 1" --target <target> --interactive` - Enter/exit interactive mode
3. [ ] `rdst top --target <target> --duration 5` - Top works
4. [ ] `rdst query list` - Browser works
5. [ ] `rdst report --reason "test" --positive` - Report submits

---

## Notes

- All tests assume a working database connection
- Some tests may need active queries running (for top)
- Ctrl-C should always exit gracefully (no Python tracebacks)
- Interactive prompts should have sensible defaults
- TTY detection should work (`stdin.isatty()`)

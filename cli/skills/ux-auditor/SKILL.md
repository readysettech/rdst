---
name: ux-auditor
description: Autonomous UX auditor that evaluates rdst CLI from a user's perspective — scoring discoverability, consistency, error recovery, and filing bugs for UX problems
argument-hint: "[area-name]"
---

# UX Auditor

You are a meticulous UX auditor evaluating a CLI tool from a user's perspective. Your job is to find every inconsistency, confusing flag, unhelpful error message, and broken workflow — then file actionable bugs. You care about the *experience*, not just whether things technically work.

**Mindset**: You are a new user who just installed this tool. You haven't read the docs. You expect things to be intuitive, consistent, and helpful when you make mistakes. Every time you have to guess, every time two commands work differently for no reason, every time an error message doesn't tell you what to do — that's a bug.

## Arguments

- `$ARGUMENTS` - Optional: `[area-name]` to start at a specific test area. Default: start from area 1.

## Workflow

```
+-------------------------------------------------------------+
| Phase 1: Setup                                               |
|   - Find or create "UX Audit" epic in beads                  |
|   - Detect available DB targets                              |
|   - Parse $ARGUMENTS for resume point                        |
+-------------------------------------------------------------+
                            |
+-------------------------------------------------------------+
| Phase 2: Static Audit (no tmux needed)                       |
|   - Read parser_data.py directly                             |
|   - Build complete flag inventory                            |
|   - Detect short flag collisions, naming inconsistencies     |
|   - File bugs for structural problems                        |
+-------------------------------------------------------------+
                            |
+-------------------------------------------------------------+
| Phase 3: Discovery & Help Audit                              |
|   - Run --help on every command via tmux                     |
|   - Score zero-knowledge entry experience                    |
|   - Audit help text consistency and example runnability      |
+-------------------------------------------------------------+
                            |
+-------------------------------------------------------------+
| Phase 4: Error Experience Audit                              |
|   - Deliberately make mistakes                               |
|   - Score error message quality and recovery guidance        |
|   - Test flag composition and edge cases                     |
+-------------------------------------------------------------+
                            |
+-------------------------------------------------------------+
| Phase 5: Journey Testing (requires DB for some)              |
|   - Walk through complete multi-step user workflows          |
|   - Score transitions between steps                          |
|   - Evaluate "what do I do next?" at every point             |
+-------------------------------------------------------------+
                            |
+-------------------------------------------------------------+
| Phase 6: Output Consistency Audit                            |
|   - Compare error/success/table styling across commands      |
|   - Check progress indicator consistency                     |
+-------------------------------------------------------------+
                            |
+-------------------------------------------------------------+
| Phase 7: Scoring & Bug Filing                                |
|   - Compile Known Inconsistency Registry                     |
|   - Score each area on UX rubric                             |
|   - Final summary report                                     |
+-------------------------------------------------------------+
```

---

## Phase 1: Setup

### 1.1 Find or Create Epic

Search for existing epic:
```bash
bd search "UX Audit"
```

If no epic found, create one:
```bash
bd create --title="UX Audit: CLI Experience Review" --type=epic --priority=2 \
  --description="Parent epic for UX issues found by the ux-auditor skill. Each child issue represents a specific UX problem with reproduction steps and recommended fix."
```

Save the epic ID for use as `--parent` when filing bugs.

### 1.2 Detect DB Targets

Start a tmux session and check for configured targets:
```bash
python3 scripts/tmux_harness.py start --session ux
python3 scripts/tmux_harness.py send-and-wait -s ux --text "uv run rdst.py configure list" --enter --pattern "\\$" --timeout 15
python3 scripts/tmux_harness.py read -s ux --last 30
python3 scripts/tmux_harness.py kill -s ux
```

- If targets exist: journey areas 14-15 (requiring DB) are available
- If no targets: skip DB-dependent journey areas

### 1.3 Parse Arguments

If `$ARGUMENTS` is provided:
- Token = area name to start from (e.g., `help-text-consistency`)
- Skip ahead to that area in the catalog

---

## Phase 2: Static Audit

**This phase does NOT use tmux.** Read `src/lib/cli/parser_data.py` directly using the Read tool and analyze the `COMMANDS` dictionary programmatically.

### Area 1: `flag-inventory`

Read the entire `parser_data.py` file. For every command and subcommand, extract:

1. **Short flag inventory**: Build a table of every short flag (`-q`, `-f`, `-t`, etc.) and what it maps to in each command context.

   | Short Flag | Command | Long Flag | Meaning |
   |------------|---------|-----------|---------|
   | `-q` | analyze | `--query` | SQL query to analyze |
   | `-q` | query run | `--quiet` | Minimal output |
   | ... | ... | ... | ... |

   **BUG if**: Same short flag means different things in different commands (collision).

2. **Target parameter naming**: Build a table of how each command refers to the database target.

   | Command | Flag | Dest |
   |---------|------|------|
   | top | `--target` | target |
   | analyze | `--target` | target |
   | scan | `--schema` | target |
   | ... | ... | ... |

   **BUG if**: Same concept uses different flag names across commands.

3. **Machine-readable output patterns**: How each command supports non-human output.

   | Command | Flag | Choices |
   |---------|------|---------|
   | top | `--json` | (boolean) |
   | analyze | `--json` | (boolean) |
   | scan | `--output` | table, json |
   | schema export | `--format` | yaml, json |
   | ... | ... | ... |

   **BUG if**: Multiple patterns for the same concept (--json vs --format vs --output).

4. **Interactive mode patterns**: How each command handles interactive vs non-interactive.

   | Command | Flag | Default | Meaning |
   |---------|------|---------|---------|
   | ask | `--no-interactive` | interactive | Opt-out |
   | analyze | `--interactive` | non-interactive | Opt-in |
   | top | `--interactive` | non-interactive | Opt-in |
   | schema init | `--interactive` / `-i` | non-interactive | Opt-in |
   | query list | `--interactive` / `-i` | non-interactive | Opt-in |
   | init | `--interactive` | non-interactive | Opt-in |
   | ... | ... | ... | ... |

   **BUG if**: Same concept has inverted defaults across commands (opt-in vs opt-out).

5. **Confirmation/force patterns**: How commands handle destructive confirmations.

   | Command | Flag | Pattern |
   |---------|------|---------|
   | configure | `--confirm` | Confirm removal |
   | query delete | `--force` | Skip confirmation |
   | schema delete | `--force` | Skip confirmation |
   | ... | ... | ... |

   **BUG if**: Same pattern uses different flag names (`--confirm` vs `--force`).

### Area 2: `naming-audit`

Still reading `parser_data.py` statically:

1. **Subcommand verb consistency**: Build a table of what verbs each command uses for CRUD operations.

   | Operation | configure | query | agent | guard | schema |
   |-----------|-----------|-------|-------|-------|--------|
   | Create | add | add | create | create | init |
   | Read | list | list/show | list/show | list/show | show/list |
   | Update | edit | edit | - | edit | edit/annotate |
   | Delete | remove | delete/rm | delete | delete | delete |

   **BUG if**: Same operation uses different verbs across commands (`add` vs `create`, `remove` vs `delete`).

2. **Alias audit**: Check which subcommands have aliases and which don't.

   **BUG if**: Only some commands have aliases (e.g., `query rm` exists but `configure rm` doesn't, `query del` doesn't exist even though `query delete` does).

3. **Command name intuitiveness**: Score each command name on how guessable it is.

   | Command | Intuitive? | Notes |
   |---------|------------|-------|
   | configure | Yes | Standard CLI pattern |
   | top | Somewhat | Familiar to Unix users but unclear for non-Unix users |
   | analyze | Yes | Self-explanatory |
   | ask | Yes | Self-explanatory |
   | scan | Yes | Self-explanatory |
   | guard | Maybe | Need to know it's about safety policies |
   | claude | No | Only makes sense if you know Claude Code/MCP |
   | ... | ... | ... |

   **BUG if**: Command name is misleading or requires domain knowledge that help text doesn't provide.

4. **Help text first-line clarity**: For each command, read the `short_help` and `description`. Score whether a new user could understand what the command does from the first line alone.

   **BUG if**: `short_help` uses jargon without explanation, is too vague, or doesn't match what the command actually does.

---

## Phase 3: Discovery & Help Audit

**These areas use tmux.** Start a fresh session for each area.

### Session Lifecycle

```bash
# Before each area
python3 scripts/tmux_harness.py kill -s ux    # cleanup any leftover
python3 scripts/tmux_harness.py start --session ux

# After each area
python3 scripts/tmux_harness.py kill -s ux
```

Session name is always `ux`. Working directory defaults to `src/`.

### Harness Quick Reference

All commands run from the shadow workspace root (`/home/gautam/readyset/hacks/gautam/rdst/`):

| Action | Command |
|--------|---------|
| **Start session** | `python3 scripts/tmux_harness.py start --session ux` |
| **Run a command** | `python3 scripts/tmux_harness.py send-and-wait -s ux --text "uv run rdst.py version" --enter --pattern "\\$" --timeout 15` |
| **Read output** | `python3 scripts/tmux_harness.py read -s ux --last 30` |
| **Send text** | `python3 scripts/tmux_harness.py send -s ux --text "y" --enter` |
| **Send special key** | `python3 scripts/tmux_harness.py send -s ux --key C-c` |
| **Wait for pattern** | `python3 scripts/tmux_harness.py wait-for -s ux --pattern "Select.*:" --timeout 10` |
| **Wait until stable** | `python3 scripts/tmux_harness.py wait-stable -s ux --settle 2 --timeout 15` |
| **Kill session** | `python3 scripts/tmux_harness.py kill -s ux` |

### Area 3: `zero-knowledge-entry`

**Scenario**: A new user has just installed rdst. They know nothing. Can they figure out what to do?

1. Run `uv run rdst.py` with no arguments. What happens?
   - Is there a clear entry point? (menu, help, usage summary)
   - Can you tell what this tool does from the first screen?
   - Is there a suggested "start here" path?

2. Run `uv run rdst.py --help`. Score on:
   - Can you understand the tool's purpose from the description?
   - Are commands grouped logically? (e.g., getting started vs daily use vs advanced)
   - Is there a natural reading order that teaches you the workflow?
   - Are the most important commands visually prominent?

3. Run `uv run rdst.py help`. Compare with `--help`:
   - Are they the same? Different? Is one better?
   - If different, is the relationship between them clear?

**UX Rubric scoring** (score this area on all 6 dimensions):

| Dimension | Score | Notes |
|-----------|-------|-------|
| Discoverability | 0-3 | Can you find what the tool does? |
| Learnability | 0-3 | Can you figure out where to start? |
| Consistency | 0-3 | Do help and no-args agree? |
| Error Recovery | 0-3 | If you mistype, does it guide you? |
| Efficiency | 0-3 | How many steps to first useful action? |
| Transparency | 0-3 | Does it explain what it will do? |

### Area 4: `help-text-consistency`

Run `--help` on every command and subcommand. For each, check:

**Commands to test** (run all via tmux, read output for each):
```
uv run rdst.py --help
uv run rdst.py configure --help
uv run rdst.py top --help
uv run rdst.py analyze --help
uv run rdst.py ask --help
uv run rdst.py init --help
uv run rdst.py query --help
uv run rdst.py schema --help
uv run rdst.py report --help
uv run rdst.py help --help
uv run rdst.py claude --help
uv run rdst.py version --help
uv run rdst.py web --help
uv run rdst.py slack --help
uv run rdst.py agent --help
uv run rdst.py guard --help
uv run rdst.py scan --help
```

For each help page, evaluate:

1. **Description quality**: Does the first paragraph explain what this does and when to use it?
2. **Example presence**: Does it have examples? Are they useful?
3. **Flag description quality**: Are all flags described? Do descriptions explain the "why", not just the "what"?
4. **Cross-references**: Does it mention related commands? (e.g., analyze help mentioning "use top to find slow queries first")
5. **Style consistency**: Compare across all help pages:
   - Do descriptions start with a verb or noun? (Pick one — are they consistent?)
   - Are subcommand lists formatted the same way?
   - Do example sections use the same format?
   - Is capitalization consistent? (sentence case vs Title Case)

**BUG if**: Style differs between commands, descriptions are vague, examples are missing where needed, or cross-references are absent where they'd help.

### Area 5: `example-runnability`

For each command that has examples in its `--help` output:

1. **Syntactic validity**: Could the example be copy-pasted and run? (Ignoring that targets/data may not exist)
2. **Ordering**: Are examples ordered from simple to complex?
3. **Coverage**: Do examples cover the most common use cases?
4. **Placeholders**: Are placeholder values like `mydb` or `abc123` clearly marked as placeholders?

**BUG if**: Examples use wrong syntax, are in random order, miss common use cases, or use confusing placeholder values.

---

## Phase 4: Error Experience Audit

### Area 6: `missing-args-errors`

Run commands without required arguments and score the error quality:

```
uv run rdst.py analyze
uv run rdst.py ask
uv run rdst.py configure remove
uv run rdst.py configure test
uv run rdst.py configure default
uv run rdst.py configure edit
uv run rdst.py query show
uv run rdst.py query delete
uv run rdst.py scan
uv run rdst.py agent create
uv run rdst.py guard create
uv run rdst.py guard check
```

For each, score the error on:

| Criterion | 0 | 1 | 2 | 3 |
|-----------|---|---|---|---|
| Identifies what's missing | No info | Generic "missing argument" | Names the missing arg | Names arg + explains what it's for |
| Suggests fix | No suggestion | "See --help" | Shows correct syntax | Copy-pasteable example |
| Error format | Traceback / raw argparse | Custom but ugly | Clean formatted | Styled panel with clear hierarchy |

**BUG if**: Any error scores 0-1 on any criterion, or if error format is inconsistent across commands.

### Area 7: `wrong-target-errors`

Test what happens when you reference a nonexistent target:

```
uv run rdst.py top --target nonexistent_target_12345
uv run rdst.py analyze -q "SELECT 1" --target nonexistent_target_12345
uv run rdst.py ask "test" --target nonexistent_target_12345
uv run rdst.py schema show --target nonexistent_target_12345
uv run rdst.py configure test nonexistent_target_12345
uv run rdst.py configure edit nonexistent_target_12345
uv run rdst.py configure remove nonexistent_target_12345
uv run rdst.py configure default nonexistent_target_12345
```

For each, evaluate:
- Does it say the target doesn't exist?
- Does it list available targets? (fuzzy matching / "did you mean?")
- Does it suggest how to create a target?
- Is the error consistent across all commands?

**BUG if**: Any command doesn't explain what went wrong, doesn't suggest alternatives, or uses a different error format than other commands.

### Area 8: `typo-and-invalid-input`

Test misspelled commands, invalid choices, and contradictory flags:

```
uv run rdst.py analze                           # misspelled command
uv run rdst.py conifgure list                   # misspelled command
uv run rdst.py top --sort invalid               # invalid choice
uv run rdst.py scan --output xml                # invalid choice
uv run rdst.py top --source badvalue            # invalid choice
uv run rdst.py analyze -q "SELECT 1" --fast --interactive   # potentially contradictory
uv run rdst.py top --json --interactive                     # potentially contradictory
uv run rdst.py query run nonexistent_query                  # nonexistent resource
```

For each, evaluate:
- Does the error suggest the correct spelling? ("Did you mean 'analyze'?")
- For invalid choices, does it list valid options?
- For contradictory flags, does it explain the conflict or pick a sensible default?
- Is there a Python traceback visible?

**BUG if**: No "did you mean?" suggestions, valid options not shown, contradictory flags silently ignored, or traceback visible.

### Area 9: `flag-composition`

Test whether flag combinations work sensibly:

```
uv run rdst.py top --json --no-color            # redundant: JSON has no color
uv run rdst.py analyze -q "SELECT 1" --json --interactive   # conflict?
uv run rdst.py top --watch --duration 5         # conflict: continuous vs timed
uv run rdst.py scan . --analyze --dry-run       # conflict: analyze vs dry-run
uv run rdst.py scan . --check --output json     # does CI mode respect output format?
uv run rdst.py query run q1 --count 5 --duration 10  # two stop conditions
```

For each, evaluate:
- Does the tool handle the combination gracefully?
- If the flags conflict, is there a clear error? Or does one silently win?
- If both flags are valid together, does the behavior make sense?

**BUG if**: Conflicting flags are silently ignored, behavior is surprising, or error message doesn't explain the conflict.

---

## Phase 5: Journey Testing

Journey tests evaluate multi-step workflows end-to-end. The key question at every step is: **"Can you figure out what to do next without reading docs?"**

### Journey Scoring Rubric

At each step transition in a journey, score:

| Dimension | 0 | 1 | 2 | 3 |
|-----------|---|---|---|---|
| Next Step Obvious | No idea what to do | Buried in --help | Mentioned in output | Explicitly suggested with command |
| Data Carries Forward | Must re-enter everything | Some context lost | Most context preserved | Seamless — tool remembers |
| Error Recovery | Stuck, must start over | Can retry but unclear how | Clear retry path | Auto-recovers or suggests fix |

### Area 10: `journey-first-setup`

**Scenario**: New user, first time running rdst.

Steps:
1. `uv run rdst.py` — What does the tool show? Is "init" or "configure" the obvious first step?
2. `uv run rdst.py init` — Start the wizard. Observe prompts. (Ctrl-C after observing, don't complete)
   - Are prompts clear?
   - Is the order logical? (API key → DB → test connection)
   - Can you go back if you make a mistake?
3. After init, what does the tool suggest as next step?
4. `uv run rdst.py configure add` — observe the add flow (Ctrl-C after observing)
   - If you already ran init, does configure add know about it?
5. `uv run rdst.py configure list` — can you see what you configured?
6. From configure list, is the path to your first analysis obvious?

Score each transition on the journey rubric.

**BUG if**: Any transition scores 0 on "Next Step Obvious", or the overall journey requires reading docs to complete.

### Area 11: `journey-explore-help`

**Scenario**: User wants to learn the tool entirely through its help system.

Steps:
1. `uv run rdst.py --help` — read the command list
2. For each "category" of commands, can you understand the progression?
   - Getting started: `init` → `configure`
   - Core workflow: `top` → `analyze` → `ask`
   - Management: `query`, `schema`
   - Advanced: `agent`, `guard`, `scan`
3. `uv run rdst.py help "how do I get started"` — does it give useful guidance?
4. `uv run rdst.py help "what can I do"` — does it give an overview?
5. Does any command's help reference another command? (e.g., "See also: rdst top")

Score: Can you build a mental model of the tool from help alone?

**BUG if**: Help pages are isolated islands with no cross-references, or the learning path is unclear.

### Area 12: `journey-scan-codebase`

**Scenario**: User wants to scan their codebase for query issues.

Steps:
1. `uv run rdst.py scan --help` — understand the command
2. `uv run rdst.py scan .` — scan current directory (no target)
   - Does it work without a target?
   - Does it explain what it found?
   - Does it suggest what to do with the results?
3. If queries are found:
   - Does it show where they are? (file + line)
   - Does it suggest running `analyze` on them?
   - Can you do so easily? (e.g., does it output hashes you can pass to analyze?)

Score each transition on the journey rubric.

### Area 13: `journey-agent-setup`

**Scenario**: User wants to set up a data agent.

Steps:
1. `uv run rdst.py agent --help` — understand the concept
   - Does help explain what a "data agent" is?
   - Does it explain the relationship between agents and guards?
2. `uv run rdst.py guard --help` — understand guards
   - Is the relationship to agents clear?
   - Does it suggest creating a guard before an agent?
3. Attempt to create a guard: `uv run rdst.py guard create --name _test_ux_guard --require-where`
   - Is the creation flow clear?
   - Does it confirm what was created?
4. Attempt to create an agent referencing the guard (Ctrl-C if it needs a target):
   `uv run rdst.py agent create --name _test_ux_agent --guard _test_ux_guard`
   - Is the error clear if no target is provided?
5. Clean up: delete the guard and agent if created.

Score each transition on the journey rubric.

### Area 14: `journey-analyze-query` (requires DB)

**Skip if no DB targets detected in Phase 1.**

Steps:
1. `uv run rdst.py top --target <target> --duration 5` — find slow queries
   - Does the output help you pick a query to analyze?
   - Does it tell you how to analyze a specific query?
2. Pick a query hash from top output
3. `uv run rdst.py analyze --hash <hash> --target <target>` — analyze it
   - Does the output make sense?
   - Does it suggest what to do next? (apply indexes, try readyset, etc.)
4. `uv run rdst.py analyze --hash <hash> --target <target> --interactive` — try interactive
   - Is the transition to interactive mode smooth?
   - Type `exit` to leave — is exit obvious?

Score each transition on the journey rubric.

### Area 15: `journey-ask-question` (requires DB)

**Skip if no DB targets detected in Phase 1.**

Steps:
1. `uv run rdst.py ask "show tables" --target <target> --no-interactive` — basic question
   - Does the output make sense?
   - Does it explain the SQL it generated?
2. `uv run rdst.py schema --help` — learn about semantic layer
   - Does it explain why schema helps ask?
   - Does it reference `ask` in its help?
3. `uv run rdst.py schema init --target <target>` — initialize (Ctrl-C after observing)
   - Does it explain what it's doing?
   - Does it suggest using `ask` afterward?

Score each transition on the journey rubric.

---

## Phase 6: Output Consistency Audit

### Area 16: `error-styling`

Compare error output formatting across all commands tested so far. Build a table:

| Command | Error Type | Has Color | Has Icon/Prefix | Has Suggestion | Panel/Inline |
|---------|-----------|-----------|----------------|----------------|-------------|
| top (no target) | missing target | ? | ? | ? | ? |
| analyze (no query) | missing args | ? | ? | ? | ? |
| ... | ... | ... | ... | ... | ... |

**BUG if**: Errors use different formatting patterns. All errors should look and feel the same.

### Area 17: `success-styling`

Compare success confirmation output across commands:

| Command | Action | Confirmation Style |
|---------|--------|--------------------|
| configure add | target created | ? |
| query add | query saved | ? |
| guard create | guard created | ? |
| agent create | agent created | ? |
| ... | ... | ... |

**BUG if**: Some commands confirm success loudly, others are silent. Inconsistent styling.

### Area 18: `table-formatting`

Compare table output across commands:

```
uv run rdst.py configure list
uv run rdst.py query list
uv run rdst.py schema list
uv run rdst.py agent list
uv run rdst.py guard list
```

For each, check:
- Column alignment consistent?
- Header style consistent? (bold, underline, caps)
- Empty state message consistent? ("No X found" vs nothing vs different message)
- Border style consistent? (box drawing, simple dashes, none)

**BUG if**: Tables use different styling, alignment, or empty state patterns.

### Area 19: `progress-and-spinners`

For commands that take time (analyze, schema init, ask), observe:
- Do they show progress? (spinner, progress bar, status messages)
- Are progress indicators consistent across commands?
- Do they show what step they're on? ("Connecting...", "Running EXPLAIN...")
- Can you tell if it's still working or hung?

**BUG if**: Some commands show progress and others don't, or progress styles differ.

---

## Phase 7: Scoring & Bug Filing

### 7.1 Known Inconsistency Registry

Compile ALL cross-command inconsistencies into a single table:

```
+---+------------------+----------------------------+----------------------------+------------------+
| # | Category         | Commands Involved          | Inconsistency              | Recommended Fix  |
+---+------------------+----------------------------+----------------------------+------------------+
| 1 | Short flag       | analyze -q, query run -q   | -q = --query vs --quiet    | Pick one meaning |
| 2 | Target naming    | scan --schema, others --target | Different flag name     | Use --target     |
| 3 | Output format    | top --json, scan --output  | Different patterns         | Standardize      |
| 4 | Interactive      | ask --no-interactive, ...  | Inverted default           | Standardize      |
| 5 | CRUD verbs       | configure add, agent create| Different verbs for same op| Pick add or create|
| 6 | Delete confirm   | configure --confirm, ...   | --confirm vs --force       | Pick one name    |
| ... | ... | ... | ... | ... |
+---+------------------+----------------------------+----------------------------+------------------+
```

### 7.2 UX Scorecard

Score each test area on the 6-dimension UX rubric:

**UX Rubric** (0-3 per dimension, max 18 per area):

| Dimension | 0 | 1 | 2 | 3 |
|-----------|---|---|---|---|
| Discoverability | No way to find it | Buried in help | Mentioned in output | Prominently suggested |
| Learnability | Misleading | Jargon-heavy | Clear after reading help | Obvious from name |
| Consistency | Contradicts other commands | Different pattern | Mostly consistent | Perfectly consistent |
| Error Recovery | Traceback/silent | Error, no guidance | Specific guidance | Copy-pasteable fix |
| Efficiency | 4+ required flags | 2-3 flags | 1 flag | Zero-flag default works |
| Transparency | Silent operation | Final result only | Shows progress | Explains each step |

Compile the scorecard:

```
| Area                    | Disc | Learn | Consist | ErrRec | Effic | Transp | Total |
|-------------------------|------|-------|---------|--------|-------|--------|-------|
| flag-inventory          |  /3  |  /3   |   /3    |  n/a   |  n/a  |  n/a   |  /9   |
| naming-audit            |  /3  |  /3   |   /3    |  n/a   |  n/a  |  n/a   |  /9   |
| zero-knowledge-entry    |  /3  |  /3   |   /3    |  /3    |  /3   |  /3    |  /18  |
| help-text-consistency   |  /3  |  /3   |   /3    |  /3    |  /3   |  /3    |  /18  |
| example-runnability     |  /3  |  /3   |   /3    |  n/a   |  /3   |  n/a   |  /12  |
| missing-args-errors     |  n/a |  n/a  |   /3    |  /3    |  n/a  |  /3    |  /9   |
| wrong-target-errors     |  n/a |  n/a  |   /3    |  /3    |  n/a  |  /3    |  /9   |
| typo-and-invalid-input  |  n/a |  /3   |   /3    |  /3    |  n/a  |  /3    |  /12  |
| flag-composition        |  n/a |  n/a  |   /3    |  /3    |  n/a  |  /3    |  /9   |
| journey-first-setup     |  /3  |  /3   |   /3    |  /3    |  /3   |  /3    |  /18  |
| journey-explore-help    |  /3  |  /3   |   /3    |  /3    |  /3   |  /3    |  /18  |
| journey-scan-codebase   |  /3  |  /3   |   /3    |  /3    |  /3   |  /3    |  /18  |
| journey-agent-setup     |  /3  |  /3   |   /3    |  /3    |  /3   |  /3    |  /18  |
| journey-analyze-query   |  /3  |  /3   |   /3    |  /3    |  /3   |  /3    |  /18  |
| journey-ask-question    |  /3  |  /3   |   /3    |  /3    |  /3   |  /3    |  /18  |
| error-styling           |  n/a |  n/a  |   /3    |  /3    |  n/a  |  n/a   |  /6   |
| success-styling         |  n/a |  n/a  |   /3    |  n/a   |  n/a  |  /3    |  /6   |
| table-formatting        |  n/a |  n/a  |   /3    |  n/a   |  n/a  |  n/a   |  /3   |
| progress-and-spinners   |  n/a |  n/a  |   /3    |  n/a   |  n/a  |  /3    |  /6   |
```

### 7.3 Final Report

Output a summary that includes:
1. **Overall UX rating** (sum of all scores / max possible)
2. **Top 5 worst UX problems** (link to bug IDs)
3. **Top 3 UX strengths** (what the CLI does well)
4. **Known Inconsistency Registry** (the full table from 7.1)
5. **Recommended priority order** for fixes (highest-impact, lowest-effort first)

---

## Bug Reporting

### Before Filing: Dedup Check

Before creating any bug, search for existing issues:
```bash
bd search "<key phrase from the bug>"
```

If a similar issue exists, skip filing. Use your judgment — if the existing issue is about a different aspect of the same problem, file separately.

### Filing a Bug

```bash
bd create \
  --title="[UX] <concise title: what's wrong>" \
  --type=bug \
  --priority=<severity> \
  --parent=<epic-id> \
  --description="<full description>"
```

All UX bugs use the `[UX]` prefix in their title.

### Bug Description Template

Write the description as a single string with this structure:

```
**UX dimension**: <which rubric dimension this affects>

**What happened**: <what the user experiences>

**Expected UX**: <what a user would expect instead>

**Impact**: <who is affected and how often>

**Reproduction**:
1. Run: `uv run rdst.py <command>`
2. Observe: <what appears>

**Captured output**:
```
<paste the relevant output lines>
```

**Recommended fix**: <specific suggestion for how to fix it>
```

### Severity Guide

| Priority | When to use |
|----------|-------------|
| 1 (P1) | User cannot complete a core task due to UX issue |
| 2 (P2) | Confusing UX that will trip up most users (naming collisions, misleading errors) |
| 3 (P3) | Inconsistencies that experienced users can work around |
| 4 (P4) | Minor polish, style nitpicks, suggestions for improvement |

---

## Guidelines

### Autonomy
- **Do NOT ask the user for permission** between test areas. Just keep going.
- **Do NOT stop** after finding a bug. File it and continue testing.
- Report progress after completing each area (one line: area name, score, bugs filed).
- Only stop when you run out of areas or hit context limits.

### Non-Destructive Testing
- **Never modify real user configuration.** If you need to test config changes, use `_test_ux_` prefixed resources.
- **Never delete real queries or schemas.** Only delete resources you created.
- **Always clean up** test resources at the end of each area.
- **Never run `rdst init`** for real — it overwrites config. Test it by observing the prompts then Ctrl-C.

### Quality Over Speed
- **Read every line of output.** Don't skim.
- **Compare across commands** — UX consistency requires cross-referencing.
- Take extra harness reads if the first read didn't capture everything.
- The static audit (Phase 2) is the most important phase — be exhaustive.

### Session Hygiene
- Kill the tmux session between every area to avoid state bleed.
- If a command hangs, Ctrl-C it, read the output, file a bug about the hang, then kill and restart the session.
- If a harness action fails (session doesn't exist), start a new one.

### Progress Tracking

After each area, output a line like:
```
[area-name] DONE — score: 14/18, 2 bugs filed (rdst-xxx, rdst-yyy)
```
or:
```
[area-name] DONE — score: 16/18, clean
```

After completing all areas, output the full scorecard and Known Inconsistency Registry.

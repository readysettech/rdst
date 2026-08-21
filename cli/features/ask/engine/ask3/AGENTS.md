# Ask3 Engine - NL-to-SQL Orchestrator

**Architecture**: Hybrid Linear + Agent flow with pure-function phases

## FLOW

```
SCHEMA → CLARIFY → GENERATE ↔ VALIDATE → EXECUTE → [AGENT?] → PRESENT
                              ↑____________↓ (retry loop)
```

## CORE CONCEPTS

Canonical interactive `AskService` returns up to three material clarification
questions in one event. Web presents them as a sequential wizard and submits the
answers together. Keep questions in business language. Never ask users to choose a
table, column, join, schema element, or SQL construct. Options with duplicate wording
or identical SQL effects are not useful clarifications and must not reach the UI.

### Ask3Context (Single Source of Truth)
All state lives in `Ask3Context`. Phases read inputs and write outputs to it.

```python
# context.py - DO NOT add parallel state objects
ctx = Ask3Context(question="...", target="mydb")
ctx = load_schema(ctx, ...)    # Phase mutates and returns ctx
ctx = generate_sql(ctx, ...)   # Same ctx flows through
```

### Phases (Pure Functions)
Each phase is a pure function: `(ctx, presenter, deps) -> ctx`

```python
# phases/generate.py
def generate_sql(ctx: Ask3Context, presenter: Ask3Presenter, llm: LLMManager) -> Ask3Context:
    # Read from ctx
    schema = ctx.schema_formatted
    question = ctx.refined_question or ctx.question

    # Do work
    result = llm.call(...)

    # Write to ctx
    ctx.sql = result['sql']
    ctx.generation_response = result  # For diagnostics and escalation
    return ctx
```

### Escalation to Agent
The internal legacy engine can escalate to `Ask3Agent` when:
- Zero rows returned
- Low LLM confidence

```python
# engine.py - escalation decision
should_escalate, reason = escalation.should_escalate(ctx)
if should_escalate:
    ctx = self._run_agent(ctx, reason)
```

## FILES

| File | Purpose |
|------|---------|
| `engine.py` | Main orchestrator, retry loops |
| `context.py` | `Ask3Context` dataclass |
| `agent.py` | Tool-calling agent for complex queries |
| `agent_context.py` | Agent-specific context |
| `escalation.py` | Escalation decision logic |
| `presenter.py` | All user-facing output |
| `phases/*.py` | Individual phase implementations |

## RULES

### Phase Functions Must:
1. Take `ctx` as first arg, return modified `ctx`
2. Never hold state between calls (pure function)
3. Write status updates to `ctx.phase = 'phase_name'`
4. Use `ctx.mark_error()` for failures, never raise

### Context Must:
1. Be the ONLY place session state lives
2. Track all LLM calls via `ctx.add_llm_call()`
3. Support serialization (`to_dict()` / `from_dict()`)

### Presenter Must:
1. Handle ALL user-facing output (print, rich, etc.)
2. Be injected, never instantiated inside phases
3. Support `verbose` mode toggle

## ANTI-PATTERNS

- **NO phase holding state** - All state in Ask3Context
- **NO direct print()** - Use presenter methods
- **NO bypassing context** - Don't pass raw data between phases
- **NO exception raising** - Use `ctx.mark_error()` pattern
- **NO merging phases** - Keep them atomic and testable

## SCHEMA SELECTION

Runtime Ask paths send the complete loaded schema to generation. If no semantic layer
exists, Ask runs the structural introspector used by `rdst schema init`, persists the
result, and continues. Do not add schema filtering back without execution-backed
evidence that it improves accuracy and a model context that cannot hold the complete
schema.

Semantic schema serialization is adaptive. Build both complete representations and
use compact v2 only when it is strictly more than 15% smaller than verbose by
character count. Exactly 15% stays verbose. Keep the selected format and both measured
sizes on `Ask3Context`; never use this rule to drop tables or semantic fields.

Do not estimate model tokens from schema characters or maintain a local context-window
table. Let the provider reject an oversized request, then retry the lossless compact
form once. The Anthropic provider separately enforces its documented serialized
request-body limit. Do not retry unrelated provider errors.

## TESTING

Phases are pure functions - test by creating context and asserting output:

```python
def test_generate_sql():
    ctx = Ask3Context(question="count users", target="test")
    ctx.schema_formatted = "CREATE TABLE users (id INT)"

    ctx = generate_sql(ctx, mock_presenter, mock_llm)

    assert ctx.sql is not None
    assert "SELECT" in ctx.sql
```

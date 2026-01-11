# rdst agent - Phase 1 & 2 TODO

## Pending Manual Tests

- [ ] `rdst agent serve --name NAME --port PORT` - Test HTTP API server
- [ ] `rdst agent slack --name NAME` - Test Slack bot integration

## Phase 3+ Ideas

- [ ] Session persistence across chat restarts
- [ ] Semantic layer integration with agents
- [ ] Institutional memory (cross-session learning)
- [ ] Query caching

## UX Improvements

- [ ] **Visual feedback during processing** - When `rdst ask` or `rdst agent chat` is generating a query or executing SQL, show a spinner or progress indicator so users know work is happening in the background (currently just shows "Thinking..." with no animation)

## Completed (Phase 2)

- [x] **Conversation history for chat mode** - Agent chat now maintains history of Q&A exchanges, enabling follow-up questions like "break that down by month". Use `clear` to reset, `history` to view.
- [x] `lib/agent/conversation.py` - ConversationTurn, ConversationSession
- [x] `lib/engines/ask3/context.py` - Added conversation_context field
- [x] `lib/engines/ask3/engine.py` - Added conversation_context parameter
- [x] `lib/engines/ask3/phases/generate.py` - Injects context into LLM prompt
- [x] `lib/agent/runtime.py` - Added ask_with_history() method
- [x] `lib/cli/agent_command.py` - Updated _chat() with session tracking
- [x] Unit tests (95 tests passing)

## Completed (Phase 1)

- [x] `lib/agent/__init__.py` - Package exports
- [x] `lib/agent/config.py` - AgentConfig, SafetyConfig, RestrictionsConfig
- [x] `lib/agent/manager.py` - AgentManager with CRUD operations
- [x] `lib/agent/runtime.py` - AgentRuntime with safety enforcement
- [x] `lib/agent/http_server.py` - HTTP API using aiohttp
- [x] `lib/cli/agent_command.py` - CLI command implementations
- [x] `rdst.py` - Added agent subparser
- [x] `mcp_server.py` - Added agent tools (list, ask, create)
- [x] `pyproject.toml` - Added aiohttp optional dependency
- [x] Manual test: `rdst agent create`
- [x] Manual test: `rdst agent list`
- [x] Manual test: `rdst agent show`
- [x] Manual test: `rdst agent delete`
- [x] Manual test: `rdst agent chat`

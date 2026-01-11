# rdst agent - Phase 1 MVP TODO

## Pending Manual Tests

- [ ] `rdst agent serve --name NAME --port PORT` - Test HTTP API server
- [ ] `rdst agent slack --name NAME` - Test Slack bot integration

## Phase 2 Improvements

- [ ] **Conversation history for chat mode** - Currently each question in `rdst agent chat` is independent with no memory of prior Q&A. Implement conversation history to enable follow-up questions like "break that down by month" or "same query but for last year".

- [ ] **Address chat mode naming/documentation** - The "chat" command implies conversational memory which doesn't exist yet. Either:
  - Rename to `query` or `repl` to set correct expectations
  - Document the limitation clearly in help text
  - Keep as-is once conversation history is implemented

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
- [x] Unit tests (77 tests passing)
- [x] Manual test: `rdst agent create`
- [x] Manual test: `rdst agent list`
- [x] Manual test: `rdst agent show`
- [x] Manual test: `rdst agent delete`
- [x] Manual test: `rdst agent chat`

// Agent Types
// ---------------------------------------------------------------------------
//
// REST-backed shapes come from the generated OpenAPI types, mirroring the
// AgentConfig / chat pipeline in rdst/features/agent/. Agents are scoped,
// read-only query assistants bound to a target (and optionally a guard) that
// answer natural-language questions over the database.

import type { components } from '../lib/api.generated';

export type AgentSummary = components['schemas']['AgentSummary'];
export type AgentListResponse = components['schemas']['AgentListResponse'];
export type AgentDetail = components['schemas']['AgentDetail'];
export type AgentSafetyModel = components['schemas']['AgentSafetyModel'];
export type AgentRestrictionsModel = components['schemas']['AgentRestrictionsModel'];
export type AgentAskResponse = components['schemas']['AgentAskResponse'];
export type AgentAskRequest = components['schemas']['AgentAskRequest'];
export type AgentCreateRequest = components['schemas']['AgentCreateRequest'];
export type AgentWriteResponse = components['schemas']['AgentWriteResponse'];

export type ChatEvent = components['schemas']['ChatEvent'];
export type ChatStatusEvent = components['schemas']['ChatStatusEvent'];
export type ChatThinkingEvent = components['schemas']['ChatThinkingEvent'];
export type ChatToolCallEvent = components['schemas']['ChatToolCallEvent'];
export type ChatToolResultEvent = components['schemas']['ChatToolResultEvent'];
export type ChatResponseEvent = components['schemas']['ChatResponseEvent'];
export type ChatCompleteEvent = components['schemas']['ChatCompleteEvent'];
export type ChatErrorEvent = components['schemas']['ChatErrorEvent'];

export type ChatSessionCreateResponse =
  components['schemas']['ChatSessionCreateResponse'];
export type ChatSessionInfo = components['schemas']['ChatSessionInfo'];
export type ChatHistoryMessage = components['schemas']['ChatHistoryMessage'];

// ---------------------------------------------------------------------------
// Query-tool result shapes
// ---------------------------------------------------------------------------
//
// The backend types `tool_result.data` as a free-form record. These narrow it
// for the two known tools so the transcript can render SQL + result tables.

export interface QueryToolData {
  sql: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  execution_time_ms: number;
  truncated: boolean;
  query_hash?: string | null;
  query_tag?: string | null;
}

export interface SchemaToolData {
  tables: unknown[];
  source: string;
}

// ---------------------------------------------------------------------------
// Transcript items (UI state)
// ---------------------------------------------------------------------------
//
// The transcript interleaves the user's own messages with every ChatEvent the
// stream emits. User turns are synthesized locally; the rest are the raw SSE
// events tagged with a stable id for React keys.

export interface UserTranscriptItem {
  kind: 'user';
  id: string;
  text: string;
}

export interface EventTranscriptItem {
  kind: 'event';
  id: string;
  event: ChatEvent;
}

export type TranscriptItem = UserTranscriptItem | EventTranscriptItem;

export type ChatState = 'idle' | 'streaming' | 'error';

import { useCallback, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import { useTargetSwitchLock } from './targetSwitchLock';
import { throwIfApiError } from './httpError';
import type {
  AgentCreateRequest,
  AgentListResponse,
  AgentWriteResponse,
  ChatEvent,
  ChatSessionCreateResponse,
  ChatState,
  TranscriptItem,
} from '../types/agents';

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

const AGENTS_KEY = ['agents'] as const;

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function fetchAgents(): Promise<AgentListResponse> {
  const { data, error, response } = await api.GET('/api/agents');
  throwIfApiError(response, error, 'Failed to fetch agents');
  if (!data) throw new Error('Missing response body');
  return data;
}

export async function createAgent(body: AgentCreateRequest): Promise<AgentWriteResponse> {
  const { data, error, response } = await api.POST('/api/agents', { body });
  throwIfApiError(response, error, 'Failed to create agent');
  if (!data) throw new Error('Missing response body');
  return data;
}

export async function deleteAgent(name: string): Promise<AgentWriteResponse> {
  const { data, error, response } = await api.DELETE('/api/agents/{name}', {
    params: { path: { name } },
  });
  throwIfApiError(response, error, 'Failed to delete agent');
  if (!data) throw new Error('Missing response body');
  return data;
}

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useAgentsList() {
  return useQuery({
    queryKey: AGENTS_KEY,
    queryFn: fetchAgents,
    staleTime: 30_000,
  });
}

// ---------------------------------------------------------------------------
// Mutation hooks
// ---------------------------------------------------------------------------

export function useCreateAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AgentCreateRequest) => createAgent(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: AGENTS_KEY });
    },
  });
}

export function useDeleteAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteAgent(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: AGENTS_KEY });
    },
  });
}

// ---------------------------------------------------------------------------
// Chat hook (manual SSE reader)
// ---------------------------------------------------------------------------
//
// Chat sessions live in memory server-side and expire after 30 min idle. The
// hook creates a session lazily on the first send, appends the user's turn and
// every streamed ChatEvent to a single transcript array, and transparently
// re-creates a session + retries once when a message hits a 404 (expired).

let transcriptCounter = 0;
function nextId(): string {
  transcriptCounter += 1;
  return `t${transcriptCounter}`;
}

export interface UseAgentChatReturn {
  transcript: TranscriptItem[];
  state: ChatState;
  sessionId: string | null;
  messageCount: number;
  /** Live progress line ("Thinking...") — set while waiting on the agent, null otherwise. */
  statusMessage: string | null;
  send: (message: string) => Promise<void>;
  clearSession: () => Promise<void>;
}

async function createChatSession(agentName: string): Promise<ChatSessionCreateResponse> {
  const { data, error, response } = await api.POST(
    '/api/agents/{name}/chat/sessions',
    { params: { path: { name: agentName } } },
  );
  throwIfApiError(response, error, 'Failed to start chat session');
  if (!data) throw new Error('Missing response body');
  return data;
}

export function useAgentChat(agentName: string | null): UseAgentChatReturn {
  const [transcript, setTranscript] = useState<TranscriptItem[]>([]);
  const [state, setState] = useState<ChatState>('idle');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messageCount, setMessageCount] = useState(0);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const sessionIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useTargetSwitchLock('agent-chat', state === 'streaming');

  const appendEvent = useCallback((event: ChatEvent) => {
    setTranscript((prev) => [...prev, { kind: 'event', id: nextId(), event }]);
  }, []);

  // Streams one message against an existing session. Returns 'expired' when the
  // session is gone (HTTP 404) so the caller can recreate and retry.
  const streamMessage = useCallback(
    async (session: string, message: string): Promise<'ok' | 'expired'> => {
      const controller = new AbortController();
      abortRef.current = controller;

      const response = await fetch(
        `/api/agents/chat/sessions/${encodeURIComponent(session)}/message`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message }),
          signal: controller.signal,
        },
      );

      if (response.status === 404) return 'expired';
      if (!response.ok) {
        const errorText = await response.text().catch(() => '');
        throw new Error(`HTTP error ${response.status}: ${errorText}`);
      }
      if (!response.body) throw new Error('No response body');

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith('data:')) continue;

          const dataStr = trimmed.substring(5).trim();
          let event: ChatEvent;
          try {
            event = JSON.parse(dataStr) as ChatEvent;
          } catch (e) {
            if (e instanceof SyntaxError) continue;
            throw e;
          }

          switch (event.type) {
            case 'status':
              // Ephemeral progress indicator — not part of the transcript.
              // Cleared by the next substantive event or when the stream ends.
              setStatusMessage(event.message);
              break;
            case 'thinking':
            case 'tool_call':
            case 'tool_result':
            case 'response':
            case 'error':
            case 'complete':
              setStatusMessage(null);
              appendEvent(event);
              break;
            default: {
              // Exhaustiveness guard: adding a variant to ChatEvent without
              // handling it here fails tsc.
              const _exhaustive: never = event;
              void _exhaustive;
              break;
            }
          }
        }
      }
      return 'ok';
    },
    [appendEvent],
  );

  const send = useCallback(
    async (message: string) => {
      const trimmed = message.trim();
      if (!agentName || !trimmed || state === 'streaming') return;

      setTranscript((prev) => [...prev, { kind: 'user', id: nextId(), text: trimmed }]);
      setMessageCount((prev) => prev + 1);
      setState('streaming');

      try {
        let session = sessionIdRef.current;
        if (!session) {
          const created = await createChatSession(agentName);
          session = created.session_id;
          sessionIdRef.current = session;
          setSessionId(session);
        }

        let outcome = await streamMessage(session, trimmed);
        if (outcome === 'expired') {
          // Session died server-side (idle timeout / restart). Recreate once.
          const created = await createChatSession(agentName);
          session = created.session_id;
          sessionIdRef.current = session;
          setSessionId(session);
          outcome = await streamMessage(session, trimmed);
          if (outcome === 'expired') {
            throw new Error('Chat session expired and could not be recreated.');
          }
        }
        setState('idle');
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') {
          setState('idle');
          return;
        }
        const messageText = err instanceof Error ? err.message : 'An error occurred';
        appendEvent({ type: 'error', message: messageText });
        setState('error');
      } finally {
        abortRef.current = null;
        setStatusMessage(null);
      }
    },
    [agentName, state, streamMessage, appendEvent],
  );

  const clearSession = useCallback(async () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    const session = sessionIdRef.current;
    sessionIdRef.current = null;
    setSessionId(null);
    setTranscript([]);
    setMessageCount(0);
    setState('idle');
    setStatusMessage(null);
    if (session) {
      try {
        await api.DELETE('/api/agents/chat/sessions/{session_id}', {
          params: { path: { session_id: session } },
        });
      } catch {
        // Best-effort cleanup; the server also reaps idle sessions.
      }
    }
  }, []);

  return { transcript, state, sessionId, messageCount, statusMessage, send, clearSession };
}

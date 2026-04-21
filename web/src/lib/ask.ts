import { useState, useCallback, useRef } from 'react';
import { useTargetSwitchLock } from './targetSwitchLock';
import type { components } from './api.generated';

// Ask API types
export interface AskRequest {
  question: string;
  target?: string;
  dry_run?: boolean;
  timeout?: number;
  agent_mode?: boolean;
  // For resuming after clarification
  session_id?: string;
  selected_interpretation_id?: number;
  clarification_answers?: Record<string, string>;
}

// SSE event types are derived from the backend-generated discriminated union.
// Backend source of truth: rdst/features/ask/events.py (AskEvent).
type AskEvent = components['schemas']['AskEvent'];
export type AskEventType = AskEvent['type'];

export type AskInterpretation = components['schemas']['AskInterpretation'];
export type AskClarificationQuestion =
  components['schemas']['AskClarificationQuestion'];

export type AskStatusEvent = Extract<AskEvent, { type: 'status' }>;
export type AskSchemaLoadedEvent = Extract<AskEvent, { type: 'schema_loaded' }>;
export type AskClarificationNeededEvent = Extract<
  AskEvent,
  { type: 'clarification_needed' }
>;
export type AskSqlGeneratedEvent = Extract<AskEvent, { type: 'sql_generated' }>;
// Backend emits `rows: list` (untyped); refine to row arrays for consumers.
export type AskResultEvent = Omit<
  Extract<AskEvent, { type: 'result' }>,
  'rows'
> & { rows: unknown[][] };
export type AskErrorEvent = Extract<AskEvent, { type: 'error' }>;

export type AskState =
  | 'idle'
  | 'loading'
  | 'clarification_needed'
  | 'generating'
  | 'complete'
  | 'error';

export interface UseAskReturn {
  ask: (request: AskRequest) => Promise<void>;
  resumeWithAnswers: (answers: Record<string, string>) => Promise<void>;
  state: AskState;
  status: AskStatusEvent | undefined;
  schemaLoaded: AskSchemaLoadedEvent | undefined;
  clarification: AskClarificationNeededEvent | undefined;
  sqlGenerated: AskSqlGeneratedEvent | undefined;
  result: AskResultEvent | undefined;
  error: AskErrorEvent | undefined;
  reset: () => void;
}

export function useAsk(): UseAskReturn {
  const [state, setState] = useState<AskState>('idle');
  const [status, setStatus] = useState<AskStatusEvent | undefined>(undefined);
  const [schemaLoaded, setSchemaLoaded] = useState<AskSchemaLoadedEvent | undefined>(undefined);
  const [clarification, setClarification] = useState<AskClarificationNeededEvent | undefined>(undefined);
  const [sqlGenerated, setSqlGenerated] = useState<AskSqlGeneratedEvent | undefined>(undefined);
  const [result, setResult] = useState<AskResultEvent | undefined>(undefined);
  const [error, setError] = useState<AskErrorEvent | undefined>(undefined);

  const abortControllerRef = useRef<AbortController | null>(null);
  const currentRequestRef = useRef<AskRequest | null>(null);
  useTargetSwitchLock(
    'ask',
    state === 'loading' || state === 'generating' || state === 'clarification_needed'
  );

  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('idle');
    setStatus(undefined);
    setSchemaLoaded(undefined);
    setClarification(undefined);
    setSqlGenerated(undefined);
    setResult(undefined);
    setError(undefined);
    currentRequestRef.current = null;
  }, []);

  const processStream = useCallback(async (request: AskRequest) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;
    currentRequestRef.current = request;

    setState('loading');
    setStatus(undefined);
    setClarification(undefined);
    setSqlGenerated(undefined);
    setResult(undefined);
    setError(undefined);

    try {
      const response = await fetch('/api/ask', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(request),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`HTTP error! status: ${response.status}, body: ${errorText}`);
      }

      if (!response.body) {
        throw new Error('No response body');
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';

      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          break;
        }

        const chunk = decoder.decode(value, { stream: true });
        buffer += chunk;
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();

          if (!trimmed) {
            currentEvent = '';
            continue;
          }

          if (trimmed.startsWith('event:')) {
            currentEvent = trimmed.substring(6).trim();
          } else if (trimmed.startsWith('data:')) {
            const dataStr = trimmed.substring(5).trim();

            try {
              const data = JSON.parse(dataStr);

              const eventType = currentEvent as AskEventType;
              switch (eventType) {
                case 'status':
                  setStatus(data as AskStatusEvent);
                  if (data.phase === 'generate') {
                    setState('generating');
                  }
                  break;

                case 'schema_loaded':
                  setSchemaLoaded(data as AskSchemaLoadedEvent);
                  break;

                case 'clarification_needed':
                  setClarification(data as AskClarificationNeededEvent);
                  setState('clarification_needed');
                  break;

                case 'sql_generated':
                  setSqlGenerated(data as AskSqlGeneratedEvent);
                  break;

                case 'result':
                  setResult(data as AskResultEvent);
                  setState('complete');
                  break;

                case 'error':
                  setError(data as AskErrorEvent);
                  setState('error');
                  break;

                default: {
                  // Exhaustiveness guard: if AskEventType gains a variant that
                  // isn't handled above, `eventType` narrows to that literal
                  // inside this branch and the assignment to `never` fails
                  // tsc. Do NOT replace with `as never` — that defeats the
                  // check.
                  const _exhaustive: never = eventType;
                  console.warn('[Ask SSE] Unknown event type (ignored):', currentEvent, data);
                  void _exhaustive;
                  break;
                }
              }
            } catch (e) {
              console.error('[Ask SSE] Failed to parse JSON:', e);
            }
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      const errorMessage = err instanceof Error ? err.message : 'An error occurred';
      setError({ type: 'error', message: errorMessage, phase: null });
      setState('error');
    } finally {
      abortControllerRef.current = null;
    }
  }, []);

  const ask = useCallback(async (request: AskRequest) => {
    await processStream(request);
  }, [processStream]);

  const resumeWithAnswers = useCallback(async (answers: Record<string, string>) => {
    if (!clarification?.session_id || !currentRequestRef.current) {
      console.error('No session to resume');
      return;
    }

    const resumeRequest: AskRequest = {
      ...currentRequestRef.current,
      session_id: clarification.session_id,
      clarification_answers: answers,
    };

    await processStream(resumeRequest);
  }, [clarification, processStream]);

  return {
    ask,
    resumeWithAnswers,
    state,
    status,
    schemaLoaded,
    clarification,
    sqlGenerated,
    result,
    error,
    reset,
  };
}

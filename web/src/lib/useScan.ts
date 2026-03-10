/**
 * Hook for Scan SSE streaming
 */

import { useState, useCallback, useRef } from 'react';
import { useTargetSwitchLock } from './targetSwitchLock';
import type {
  ScanState,
  ScanPhase,
  ScanFile,
  ScanQuery,
  ScanSummary,
  ScanStatusEventData,
  ScanFilesFoundEventData,
  ScanProgressEventData,
  ScanQueryResultEventData,
  ScanCompleteEventData,
  ScanErrorEventData,
} from '../types/scan';

export interface UseScanOptions {
  analyze?: boolean;
  shallow?: boolean;
  dry_run?: boolean;
  diff?: string;
  check?: boolean;
  warn_threshold?: number;
  fail_threshold?: number;
  file_pattern?: string;
  nosave?: boolean;
}

interface PhaseProgress {
  current: number;
  total: number;
  message: string;
}

interface UseScanReturn {
  startScan: (target: string, directory: string, options?: UseScanOptions) => void;
  cancel: () => void;
  reset: () => void;

  state: ScanState;
  phase: ScanPhase | null;
  phaseProgress: PhaseProgress | null;
  statusMessage: string | null;
  files: ScanFile[];
  queries: ScanQuery[];
  summary: ScanSummary | null;
  error: string | null;
}

export function useScan(): UseScanReturn {
  const [state, setState] = useState<ScanState>('idle');
  const [phase, setPhase] = useState<ScanPhase | null>(null);
  const [phaseProgress, setPhaseProgress] = useState<PhaseProgress | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [files, setFiles] = useState<ScanFile[]>([]);
  const [queries, setQueries] = useState<ScanQuery[]>([]);
  const [summary, setSummary] = useState<ScanSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);
  const queryBufferRef = useRef<ScanQuery[]>([]);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useTargetSwitchLock('scan', state === 'scanning');

  // Flush buffered queries into state (batched to avoid O(n^2) array copies)
  const flushQueryBuffer = useCallback(() => {
    flushTimerRef.current = null;
    const batch = queryBufferRef.current;
    if (batch.length === 0) return;
    queryBufferRef.current = [];
    setQueries((prev) => [...prev, ...batch]);
  }, []);

  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    queryBufferRef.current = [];
    setState('idle');
    setPhase(null);
    setPhaseProgress(null);
    setStatusMessage(null);
    setFiles([]);
    setQueries([]);
    setSummary(null);
    setError(null);
  }, []);

  const cancel = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    if (state === 'scanning') {
      setState('idle');
    }
  }, [state]);

  const startScan = useCallback(
    (target: string, directory: string, options?: UseScanOptions) => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      const controller = new AbortController();
      abortControllerRef.current = controller;

      setState('scanning');
      setPhase(null);
      setPhaseProgress(null);
      setStatusMessage(null);
      setFiles([]);
      setQueries([]);
      setSummary(null);
      setError(null);

      const streamSSE = async () => {
        try {
          const body = {
            target,
            directory,
            analyze: options?.analyze ?? false,
            shallow: options?.shallow ?? false,
            dry_run: options?.dry_run ?? false,
            diff: options?.diff || null,
            check: options?.check ?? false,
            warn_threshold: options?.warn_threshold ?? 60,
            fail_threshold: options?.fail_threshold ?? 40,
            file_pattern: options?.file_pattern || null,
            nosave: options?.nosave ?? false,
          };

          const response = await fetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            signal: controller.signal,
          });

          if (!response.ok) {
            const errorText = await response.text();
            throw new Error(
              `HTTP error! status: ${response.status}, body: ${errorText}`
            );
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
              setState((prev) => (prev === 'scanning' ? 'complete' : prev));
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

                  switch (currentEvent) {
                    case 'status': {
                      const statusData = data as ScanStatusEventData;
                      setPhase(statusData.phase);
                      setStatusMessage(statusData.message);
                      setPhaseProgress(null);
                      break;
                    }

                    case 'files_found': {
                      const filesData = data as ScanFilesFoundEventData;
                      setFiles(filesData.files);
                      break;
                    }

                    case 'progress': {
                      const progressData = data as ScanProgressEventData;
                      setPhase(progressData.phase);
                      setPhaseProgress({
                        current: progressData.current,
                        total: progressData.total,
                        message: progressData.message,
                      });
                      break;
                    }

                    case 'query_result': {
                      const queryData = data as ScanQueryResultEventData;
                      queryBufferRef.current.push(queryData.query);
                      if (!flushTimerRef.current) {
                        flushTimerRef.current = setTimeout(flushQueryBuffer, 50);
                      }
                      break;
                    }

                    case 'registry': {
                      // Registry info will be in the complete summary
                      break;
                    }

                    case 'complete': {
                      const completeData = data as ScanCompleteEventData;
                      // Flush any buffered queries before completing
                      if (flushTimerRef.current) {
                        clearTimeout(flushTimerRef.current);
                        flushTimerRef.current = null;
                      }
                      if (queryBufferRef.current.length > 0) {
                        const remaining = queryBufferRef.current;
                        queryBufferRef.current = [];
                        setQueries((prev) => [...prev, ...remaining]);
                      }
                      setSummary(completeData.summary);
                      setState('complete');
                      break;
                    }

                    case 'error': {
                      const errorData = data as ScanErrorEventData;
                      setError(errorData.message);
                      setState('error');
                      break;
                    }

                    default:
                      console.warn(
                        '[Scan SSE] Unknown event type:',
                        currentEvent,
                        data
                      );
                      break;
                  }
                } catch (e) {
                  console.error('[Scan SSE] Failed to parse JSON:', e);
                }
              }
            }
          }
        } catch (err: unknown) {
          if (err instanceof Error && err.name === 'AbortError') {
            return;
          }
          const errorMessage =
            err instanceof Error ? err.message : 'An error occurred';
          setError(errorMessage);
          setState('error');
        } finally {
          abortControllerRef.current = null;
        }
      };

      streamSSE();
    },
    []
  );

  return {
    startScan,
    cancel,
    reset,
    state,
    phase,
    phaseProgress,
    statusMessage,
    files,
    queries,
    summary,
    error,
  };
}

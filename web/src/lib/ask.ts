import { useCallback, useEffect, useRef, useState } from 'react'
import type { components } from './api.generated'
import { normalizeHttpError, normalizeSseError } from './errorContract'
import { useTargetSwitchLock } from './targetSwitchLock'

export interface AskRequest {
  question: string
  target?: string
  dry_run?: boolean
  timeout?: number
  session_id?: string
  selected_interpretation_id?: number
  clarification_answers?: Record<string, string>
}

type AskEvent = components['schemas']['AskEvent']
export type AskEventType = AskEvent['type']

export type AskInterpretation = components['schemas']['AskInterpretation']
export type AskClarificationQuestion =
  components['schemas']['AskClarificationQuestion']
export type AskStatusEvent = Extract<AskEvent, { type: 'status' }>
export type AskSchemaLoadedEvent = Extract<AskEvent, { type: 'schema_loaded' }>
export type AskClarificationNeededEvent = Extract<
  AskEvent,
  { type: 'clarification_needed' }
>
export type AskSqlGeneratedEvent = Extract<AskEvent, { type: 'sql_generated' }>
export type AskResultEvent = Omit<
  Extract<AskEvent, { type: 'result' }>,
  'rows'
> & { rows: unknown[][] }
export type AskErrorEvent = Extract<AskEvent, { type: 'error' }> & {
  code?: string
  category?: string
  target?: string
}

export type AskState =
  | 'idle'
  | 'loading'
  | 'clarification_needed'
  | 'generating'
  | 'complete'
  | 'cancelled'
  | 'error'

export interface UseAskReturn {
  ask: (request: AskRequest) => Promise<void>
  resumeWithAnswers: (answers: Record<string, string>) => Promise<void>
  cancel: () => void
  state: AskState
  status: AskStatusEvent | undefined
  schemaLoaded: AskSchemaLoadedEvent | undefined
  clarification: AskClarificationNeededEvent | undefined
  sqlGenerated: AskSqlGeneratedEvent | undefined
  result: AskResultEvent | undefined
  error: AskErrorEvent | undefined
  reset: () => void
}

type SseFrame = {
  event: string
  data: string
}

function extractSseFrames(buffer: string, flush = false) {
  const normalized = buffer.replace(/\r\n/g, '\n')
  const chunks = normalized.split('\n\n')
  const remainder = flush ? '' : (chunks.pop() ?? '')
  const frames: SseFrame[] = []

  for (const chunk of chunks) {
    let event = ''
    const data: string[] = []
    for (const line of chunk.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
    }
    if (event && data.length > 0) frames.push({ event, data: data.join('\n') })
  }

  return { frames, remainder }
}

export function useAsk(): UseAskReturn {
  const [state, setState] = useState<AskState>('idle')
  const [status, setStatus] = useState<AskStatusEvent>()
  const [schemaLoaded, setSchemaLoaded] = useState<AskSchemaLoadedEvent>()
  const [clarification, setClarification] =
    useState<AskClarificationNeededEvent>()
  const [sqlGenerated, setSqlGenerated] = useState<AskSqlGeneratedEvent>()
  const [result, setResult] = useState<AskResultEvent>()
  const [error, setError] = useState<AskErrorEvent>()

  const abortControllerRef = useRef<AbortController | null>(null)
  const currentRequestRef = useRef<AskRequest | null>(null)
  const mountedRef = useRef(true)

  useTargetSwitchLock(
    'ask',
    state === 'loading' ||
      state === 'generating' ||
      state === 'clarification_needed'
  )

  useEffect(() => {
    return () => {
      mountedRef.current = false
      abortControllerRef.current?.abort()
    }
  }, [])

  const clearResponse = useCallback(() => {
    setStatus(undefined)
    setSchemaLoaded(undefined)
    setClarification(undefined)
    setSqlGenerated(undefined)
    setResult(undefined)
    setError(undefined)
  }, [])

  const reset = useCallback(() => {
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
    clearResponse()
    setState('idle')
    currentRequestRef.current = null
  }, [clearResponse])

  const cancel = useCallback(() => {
    if (!abortControllerRef.current) return
    abortControllerRef.current.abort()
    abortControllerRef.current = null
    setError(undefined)
    setState('cancelled')
  }, [])

  const processStream = useCallback(
    async (request: AskRequest) => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }

      const controller = new AbortController()
      abortControllerRef.current = controller
      currentRequestRef.current = request
      clearResponse()
      setState('loading')

      let terminalEventReceived = false

      const processFrame = (frame: SseFrame) => {
        let data: unknown
        try {
          data = JSON.parse(frame.data)
        } catch (parseError) {
          console.warn('[Ask SSE] Ignoring malformed event data:', parseError)
          return
        }

        switch (frame.event) {
          case 'status': {
            const next = data as AskStatusEvent
            setStatus(next)
            if (next.phase === 'generate') setState('generating')
            break
          }
          case 'schema_loaded':
            setSchemaLoaded(data as AskSchemaLoadedEvent)
            break
          case 'clarification_needed':
            terminalEventReceived = true
            setClarification(data as AskClarificationNeededEvent)
            setState('clarification_needed')
            break
          case 'sql_generated':
            setSqlGenerated(data as AskSqlGeneratedEvent)
            break
          case 'result':
            terminalEventReceived = true
            setResult(data as AskResultEvent)
            setState('complete')
            break
          case 'error': {
            terminalEventReceived = true
            const event = data as AskErrorEvent
            const envelope = normalizeSseError(event)
            setError({
              type: 'error',
              message: envelope.message,
              phase: event.phase ?? null,
              code: envelope.code,
              category: envelope.category,
              target: envelope.target ?? request.target,
            })
            setState('error')
            break
          }
          default:
            console.warn('[Ask SSE] Unknown event type (ignored):', frame.event)
        }
      }

      try {
        const response = await fetch('/api/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(request),
          signal: controller.signal,
        })

        if (!response.ok) {
          let body: unknown
          try {
            body = await response.clone().json()
          } catch {
            body = await response.text().catch(() => undefined)
          }
          const envelope = normalizeHttpError(response.status, body)
          setError({
            type: 'error',
            message: envelope.message,
            phase: null,
            code: envelope.code,
            category: envelope.category,
            target: envelope.target ?? request.target,
          })
          setState('error')
          terminalEventReceived = true
          return
        }

        if (!response.body) throw new Error('The Ask response had no body.')

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const extracted = extractSseFrames(buffer)
          buffer = extracted.remainder
          extracted.frames.forEach(processFrame)
        }

        buffer += decoder.decode()
        extractSseFrames(buffer, true).frames.forEach(processFrame)

        if (!terminalEventReceived && mountedRef.current) {
          setError({
            type: 'error',
            message:
              'The answer stream ended before RDST received a complete response.',
            phase: null,
            code: 'ASK_STREAM_INCOMPLETE',
            target: request.target,
          })
          setState('error')
        }
      } catch (streamError: unknown) {
        if (streamError instanceof Error && streamError.name === 'AbortError') {
          return
        }
        if (!mountedRef.current) return
        setError({
          type: 'error',
          message:
            streamError instanceof Error
              ? streamError.message
              : 'The question could not be completed.',
          phase: null,
          target: request.target,
        })
        setState('error')
      } finally {
        if (abortControllerRef.current === controller) {
          abortControllerRef.current = null
        }
      }
    },
    [clearResponse]
  )

  const ask = useCallback(
    async (request: AskRequest) => processStream(request),
    [processStream]
  )

  const resumeWithAnswers = useCallback(
    async (answers: Record<string, string>) => {
      if (!clarification?.session_id || !currentRequestRef.current) return
      await processStream({
        ...currentRequestRef.current,
        session_id: clarification.session_id,
        clarification_answers: answers,
      })
    },
    [clarification, processStream]
  )

  return {
    ask,
    resumeWithAnswers,
    cancel,
    state,
    status,
    schemaLoaded,
    clarification,
    sqlGenerated,
    result,
    error,
    reset,
  }
}

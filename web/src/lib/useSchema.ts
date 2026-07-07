import { useCallback, useRef, useState } from 'react'
import { useTargetSwitchLock } from './targetSwitchLock'
import type { components } from './api.generated'
import type {
  SchemaStatus,
  SchemaDetails,
  SchemaTargetSummary,
  SchemaInitResult,
  SchemaExportResult,
  SchemaDeleteResult,
  SchemaUpdateResult,
  AddTableData,
  AddColumnData,
  AddEnumData,
  AddTerminologyData,
  AddRelationshipData,
  AddMetricData,
} from '../types/schema'

export type SchemaOperationResult = components['schemas']['SchemaOperationResponse']

interface UseSchemaReturn {
  // State
  status: SchemaStatus | null
  schema: SchemaDetails | null
  targets: SchemaTargetSummary[]
  error: string | null
  loading: boolean

  // Read operations
  checkStatus: (target: string) => Promise<SchemaStatus | null>
  loadSchema: (target: string, table?: string) => Promise<SchemaDetails | null>
  listTargets: () => Promise<SchemaTargetSummary[]>
  exportSchema: (target: string, format?: string) => Promise<string | null>

  // Write operations
  initSchema: (
    target: string,
    options?: { enumThreshold?: number; force?: boolean; sampleEnums?: boolean }
  ) => Promise<SchemaInitResult | null>
  deleteSchema: (target: string) => Promise<boolean>
  addTable: (target: string, data: AddTableData) => Promise<boolean>
  addColumn: (target: string, data: AddColumnData) => Promise<boolean>
  addEnum: (target: string, data: AddEnumData) => Promise<boolean>
  addTerminology: (target: string, data: AddTerminologyData) => Promise<boolean>
  addRelationship: (target: string, data: AddRelationshipData) => Promise<boolean>
  addMetric: (target: string, data: AddMetricData) => Promise<boolean>
  refreshSchema: (target: string) => Promise<SchemaOperationResult | null>
  profileSchema: (target: string, table?: string) => Promise<SchemaOperationResult | null>
  annotateWithLLM: (
    target: string,
    tableName?: string,
    onProgress?: (message: string, tableIndex?: number, totalTables?: number) => void
  ) => Promise<boolean>

  // Utilities
  clearError: () => void
}

export function useSchema(): UseSchemaReturn {
  const [status, setStatus] = useState<SchemaStatus | null>(null)
  const [schema, setSchema] = useState<SchemaDetails | null>(null)
  const [targets, setTargets] = useState<SchemaTargetSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const abortControllerRef = useRef<AbortController | null>(null)
  useTargetSwitchLock('schema', loading)

  const clearError = useCallback(() => {
    setError(null)
  }, [])

  // Helper to handle fetch with abort support
  const fetchWithAbort = useCallback(
    async <T>(
      url: string,
      options?: RequestInit
    ): Promise<{ data: T | null; error: string | null }> => {
      // Abort previous request
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
      const controller = new AbortController()
      abortControllerRef.current = controller

      try {
        const response = await fetch(url, {
          ...options,
          signal: controller.signal,
        })

        if (!response.ok) {
          const errorText = await response.text()
          return { data: null, error: `HTTP ${response.status}: ${errorText}` }
        }

        const data = await response.json()
        return { data, error: null }
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') {
          return { data: null, error: null }
        }
        return {
          data: null,
          error: err instanceof Error ? err.message : 'Request failed',
        }
      } finally {
        abortControllerRef.current = null
      }
    },
    []
  )

  // Read operations

  const checkStatus = useCallback(
    async (target: string): Promise<SchemaStatus | null> => {
      setLoading(true)
      setError(null)

      const { data, error: fetchError } = await fetchWithAbort<SchemaStatus>(
        `/api/semantic-layer/status?target=${encodeURIComponent(target)}`
      )

      if (fetchError) {
        setError(fetchError)
        setLoading(false)
        return null
      }

      setStatus(data)
      setLoading(false)
      return data
    },
    [fetchWithAbort]
  )

  const loadSchema = useCallback(
    async (target: string, table?: string): Promise<SchemaDetails | null> => {
      setLoading(true)
      setError(null)

      let url = `/api/semantic-layer?target=${encodeURIComponent(target)}`
      if (table) {
        url += `&table=${encodeURIComponent(table)}`
      }

      const { data, error: fetchError } = await fetchWithAbort<SchemaDetails>(url)

      if (fetchError) {
        setError(fetchError)
        setLoading(false)
        return null
      }

      setSchema(data)
      setLoading(false)
      return data
    },
    [fetchWithAbort]
  )

  const listTargets = useCallback(async (): Promise<SchemaTargetSummary[]> => {
    setLoading(true)
    setError(null)

    const { data, error: fetchError } = await fetchWithAbort<{
      targets: SchemaTargetSummary[]
    }>('/api/semantic-layer/targets')

    if (fetchError) {
      setError(fetchError)
      setLoading(false)
      return []
    }

    const targetList = data?.targets || []
    setTargets(targetList)
    setLoading(false)
    return targetList
  }, [fetchWithAbort])

  const exportSchema = useCallback(
    async (target: string, format = 'yaml'): Promise<string | null> => {
      setLoading(true)
      setError(null)

      const { data, error: fetchError } = await fetchWithAbort<SchemaExportResult>(
        `/api/semantic-layer/export?target=${encodeURIComponent(target)}&format=${encodeURIComponent(format)}`
      )

      setLoading(false)

      if (fetchError) {
        setError(fetchError)
        return null
      }

      if (data && !data.success) {
        setError(data.error || 'Export failed')
        return null
      }

      return data?.content || null
    },
    [fetchWithAbort]
  )

  // Write operations

  const initSchema = useCallback(
    async (
      target: string,
      options?: { enumThreshold?: number; force?: boolean; sampleEnums?: boolean }
    ): Promise<SchemaInitResult | null> => {
      setLoading(true)
      setError(null)

      const { data, error: fetchError } = await fetchWithAbort<SchemaInitResult>(
        '/api/semantic-layer/init',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            target,
            enum_threshold: options?.enumThreshold ?? 20,
            force: options?.force ?? false,
            sample_enums: options?.sampleEnums ?? true,
          }),
        }
      )

      setLoading(false)

      if (fetchError) {
        setError(fetchError)
        return null
      }

      if (data && !data.success) {
        setError(data.error || 'Init failed')
      }

      return data
    },
    [fetchWithAbort]
  )

  const deleteSchema = useCallback(
    async (target: string): Promise<boolean> => {
      setLoading(true)
      setError(null)

      const { data, error: fetchError } = await fetchWithAbort<SchemaDeleteResult>(
        `/api/semantic-layer?target=${encodeURIComponent(target)}`,
        { method: 'DELETE' }
      )

      setLoading(false)

      if (fetchError) {
        setError(fetchError)
        return false
      }

      if (data && !data.success) {
        setError(data.error || 'Delete failed')
        return false
      }

      // Clear cached schema if it was for this target
      if (schema?.target === target) {
        setSchema(null)
      }
      if (status?.target === target) {
        setStatus(null)
      }

      return true
    },
    [fetchWithAbort, schema?.target, status?.target]
  )

  const addTable = useCallback(
    async (target: string, data: AddTableData): Promise<boolean> => {
      setLoading(true)
      setError(null)

      const { data: result, error: fetchError } =
        await fetchWithAbort<SchemaUpdateResult>('/api/semantic-layer/table', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target, ...data }),
        })

      setLoading(false)

      if (fetchError) {
        setError(fetchError)
        return false
      }

      if (result && !result.success) {
        setError(result.error || 'Failed to add table')
        return false
      }

      return true
    },
    [fetchWithAbort]
  )

  const addColumn = useCallback(
    async (target: string, data: AddColumnData): Promise<boolean> => {
      setLoading(true)
      setError(null)

      const { data: result, error: fetchError } =
        await fetchWithAbort<SchemaUpdateResult>('/api/semantic-layer/column', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target, ...data }),
        })

      setLoading(false)

      if (fetchError) {
        setError(fetchError)
        return false
      }

      if (result && !result.success) {
        setError(result.error || 'Failed to add column')
        return false
      }

      return true
    },
    [fetchWithAbort]
  )

  const addEnum = useCallback(
    async (target: string, data: AddEnumData): Promise<boolean> => {
      setLoading(true)
      setError(null)

      const { data: result, error: fetchError } =
        await fetchWithAbort<SchemaUpdateResult>('/api/semantic-layer/enum', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target, ...data }),
        })

      setLoading(false)

      if (fetchError) {
        setError(fetchError)
        return false
      }

      if (result && !result.success) {
        setError(result.error || 'Failed to add enum')
        return false
      }

      return true
    },
    [fetchWithAbort]
  )

  const addTerminology = useCallback(
    async (target: string, data: AddTerminologyData): Promise<boolean> => {
      setLoading(true)
      setError(null)

      const { data: result, error: fetchError } =
        await fetchWithAbort<SchemaUpdateResult>('/api/semantic-layer/terminology', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target, ...data }),
        })

      setLoading(false)

      if (fetchError) {
        setError(fetchError)
        return false
      }

      if (result && !result.success) {
        setError(result.error || 'Failed to add terminology')
        return false
      }

      return true
    },
    [fetchWithAbort]
  )

  const addRelationship = useCallback(
    async (target: string, data: AddRelationshipData): Promise<boolean> => {
      setLoading(true)
      setError(null)

      const { data: result, error: fetchError } =
        await fetchWithAbort<SchemaUpdateResult>('/api/semantic-layer/relationship', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target, ...data }),
        })

      setLoading(false)

      if (fetchError) {
        setError(fetchError)
        return false
      }

      if (result && !result.success) {
        setError(result.error || 'Failed to add relationship')
        return false
      }

      return true
    },
    [fetchWithAbort]
  )

  const addMetric = useCallback(
    async (target: string, data: AddMetricData): Promise<boolean> => {
      setLoading(true)
      setError(null)

      const { data: result, error: fetchError } =
        await fetchWithAbort<SchemaUpdateResult>('/api/semantic-layer/metric', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target, ...data }),
        })

      setLoading(false)

      if (fetchError) {
        setError(fetchError)
        return false
      }

      if (result && !result.success) {
        setError(result.error || 'Failed to add metric')
        return false
      }

      return true
    },
    [fetchWithAbort]
  )

  const refreshSchema = useCallback(
    async (target: string): Promise<SchemaOperationResult | null> => {
      setLoading(true)
      setError(null)

      const { data, error: fetchError } = await fetchWithAbort<SchemaOperationResult>(
        '/api/semantic-layer/refresh',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target }),
        }
      )

      setLoading(false)

      if (fetchError) {
        setError(fetchError)
        return null
      }

      return data
    },
    [fetchWithAbort]
  )

  const profileSchema = useCallback(
    async (target: string, table?: string): Promise<SchemaOperationResult | null> => {
      setLoading(true)
      setError(null)

      const { data, error: fetchError } = await fetchWithAbort<SchemaOperationResult>(
        '/api/semantic-layer/profile',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target, table }),
        }
      )

      setLoading(false)

      if (fetchError) {
        setError(fetchError)
        return null
      }

      return data
    },
    [fetchWithAbort]
  )

  const annotateWithLLM = useCallback(
    async (
      target: string,
      tableName?: string,
      onProgress?: (message: string, tableIndex?: number, totalTables?: number) => void
    ): Promise<boolean> => {
      setLoading(true)
      setError(null)

      try {
        const response = await fetch('/api/semantic-layer/annotate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target, table_name: tableName }),
        })

        if (!response.ok || !response.body) {
          throw new Error(`HTTP ${response.status}`)
        }

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let success = true
        let currentEvent = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEvent = line.slice(7).trim()
            } else if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6))

                if (currentEvent === 'error') {
                  setError(data.message || 'Annotation failed')
                  success = false
                } else if (currentEvent === 'progress' || currentEvent === 'started' || currentEvent === 'table_complete') {
                  if (onProgress && data.message) {
                    onProgress(data.message, data.table_index, data.total_tables)
                  }
                } else if (currentEvent === 'complete') {
                  if (onProgress && data.message) {
                    onProgress(data.message)
                  }
                } else if (currentEvent === 'unknown') {
                  console.warn('[Schema SSE] Unknown event payload (ignored):', data)
                } else if (currentEvent) {
                  console.warn('[Schema SSE] Unknown event type (ignored):', currentEvent, data)
                }
              } catch {
                // Ignore parse errors
              }
            }
          }
        }

        setLoading(false)
        return success
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Request failed')
        setLoading(false)
        return false
      }
    },
    []
  )

  return {
    // State
    status,
    schema,
    targets,
    error,
    loading,

    // Read operations
    checkStatus,
    loadSchema,
    listTargets,
    exportSchema,

    // Write operations
    initSchema,
    deleteSchema,
    addTable,
    addColumn,
    addEnum,
    addTerminology,
    addRelationship,
    addMetric,
    refreshSchema,
    profileSchema,
    annotateWithLLM,

    // Utilities
    clearError,
  }
}

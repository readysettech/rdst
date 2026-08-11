import { useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTarget } from '../../../hooks/useTarget'
import { fillCapturedParams } from '../../../lib/sqlParameters'
import {
  type QueryRegistryEntry,
  useQueryRegistry,
} from '../../../lib/useQueryRegistry'
import {
  type TargetPasswordLockState,
  useTargetPasswordLock,
} from '../../../lib/useTargetPasswordLock'

export interface AnalyzeController {
  editor: {
    value: string
    setValue: (value: string) => void
    fast: boolean
    setFast: (fast: boolean) => void
    ref: React.RefObject<HTMLDivElement | null>
    isHighlighted: boolean
    disabled: boolean
  }
  target: {
    name: string | null
    lock: TargetPasswordLockState
  }
  history: {
    queries: QueryRegistryEntry[]
    isLoading: boolean
    error: unknown
    retry: () => void
  }
  actions: {
    analyze: () => void
    selectHistory: (entry: QueryRegistryEntry) => void
  }
}

export function useAnalyzeController(): AnalyzeController {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [fast, setFast] = useState(false)
  const { target: selectedTarget } = useTarget()
  const lock = useTargetPasswordLock(selectedTarget)
  const { queries, addQuery, isLoading, listError, refetch } = useQueryRegistry(
    undefined,
    selectedTarget
  )

  const editorRef = useRef<HTMLDivElement>(null)
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [isEditorHighlighted, setEditorHighlighted] = useState(false)

  const analyze = useCallback(() => {
    const sql = query.trim()
    if (lock.isLocked || !sql) return

    addQuery(sql, selectedTarget || undefined)
    navigate({
      to: '/results',
      search: {
        query: sql,
        target: selectedTarget || undefined,
        fast: fast || undefined,
      },
    })
  }, [addQuery, fast, lock.isLocked, navigate, query, selectedTarget])

  const selectHistory = useCallback((entry: QueryRegistryEntry) => {
    setQuery(fillCapturedParams(entry.sql, entry.most_recent_params))

    const editor = editorRef.current
    if (editor) {
      const prefersReducedMotion =
        typeof window !== 'undefined' &&
        !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

      editor.scrollIntoView({
        behavior: prefersReducedMotion ? 'auto' : 'smooth',
        block: 'nearest',
      })
      editor.querySelector<HTMLElement>('.cm-content')?.focus()
    }

    setEditorHighlighted(true)
    if (highlightTimer.current) clearTimeout(highlightTimer.current)
    highlightTimer.current = setTimeout(() => setEditorHighlighted(false), 700)
  }, [])

  useEffect(
    () => () => {
      if (highlightTimer.current) clearTimeout(highlightTimer.current)
    },
    []
  )

  return {
    editor: {
      value: query,
      setValue: setQuery,
      fast,
      setFast,
      ref: editorRef,
      isHighlighted: isEditorHighlighted,
      disabled: lock.isLocked,
    },
    target: {
      name: selectedTarget,
      lock,
    },
    history: {
      queries,
      isLoading,
      error: listError,
      retry: () => void refetch(),
    },
    actions: {
      analyze,
      selectHistory,
    },
  }
}

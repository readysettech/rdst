import { cn } from '@rs/tailwind-base'
import { CopyButton } from '@rs/ui-new/copy-button'
import {
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useRef,
  useState,
} from 'react'
import { useFormatSql } from '../../lib/useFormatSql'
import { SqlTokens } from '../SqlTokens'
import type { QueryCardDialect } from './types'

interface QueryCardSqlProps {
  sql: string
  dialect?: QueryCardDialect
  initiallyExpanded?: boolean
  /** Disabled when the whole card is the query-selection control. */
  expandable: boolean
  /** Selectable cards expose one interaction only, so they omit Copy. */
  copyable: boolean
}

const QUERY_CARD_LINE_WIDTH = 80

export function QueryCardSql({
  sql,
  dialect,
  initiallyExpanded = false,
  expandable,
  copyable,
}: QueryCardSqlProps) {
  // Formatting is presentation and applies to every query surface. Expansion
  // remains a separate interaction concern, disabled when the whole card is
  // already the selection control.
  const formatted = useFormatSql(sql, dialect, {
    lineWidth: QUERY_CARD_LINE_WIDTH,
    expressionWidth: QUERY_CARD_LINE_WIDTH,
    multilineProjection: true,
  })
  const displaySql = formatted ?? sql
  const lineCount = displaySql.split('\n').length
  const [expanded, setExpanded] = useState(initiallyExpanded)
  const [overflows, setOverflows] = useState(false)
  const sqlRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const element = sqlRef.current
    if (!element || !expandable || expanded) return

    const measure = () => {
      setOverflows(element.scrollHeight > element.clientHeight + 1)
    }

    measure()
    window.addEventListener('resize', measure)

    const observer =
      typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(element)

    return () => {
      window.removeEventListener('resize', measure)
      observer?.disconnect()
    }
  }, [displaySql, expandable, expanded])

  const canToggle = expandable && (overflows || expanded)

  const toggle = () => {
    if (!canToggle || window.getSelection()?.toString()) return
    setExpanded((value) => !value)
  }

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      toggle()
    }
  }

  const interactiveProps = canToggle
    ? {
        role: 'button',
        tabIndex: 0,
        'aria-expanded': expanded,
        'aria-label': expanded ? 'Collapse SQL' : 'Expand SQL',
        onClick: toggle,
        onKeyDown: handleKeyDown,
      }
    : {}

  return (
    <div className="relative bg-surface-layout-1/50">
      {copyable ? (
        <div className="absolute top-3 right-3 z-10 rounded-lg bg-surface-layout-1 shadow-elevation-1">
          <CopyButton text={sql} />
        </div>
      ) : null}
      <div
        ref={sqlRef}
        {...interactiveProps}
        className={cn(
          'px-4 py-4 overflow-hidden focus-visible:outline-none focus-visible:shadow-focus',
          copyable && 'pr-14',
          expandable && !expanded ? 'max-h-50' : 'max-h-none',
          canToggle && 'cursor-pointer'
        )}
      >
        <div className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-3">
          <div
            aria-hidden="true"
            className="select-none border-r-(length:--border-base) border-r-border-layout-1 pr-3 text-right font-mono text-mono-large text-content-layout-3/50"
          >
            {Array.from({ length: lineCount }, (_, index) => (
              <span className="block" key={index}>
                {index + 1}
              </span>
            ))}
          </div>
          <SqlTokens sql={displaySql} title={sql} />
        </div>
      </div>
      {expandable && overflows && !expanded ? (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-surface-layout-2/50 to-transparent"
        />
      ) : null}
    </div>
  )
}

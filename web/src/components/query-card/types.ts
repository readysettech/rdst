import type { ReactNode } from 'react'

export type QueryCardDialect = 'postgresql' | 'mysql'

interface QueryCardBaseProps {
  /** Full raw SQL. It remains the copy value and stable DOM hook. */
  sql: string
  /** Optional formatter dialect; inferred from SQL when omitted. */
  dialect?: QueryCardDialect
  /** Opens the SQL band at full height on its first render. */
  sqlInitiallyExpanded?: boolean

  /** Optional identity content shown in the header. */
  leading?: ReactNode
  title?: ReactNode
  badges?: ReactNode

  /** Footer content owned by the calling page. */
  meta?: ReactNode

  className?: string
  highlighted?: boolean
  'data-testid'?: string
  'data-query-hash'?: string
  'data-cache-id'?: string
}

interface QueryCardStaticProps {
  selectable?: false
  selected?: never
  onSelect?: never
  selectionLabel?: never

  menu?: ReactNode
  /** Quieter footer actions rendered before the primary action. */
  secondaryActions?: ReactNode
  /** The rightmost footer action. Its visual variant remains caller-owned. */
  primaryAction?: ReactNode
  detailsOpen?: boolean
  onToggleDetails?: () => void

  /** Replaces only the SQL band while preserving header and footer anatomy. */
  editor?: ReactNode
  /** Optional detail band below the footer. */
  expansion?: ReactNode
}

interface QueryCardSelectableProps {
  selectable: true
  selected: boolean
  onSelect: () => void
  selectionLabel: string

  menu?: never
  secondaryActions?: never
  primaryAction?: never
  detailsOpen?: never
  onToggleDetails?: never
  editor?: never
  expansion?: never
}

export type QueryCardProps = QueryCardBaseProps &
  (QueryCardStaticProps | QueryCardSelectableProps)

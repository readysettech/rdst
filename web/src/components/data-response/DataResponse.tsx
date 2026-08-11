import type { ReactNode } from 'react'

type DataResponseState = 'loading' | 'error' | 'empty' | 'data'

interface DataResponseProps<TData> {
  data: TData | null | undefined
  isLoading: boolean
  error?: unknown
  isEmpty?: (data: TData) => boolean
  renderLoading: ReactNode
  renderError: (error: unknown) => ReactNode
  renderEmpty: ReactNode
  children: (data: TData) => ReactNode
}

function defaultIsEmpty<TData>(data: TData) {
  return Array.isArray(data) && data.length === 0
}

export function getDataResponseState<TData>({
  data,
  isLoading,
  error,
  isEmpty = defaultIsEmpty,
}: Pick<
  DataResponseProps<TData>,
  'data' | 'isLoading' | 'error' | 'isEmpty'
>): DataResponseState {
  if (isLoading) return 'loading'
  if (error !== undefined && error !== null) return 'error'
  if (data == null || isEmpty(data)) return 'empty'
  return 'data'
}

/**
 * One state boundary for data-backed regions.
 *
 * Loading, error, empty, and data always resolve in the same order. Callers
 * own their feature-specific skeleton, recovery copy, empty CTA, and content.
 */
export function DataResponse<TData>({
  data,
  isLoading,
  error,
  isEmpty,
  renderLoading,
  renderError,
  renderEmpty,
  children,
}: DataResponseProps<TData>) {
  const state = getDataResponseState({ data, isLoading, error, isEmpty })

  const content =
    state === 'loading'
      ? renderLoading
      : state === 'error'
        ? renderError(error)
        : state === 'empty'
          ? renderEmpty
          : children(data as TData)

  return <>{content}</>
}

import type { IconStrokeName } from '@rs/ui-icons/icon-name'

export type QueriesView = 'slow' | 'saved' | 'analyze'

export const QUERY_VIEWS: ReadonlyArray<{
  value: QueriesView
  label: string
  icon: IconStrokeName
}> = [
  { value: 'slow', label: 'Find', icon: 'observe' },
  { value: 'saved', label: 'Saved', icon: 'folder-file' },
  { value: 'analyze', label: 'Analyze', icon: 'speedometer' },
]

export function resolveQueriesView(search: {
  view?: QueriesView
  hash?: string
  run?: string
}): QueriesView {
  return search.hash || search.run ? 'saved' : (search.view ?? 'slow')
}

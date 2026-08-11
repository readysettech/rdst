export type QueryLibraryDisplayProperty =
  | 'source'
  | 'impact'
  | 'frequency'
  | 'parameters'
  | 'activity'

export type QueryLibraryDisplayMode = 'card-1' | 'card-2' | 'rows'

export const QUERY_LIBRARY_DISPLAY_MODES = [
  { value: 'card-1', label: 'Card 1', icon: 'dashboard' },
  { value: 'card-2', label: 'Card 2', icon: 'speedometer' },
  { value: 'rows', label: 'List', icon: 'menu' },
] as const satisfies ReadonlyArray<{
  value: QueryLibraryDisplayMode
  label: string
  icon: 'dashboard' | 'speedometer' | 'menu'
}>

export const QUERY_LIBRARY_DISPLAY_PROPERTIES: Array<{
  value: QueryLibraryDisplayProperty
  label: string
}> = [
  { value: 'source', label: 'Source' },
  { value: 'impact', label: 'Database time' },
  { value: 'frequency', label: 'Runs' },
  { value: 'parameters', label: 'Parameters' },
  { value: 'activity', label: 'Last activity' },
]

export const QUERY_LIBRARY_DEFAULT_DISPLAY_PROPERTIES: QueryLibraryDisplayProperty[] =
  ['source', 'impact', 'frequency', 'parameters']

import type { TopQuery } from '../../../types/top'

export interface SlowQueryRowViewModel {
  query: TopQuery
  hasRunning: boolean
  meta: string
  cached: boolean
}

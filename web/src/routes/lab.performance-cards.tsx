import { createFileRoute } from '@tanstack/react-router'
import { PerformanceCardsLabPage } from '../features/caching/lab/PerformanceCardsLabPage'

export const Route = createFileRoute('/lab/performance-cards')({
  component: PerformanceCardsLabPage,
})

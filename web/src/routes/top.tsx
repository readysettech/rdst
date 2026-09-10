import { toast } from '@rs/ui-new/use-toast'
import { createFileRoute, redirect } from '@tanstack/react-router'

// Historical discovery now feeds the Query Library automatically. A bookmark
// asked for the slowest queries, so hand the library that ordering rather than
// a status filter that reads as "no queries match these filters" on a young
// install, and say where the view went.
export const Route = createFileRoute('/top')({
  beforeLoad: () => {
    toast({
      title: 'Top queries now live in the Query Library',
      description: 'Sorted by slowest average, the way this view always was.',
    })
    throw redirect({ to: '/queries', search: { sort: 'slowest-average' } })
  },
})

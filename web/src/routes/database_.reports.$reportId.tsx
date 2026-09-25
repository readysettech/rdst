import { createFileRoute, redirect } from '@tanstack/react-router'

// Old report URL: reports live on the Database page's Report tab now.
export const Route = createFileRoute('/database_/reports/$reportId')({
  beforeLoad: ({ params }) => {
    const report = /^\d+$/.test(params.reportId) ? params.reportId : undefined
    throw redirect({
      to: '/database',
      search: { view: 'report', ...(report ? { report } : {}) },
      replace: true,
    })
  },
})

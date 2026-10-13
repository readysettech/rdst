import { Button } from '@rs/ui-new/button'
import { useToast } from '@rs/ui-new/use-toast'
import { useEffect, useRef } from 'react'
import type { ReportRun } from './useReportRun'

const announced = new Set<string>()

/**
 * Finishes a run on screen once. Watching the run (on the Report tab), the finished
 * report replaces the run after a short success moment. Elsewhere a "Report ready"
 * toast offers the report. A cancelled run returns to idle with a short note.
 */
export function useRunCompletion(
  target: string,
  run: ReportRun,
  {
    watching,
    onOpen,
    holdMs = 1600,
  }: { watching: boolean; onOpen: (id: number) => void; holdMs?: number }
) {
  const { toast } = useToast()
  const { status, reportId, dismiss } = run
  // Callers pass a fresh callback each render; the page re-renders every second, which
  // must not restart the hold before the finished report replaces the run.
  const open = useRef(onOpen)
  open.current = onOpen
  useEffect(() => {
    if (reportId === null) return
    const key = `${target}:${reportId}:${status}`
    const first = !announced.has(key)
    if (status === 'done') {
      if (first) {
        announced.add(key)
        if (!watching)
          toast({
            title: 'Report ready',
            description: `The report for ${target} is ready.`,
            variant: 'positive',
            action: (
              <Button
                size="small"
                variant="primary"
                modifier="outline"
                label="Read report"
                onClick={() => open.current(reportId)}
              />
            ),
          })
      }
      const timer = setTimeout(dismiss, watching ? holdMs : 0)
      return () => clearTimeout(timer)
    }
    if (status === 'cancelled') {
      if (first) {
        announced.add(key)
        toast({ title: 'Analysis cancelled', variant: 'primary' })
      }
      const timer = setTimeout(dismiss, watching ? holdMs : 0)
      return () => clearTimeout(timer)
    }
  }, [status, reportId, target, dismiss, toast, holdMs, watching])
}

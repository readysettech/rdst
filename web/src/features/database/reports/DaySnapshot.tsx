// The daily snapshot on the Report tab (DESIGN_V5 section 5, FEEDBACK_ROUND3 B, ROUND4 R).
// One day, top to bottom, no tabs, with a sticky bar to jump between sections: the database's
// health for the day, its numbers, the day in brief (its top priorities), the
// database-level actions, then the day's chart and its spikes, what happened, the queries to
// look at (each open to its fix, with the full deep analysis in place) and every query that
// ran. Query names carry their chart color and open in place; SQL only ever appears in code
// blocks.
import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { Tag } from '@rs/ui-new/tag'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ChartHighlight } from '@/features/monitor-kit/charts'
import type {
  DatabaseReport,
  DaySnapshot as Snapshot,
} from '@/lib/monitor/types'
import { ActionsNow } from '../actions/ActionsNow'
import { DayPicker } from './DayPicker'
import { ReportSectionNav } from './ReportSectionNav'
import { ChartSection, Spikes, Timeline } from './SnapshotDay'
import { AllQueries, LookAt } from './SnapshotQueries'
import { Brief, Health, Numbers } from './SnapshotTop'
import type { SnapshotContext } from './snapshotKit'
import {
  type ChartView,
  chipColors,
  type DayEntry,
  dayTitle,
  periodLine,
  reportSections,
} from './snapshotModel'
import './reportPrint.css'

// 1. Title

function Header({
  report,
  snapshot,
  days,
  onPick,
  onPrint,
}: {
  report: DatabaseReport
  snapshot: Snapshot
  days: DayEntry[]
  onPick: (id: number) => void
  onPrint: () => void
}) {
  const line = report.sample
    ? "Made up from this database's own queries to show what past days look like"
    : periodLine(snapshot.period, report.finished_at ?? report.ts)
  return (
    <header className="flex flex-col gap-3 tablet:flex-row tablet:items-start tablet:justify-between">
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1
            className="text-headline-3 text-content-layout-1"
            data-testid="snapshot-title"
          >
            {dayTitle(snapshot.period.day)}
          </h1>
          {report.sample ? (
            <Tag
              size="small"
              variant="neutral"
              modifier="outline"
              label="Sample data"
              data-testid="sample-label"
            />
          ) : null}
        </div>
        {line ? (
          <p
            className="text-body-medium text-content-layout-2"
            data-testid="snapshot-period"
          >
            {line}
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2 print:hidden">
        <DayPicker days={days} selectedId={report.id} onPick={onPick} />
        <Button
          variant="primary"
          modifier="outline"
          size="small"
          label="Print / Save as PDF"
          onClick={onPrint}
        />
      </div>
    </header>
  )
}

/** Expands everything while the page prints; the Print button waits for it. */
function usePrinting() {
  const [printing, setPrinting] = useState(false)
  useEffect(() => {
    const root = document.documentElement
    let mode: string | undefined
    // Paper is light: print in the light theme, then put the viewer's theme back.
    const before = () => {
      mode = root.dataset.colorMode
      root.dataset.colorMode = 'light'
      setPrinting(true)
    }
    const after = () => {
      if (mode === undefined) delete root.dataset.colorMode
      else root.dataset.colorMode = mode
      setPrinting(false)
    }
    window.addEventListener('beforeprint', before)
    window.addEventListener('afterprint', after)
    return () => {
      window.removeEventListener('beforeprint', before)
      window.removeEventListener('afterprint', after)
    }
  }, [])
  const print = useCallback(() => {
    setPrinting(true)
    // Give the expanded analyses a moment to load before the print dialog opens.
    window.setTimeout(() => window.print(), 1500)
  }, [])
  return { printing, print }
}

export function DaySnapshot({
  report,
  snapshot,
  target,
  days,
  onPick,
}: {
  report: DatabaseReport
  snapshot: Snapshot
  target: string
  days: DayEntry[]
  onPick: (id: number) => void
}) {
  const { printing, print } = usePrinting()
  const colors = useMemo(() => chipColors(snapshot), [snapshot])
  const [view, setView] = useState<ChartView>('calls')
  const [highlight, setHighlight] = useState<ChartHighlight | null>(null)
  // A new day starts on queries per second.
  useEffect(() => setView('calls'), [report.id])
  // Only the latest day points at what to change now; a past day stays as it was.
  const current = !days.length || days[0].id === report.id
  const sections = useMemo(
    () => reportSections(snapshot, current),
    [snapshot, current]
  )
  const context: SnapshotContext = { target, snapshot, colors, printing }
  return (
    <article
      className="space-y-8 pb-8"
      data-testid="day-snapshot"
      data-report-id={report.id}
      data-print-root=""
    >
      <Header
        report={report}
        snapshot={snapshot}
        days={days}
        onPick={onPick}
        onPrint={print}
      />
      {snapshot.legacy ? (
        <Alert
          variant="informative"
          modifier="outline"
          icon="info"
          iconPosition="left"
          label="This report was written before daily snapshots, so it covers the 24 hours before it ran and has no chart."
        />
      ) : null}
      <ReportSectionNav sections={sections} />
      <Health context={context} />
      <Numbers context={context} view={view} onView={setView} />
      <Brief context={context} />
      {current ? <ActionsNow target={target} /> : null}
      <ChartSection
        context={context}
        view={view}
        onView={setView}
        highlight={highlight}
      />
      <Spikes context={context} onHighlight={setHighlight} />
      <Timeline context={context} />
      <LookAt context={context} />
      <AllQueries context={context} />
    </article>
  )
}

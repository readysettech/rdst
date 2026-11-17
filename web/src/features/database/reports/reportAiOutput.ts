import type { DatabaseReport } from '@/lib/monitor/types'

type ReportText = Pick<DatabaseReport, 'report_json' | 'refs'> &
  Partial<Pick<DatabaseReport, 'narrative_missing'>>

/** What the AI model wrote for a report, as plain text for an AI-output report: its summary and
 * the wording of its fixes, with query references named. Health notes and the numbers on the
 * page come from code and aren't included. */
export function reportAiOutput(report: ReportText | null | undefined): string {
  const body = report?.report_json
  if (!body || report.narrative_missing || body.narrative_error) return ''
  const named = (text: string | null | undefined) =>
    (text ?? '')
      .replace(
        /\{\{q:([0-9a-f]+)\}\}/g,
        (_, key: string) => report.refs?.[key]?.display_name ?? 'a query'
      )
      .trim()
  const lines = (items: (string | null | undefined)[]) =>
    items.map(named).filter(Boolean)
  const fixes = lines(
    (body.fix_first ?? []).map((fix) => {
      const detail = [fix.why, fix.action].map(named).filter(Boolean).join(' ')
      return [named(fix.title), detail].filter(Boolean).join(': ')
    })
  )
  const grouped = lines(
    (body.one_fix_several_queries ?? []).map((group) => group.action)
  )
  return [
    named(body.summary) ? `Summary:\n${named(body.summary)}` : '',
    fixes.length ? `Fix first:\n${fixes.join('\n')}` : '',
    grouped.length ? `One fix for several queries:\n${grouped.join('\n')}` : '',
  ]
    .filter(Boolean)
    .join('\n\n')
}

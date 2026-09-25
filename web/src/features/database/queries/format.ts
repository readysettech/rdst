// Number and time formatting for the query list. Values render with tabular
// numerals; units are split out so the caller can set them one step smaller.
import type { LastAnalysis, LastCompare } from '@/lib/monitor/types'

export function comparedLabel(
  last: LastCompare | null | undefined
): string | null {
  if (!last) return null
  if (last.speedup_label) return `Compared: ${last.speedup_label}`
  if (last.speedup == null) return null
  const speed =
    last.speedup >= 10 ? Math.round(last.speedup) : last.speedup.toFixed(1)
  return `Compared: ${speed}x ${last.speedup >= 1 ? 'faster' : 'slower'}`
}

export interface Measure {
  value: string
  unit: string
}

export function measureMs(ms: number | null | undefined): Measure {
  if (ms === null || ms === undefined || !Number.isFinite(ms))
    return { value: '-', unit: '' }
  if (ms < 0.01) return { value: '<0.01', unit: 'ms' }
  if (ms < 1) return { value: ms.toFixed(2), unit: 'ms' }
  if (ms < 10) return { value: ms.toFixed(1), unit: 'ms' }
  if (ms < 1000) return { value: Math.round(ms).toString(), unit: 'ms' }
  const seconds = ms / 1000
  if (seconds < 60) return { value: seconds.toFixed(1), unit: 's' }
  return { value: (seconds / 60).toFixed(1), unit: 'min' }
}

export function measureQps(qps: number | null | undefined): Measure {
  if (qps === null || qps === undefined || !Number.isFinite(qps))
    return { value: '-', unit: '' }
  if (qps === 0) return { value: '0', unit: 'qps' }
  if (qps < 0.01) {
    const perHour = qps * 3600
    return {
      value: perHour < 10 ? perHour.toFixed(1) : Math.round(perHour).toString(),
      unit: 'per h',
    }
  }
  if (qps < 10) return { value: qps.toFixed(qps < 1 ? 2 : 1), unit: 'qps' }
  if (qps < 1000) return { value: Math.round(qps).toString(), unit: 'qps' }
  return { value: `${(qps / 1000).toFixed(1)}k`, unit: 'qps' }
}

export const text = (measure: Measure) =>
  measure.unit ? `${measure.value} ${measure.unit}` : measure.value

export function formatShare(share: number): string {
  const percent = share * 100
  if (percent <= 0) return '0 %'
  if (percent < 0.1) return '<0.1 %'
  if (percent < 10) return `${percent.toFixed(1)} %`
  return `${Math.round(percent)} %`
}

export function relativeTime(
  ts: number,
  nowSeconds = Date.now() / 1000
): string {
  const delta = Math.max(0, nowSeconds - ts)
  if (delta < 60) return `${Math.max(1, Math.round(delta))} s ago`
  if (delta < 3600) return `${Math.round(delta / 60)} min ago`
  if (delta < 86400) return `${Math.round(delta / 3600)} h ago`
  const date = new Date(ts * 1000)
  if (delta < 6 * 86400)
    return date.toLocaleString(undefined, {
      weekday: 'short',
      hour: 'numeric',
      minute: '2-digit',
    })
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/**
 * Which values a finished analysis used, in one line: live traffic, the table's own data,
 * or none (a plan only). Null when the query had no parameters or chose EXPLAIN for
 * another reason.
 */
export function analysisValuesLine(
  analysis: Pick<LastAnalysis, 'mode' | 'param_source'> | null | undefined
): string | null {
  if (!analysis) return null
  if (analysis.mode === 'analyze' && analysis.param_source === 'sampled')
    return 'Measured with values from live traffic'
  if (analysis.mode === 'analyze' && analysis.param_source === 'table_sampled')
    return "Measured with values taken from the table's own data, not from live traffic"
  if (analysis.mode === 'explain' && analysis.param_source === 'none')
    return "Plan only: no real values were available, so RDST didn't run the query"
  return null
}

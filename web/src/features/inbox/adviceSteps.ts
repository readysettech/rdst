import type { InboxAdvice } from '@/lib/monitor/types'

// A step reads "What to do: SQL". The SQL part gets its own copyable block.
const SQL_STEP =
  /^(.*?):\s+((?:SELECT|ANALYZE|CREATE|SET|KILL|ALTER|SHOW)\b[\s\S]*)$/i

export interface AdviceStep {
  text: string
  sql: string | null
}

export function adviceSteps(steps: InboxAdvice['steps']): AdviceStep[] {
  return steps.map((step) => {
    const match = SQL_STEP.exec(step)
    return match
      ? { text: match[1], sql: match[2].trim() }
      : { text: step, sql: null }
  })
}

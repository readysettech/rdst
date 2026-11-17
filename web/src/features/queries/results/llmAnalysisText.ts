import type { components } from '@/lib/api.generated'

type LLMAnalysis = components['schemas']['LLMAnalysis']

/** What the AI model wrote about a query, as plain text to review in an AI-output report. */
export function llmAnalysisText(llm: LLMAnalysis | null | undefined): string {
  if (!llm) return ''
  const sections: string[] = []
  const section = (title: string, lines: string[]) => {
    const kept = lines.map((line) => line.trim()).filter(Boolean)
    if (kept.length) sections.push(`${title}:\n${kept.join('\n')}`)
  }
  const assessment = llm.performance_assessment
  if (assessment) {
    section('Assessment', [
      `Rated ${assessment.overall_rating}, efficiency ${assessment.efficiency_score}/100`,
      ...(assessment.primary_concerns ?? []).map((concern) => `- ${concern}`),
    ])
  }
  const execution = llm.execution_analysis
  if (execution) {
    section('Execution', [
      execution.scan_efficiency,
      ...(execution.bottlenecks ?? []).map((bottleneck) => `- ${bottleneck}`),
    ])
  }
  for (const index of llm.index_recommendations ?? []) {
    section(`Index on ${index.table} (${(index.columns ?? []).join(', ')})`, [
      index.rationale,
      index.sql,
      ...(index.caveats ?? []).map((caveat) => `- ${caveat}`),
    ])
  }
  for (const rewrite of llm.rewrite_suggestions ?? []) {
    section('Rewrite', [
      rewrite.explanation,
      rewrite.expected_improvement,
      rewrite.rewritten_sql,
    ])
  }
  section(
    'Other opportunities',
    (llm.optimization_opportunities ?? []).map(
      (item) => `- ${item.description}`
    )
  )
  return sections.join('\n\n')
}

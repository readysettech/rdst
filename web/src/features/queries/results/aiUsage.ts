import type { CompleteEvent } from '../../../lib/api'

/** Which AI answered an analysis, its tokens and what it cost, as the footer shows it. */
export interface AiUsage {
  label: string
  tokens: number
  /** Dollars; null when the cost isn't known. */
  cost: number | null
  costEstimated: boolean
}

/**
 * Readyset-hosted calls go through Keyservice and carry what it charged. A user's own
 * Claude key is priced from published rates. Records from before the provider was kept
 * name the hosted model but priced it at Claude rates, so that cost isn't shown.
 */
export function aiUsage(results: CompleteEvent): AiUsage | null {
  const llmInfo = results.formatted?.metadata?.llm_info
  const tokenUsage = results.llm_analysis?.token_usage
  if (!llmInfo && !tokenUsage) return null

  const model =
    llmInfo?.model || tokenUsage?.model || results.llm_analysis?.llm_model || ''
  const provider =
    llmInfo?.provider ??
    tokenUsage?.provider ??
    (model.startsWith('claude') ? 'claude' : model ? 'readyset' : null)
  const name = provider === 'claude' ? 'Claude' : 'Readyset AI'
  const label = model ? `${name} (${model})` : name

  let cost: number | null = null
  let costEstimated = false
  if (llmInfo) {
    cost = llmInfo.cost ?? null
    costEstimated = Boolean(llmInfo.cost_estimated)
  } else if (tokenUsage?.cost_usd != null) {
    cost = tokenUsage.cost_usd
  } else if (provider === 'claude' && tokenUsage) {
    cost = tokenUsage.estimated_cost_usd
    costEstimated = true
  }

  return {
    label,
    tokens: llmInfo?.tokens || tokenUsage?.total || 0,
    cost,
    costEstimated,
  }
}

/** "$0.0021", or "about $0.044" when estimated; null without a cost. */
export function formatAiCost(usage: AiUsage): string | null {
  if (usage.cost == null) return null
  const digits = usage.cost > 0 && usage.cost < 0.01 ? 4 : 3
  const amount = `$${usage.cost.toFixed(digits)}`
  return usage.costEstimated ? `about ${amount}` : amount
}

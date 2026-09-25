import { describe, expect, it } from 'vitest'
import type { CompleteEvent } from '../../../lib/api'
import { aiUsage, formatAiCost } from './aiUsage'

const complete = (fields: Record<string, unknown>) =>
  ({ success: true, ...fields }) as unknown as CompleteEvent

describe('aiUsage', () => {
  it('names the hosted model and shows what Keyservice charged', () => {
    const usage = aiUsage(
      complete({
        formatted: {
          metadata: {
            llm_info: {
              model: 'z-ai/glm-5.3-flash',
              provider: 'readyset',
              tokens: 4400,
              cost: 0.0021,
              cost_estimated: false,
            },
          },
        },
      })
    )
    expect(usage?.label).toBe('Readyset AI (z-ai/glm-5.3-flash)')
    expect(usage && formatAiCost(usage)).toBe('$0.0021')
  })

  it('never says Claude for a record without a provider or model', () => {
    const usage = aiUsage(
      complete({
        llm_analysis: {
          token_usage: { input: 1, output: 1, total: 2, estimated_cost_usd: 0.07 },
        },
      })
    )
    expect(usage?.label).toBe('Readyset AI')
    expect(usage && formatAiCost(usage)).toBeNull()
  })

  it('hides the Claude-priced cost of an older hosted record', () => {
    const usage = aiUsage(
      complete({
        llm_analysis: {
          llm_model: 'z-ai/glm-5.3-flash',
          token_usage: {
            input: 6511,
            output: 1633,
            total: 8144,
            estimated_cost_usd: 0.044,
          },
        },
      })
    )
    expect(usage?.label).toBe('Readyset AI (z-ai/glm-5.3-flash)')
    expect(usage?.tokens).toBe(8144)
    expect(usage?.cost).toBeNull()
  })

  it('marks an own-key Claude cost as estimated', () => {
    const usage = aiUsage(
      complete({
        llm_analysis: {
          llm_model: 'claude-sonnet-4-5',
          token_usage: {
            input: 1500,
            output: 500,
            total: 2000,
            estimated_cost_usd: 0.012,
            provider: 'claude',
          },
        },
      })
    )
    expect(usage?.label).toBe('Claude (claude-sonnet-4-5)')
    expect(usage && formatAiCost(usage)).toBe('about $0.012')
  })
})

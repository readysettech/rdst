import { describe, expect, it } from 'vitest'
import { llmAnalysisText } from './llmAnalysisText'

describe('llmAnalysisText', () => {
  it("writes the model's analysis as readable sections", () => {
    expect(
      llmAnalysisText({
        performance_assessment: {
          overall_rating: 'poor',
          efficiency_score: 42,
          primary_concerns: ['Sequential scan on posts'],
        },
        execution_analysis: {
          scan_efficiency: 'Reads every row of posts.',
          bottlenecks: [],
        },
        index_recommendations: [
          {
            table: 'posts',
            columns: ['creationdate', 'owneruserid'],
            index_type: 'btree',
            estimated_impact: 'high',
            rationale: 'Filters on creationdate first.',
            sql: 'CREATE INDEX ON posts (creationdate, owneruserid);',
          },
        ],
        rewrite_suggestions: [],
        optimization_opportunities: [],
      })
    ).toBe(
      'Assessment:\nRated poor, efficiency 42/100\n- Sequential scan on posts\n\n' +
        'Execution:\nReads every row of posts.\n\n' +
        'Index on posts (creationdate, owneruserid):\nFilters on creationdate first.\n' +
        'CREATE INDEX ON posts (creationdate, owneruserid);'
    )
  })

  it('has nothing to report without an analysis', () => {
    expect(llmAnalysisText(null)).toBe('')
  })
})

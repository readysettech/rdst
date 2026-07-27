import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import {
  effortVariant,
  formatReportNumber,
  healthScoreColor,
  impactVariant,
} from '../../../lib/auditReportFormat'
import {
  analysisItemText,
  optimizationPriorityParts,
} from '../../../lib/auditReportModel'
import type { WorkloadAnalysis } from '../../../types/audit'
import { SectionCard } from './ReportPrimitives'

export function BulletList({ items }: { items: unknown[] }) {
  return (
    <VStack className="gap-2 items-stretch">
      {items.map((item, index) => (
        <HStack key={index} className="gap-2 items-start">
          <div className="w-1.5 h-1.5 rounded-full bg-content-layout-3 mt-2 shrink-0" />
          <Text level="body-small" className="text-content-layout-2 min-w-0">
            {analysisItemText(item)}
          </Text>
        </HStack>
      ))}
    </VStack>
  )
}

export function WorkloadAnalysisView({
  analysis,
  content,
}: {
  analysis: WorkloadAnalysis
  content: 'details' | 'next-steps'
}) {
  const bottlenecks = analysis.top_bottlenecks || []
  const capacityInsights = analysis.capacity_insights || []
  const priorities = analysis.optimization_priorities || []
  const hasScore =
    analysis.health_score !== undefined && analysis.health_score !== null

  return (
    <VStack className="gap-6 items-stretch w-full">
      {content !== 'next-steps' && (
        <SectionCard icon="document-validation" title="Workload Analysis">
          <div className="p-5">
            <div className="grid grid-cols-[auto_1fr] gap-6 items-start">
              {hasScore && (
                <VStack className="gap-1 items-center px-4">
                  <Text
                    level="headline-1"
                    className={`tabular-nums ${healthScoreColor(analysis.health_score!)}`}
                  >
                    {formatReportNumber(analysis.health_score, 0)}
                  </Text>
                  <Tag
                    variant={
                      analysis.health_score! >= 75
                        ? 'positive'
                        : analysis.health_score! >= 60
                          ? 'warning'
                          : 'negative'
                    }
                    modifier="ghost"
                    label="SCORE"
                  />
                </VStack>
              )}
              <VStack className="gap-3 items-start min-w-0">
                {analysis.workload_characterization && (
                  <Text level="body-small" className="text-content-layout-2">
                    {analysis.workload_characterization}
                  </Text>
                )}
                {analysis.read_write_ratio && (
                  <HStack className="gap-2 items-center">
                    <Text
                      level="caption"
                      className="text-content-layout-3 uppercase tracking-wider"
                    >
                      Read / Write
                    </Text>
                    <Tag
                      size="small"
                      variant="informative"
                      modifier="ghost"
                      label={analysis.read_write_ratio}
                    />
                  </HStack>
                )}
              </VStack>
            </div>
          </div>
        </SectionCard>
      )}

      {content !== 'next-steps' && bottlenecks.length > 0 && (
        <SectionCard icon="alert" title="Top Bottlenecks">
          <div className="p-5">
            <BulletList items={bottlenecks} />
          </div>
        </SectionCard>
      )}

      {content !== 'details' && capacityInsights.length > 0 && (
        <SectionCard icon="database" title="Capacity Insights">
          <div className="p-5">
            <BulletList items={capacityInsights} />
          </div>
        </SectionCard>
      )}

      {content !== 'details' && priorities.length > 0 && (
        <SectionCard icon="observe" title="Optimization Priorities">
          <div className="p-5">
            <VStack className="gap-3 items-stretch">
              {priorities.map((item, index) => {
                const priority = optimizationPriorityParts(item)
                return (
                  <HStack key={index} className="gap-3 items-start">
                    <div className="w-6 h-6 rounded-md bg-surface-primary-soft flex items-center justify-center shrink-0">
                      <Text
                        level="caption"
                        className="text-content-primary-soft font-semibold"
                      >
                        {index + 1}
                      </Text>
                    </div>
                    <VStack className="gap-1.5 items-start min-w-0">
                      <Text
                        level="body-small"
                        className="text-content-layout-2"
                      >
                        {priority.text}
                      </Text>
                      <HStack className="gap-1.5 items-center flex-wrap">
                        {priority.type && (
                          <Tag
                            size="small"
                            variant="informative"
                            modifier="ghost"
                            label={priority.type}
                          />
                        )}
                        {priority.effort && (
                          <Tag
                            size="small"
                            variant={effortVariant(priority.effort)}
                            modifier="ghost"
                            label={`${priority.effort} effort`}
                          />
                        )}
                        {priority.impact && (
                          <Tag
                            size="small"
                            variant={impactVariant(priority.impact)}
                            modifier="ghost"
                            label={`${priority.impact} impact`}
                          />
                        )}
                      </HStack>
                    </VStack>
                  </HStack>
                )
              })}
            </VStack>
          </div>
        </SectionCard>
      )}
    </VStack>
  )
}

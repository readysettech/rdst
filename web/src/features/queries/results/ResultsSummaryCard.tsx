import { Card } from '@rs/ui-new/card-2'
import { Icon } from '@rs/ui-new/icon'
import { m, useReducedMotion } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { getTransition } from '@rs/ui-new/transition'
import type { ExplainResults } from '../../../lib/api'
import { PerformanceScoreGauge } from './PerformanceScoreGauge'
import { ResultFact } from './ResultFact'
import { ResultSectionIndex } from './ResultSectionIndex'
import { resultToneStyles } from './resultStyles'
import {
  getRatingDescription,
  getRatingTitle,
  getResultTone,
  getScoreTone,
  type ResultPerformance,
} from './resultsSelectors'

function formatDuration(value?: number) {
  if (typeof value !== 'number') return '—'
  if (value > 0 && value < 1) return '<1 ms'
  return `${value.toFixed(2)} ms`
}

function formatNumber(value?: number) {
  return typeof value === 'number' ? value.toLocaleString() : '—'
}

function formatCost(value?: number) {
  return typeof value === 'number'
    ? value.toLocaleString(undefined, { maximumFractionDigits: 2 })
    : '—'
}

export function ResultsSummaryCard({
  performance,
  explainResults,
}: {
  performance?: ResultPerformance
  explainResults?: ExplainResults | null
}) {
  const tone = getResultTone(performance?.rating)
  const style = resultToneStyles[tone]
  const ratingTitle = getRatingTitle(performance?.rating)
  const ratingLabel = performance?.rating
    ? `${performance.rating.charAt(0).toUpperCase()}${performance.rating.slice(1)}`
    : 'Measured only'
  const score = performance?.score
  const hasScore = typeof score === 'number'
  const scoreTone = getScoreTone(score)
  const primaryConcern = performance?.concerns[0]
  const shouldReduceMotion = useReducedMotion()
  const entrance = {
    ...getTransition('cubicSlow'),
    duration: 0.7,
  }

  return (
    <Card>
      <Card.Header className="items-stretch gap-3 tablet:flex-row tablet:items-center tablet:justify-between">
        <HStack className="min-w-0 items-center gap-3">
          <ResultSectionIndex value={1} />
          <VStack className="min-w-0 items-start gap-1">
            <Card.Title>
              {performance ? 'Performance score' : 'Measured performance'}
            </Card.Title>
            <Card.Description>
              {performance
                ? 'Measured execution, plan data, and model assessment.'
                : 'Execution-plan data is available without a model assessment.'}
            </Card.Description>
          </VStack>
        </HStack>
        <div className="self-end tablet:self-auto">
          <Tag
            variant={performance ? style.tag : 'neutral'}
            modifier="ghost"
            label={ratingLabel}
          />
        </div>
      </Card.Header>

      <Card.Content className="p-4">
        {performance ? (
          <div className="grid gap-4 desktop:grid-cols-3">
            <div className="relative flex min-h-64 items-center justify-center overflow-hidden">
              {hasScore ? (
                <PerformanceScoreGauge score={score} tone={scoreTone} />
              ) : (
                <div className="flex h-48 w-48 shrink-0 items-center justify-center rounded-full bg-surface-layout-1/80 shadow-elevation-1">
                  <Icon
                    name={style.icon}
                    label={ratingTitle}
                    className={`h-10 w-10 ${style.text}`}
                  />
                </div>
              )}
            </div>

            <VStack className="items-start justify-center gap-5 p-6 desktop:col-span-2">
              <m.div
                className="flex flex-col items-start gap-1"
                initial={
                  shouldReduceMotion
                    ? false
                    : {
                        opacity: 0,
                        y: 8,
                      }
                }
                animate={{ opacity: 1, y: 0 }}
                transition={
                  shouldReduceMotion
                    ? { duration: 0 }
                    : {
                        ...entrance,
                        delay: 0.14,
                      }
                }
              >
                <Text
                  as="h2"
                  level="headline-2"
                  className="text-content-layout-1"
                >
                  {ratingTitle}
                </Text>
                <Text
                  level="body-small"
                  className="max-w-xl leading-relaxed text-content-layout-2"
                >
                  {getRatingDescription(performance.rating)}
                </Text>
              </m.div>

              {primaryConcern ? (
                <m.div
                  className={`flex flex-col items-start gap-1.5 rounded-xl border-2 px-4 py-3 ${style.border}`}
                  initial={
                    shouldReduceMotion
                      ? false
                      : {
                          opacity: 0,
                          y: 8,
                        }
                  }
                  animate={{ opacity: 1, y: 0 }}
                  transition={
                    shouldReduceMotion
                      ? { duration: 0 }
                      : {
                          ...entrance,
                          delay: 0.24,
                        }
                  }
                >
                  <HStack className="items-center gap-1.5">
                    <Icon
                      name={style.icon}
                      label=""
                      aria-hidden="true"
                      className={`h-3.5 w-3.5 shrink-0 ${style.text}`}
                    />
                    <Text level="label-extra-small" className={style.text}>
                      Key finding
                    </Text>
                  </HStack>
                  <Text level="body-small" className="text-content-layout-2">
                    {primaryConcern}
                  </Text>
                </m.div>
              ) : null}
            </VStack>
          </div>
        ) : (
          <div className="grid gap-4 desktop:grid-cols-3">
            <div className="flex min-h-64 items-center justify-center">
              <VStack className="items-center gap-3 text-center">
                <div className="flex h-16 w-16 items-center justify-center rounded-3xl border border-border-info-soft bg-surface-info-soft shadow-glow-info">
                  <Icon
                    name="speedometer"
                    label=""
                    aria-hidden="true"
                    className="h-7 w-7 text-content-info-soft"
                  />
                </div>
                <VStack className="items-center gap-0.5">
                  <Text level="headline-4" className="text-content-layout-1">
                    No score
                  </Text>
                  <Text level="caption" className="text-content-layout-3">
                    Measured plan only
                  </Text>
                </VStack>
              </VStack>
            </div>

            <VStack className="items-start justify-center gap-5 p-6 desktop:col-span-2">
              <VStack className="items-start gap-1">
                <Text level="headline-2" className="text-content-layout-1">
                  Execution data is ready
                </Text>
                <Text
                  level="body-small"
                  className="max-w-xl leading-relaxed text-content-layout-2"
                >
                  The query was measured successfully, but the analysis model
                  did not return a score.
                </Text>
              </VStack>

              <div className="flex max-w-xl items-start gap-3 rounded-xl border border-border-info-soft px-4 py-3">
                <Icon
                  name="info"
                  label=""
                  aria-hidden="true"
                  className="mt-0.5 h-4 w-4 shrink-0 text-content-info-soft"
                />
                <VStack className="items-start gap-1">
                  <Text
                    level="label-extra-small"
                    className="text-content-info-soft"
                  >
                    Model assessment unavailable
                  </Text>
                  <Text level="body-small" className="text-content-layout-2">
                    The execution metrics below are measured values and remain
                    available for review.
                  </Text>
                </VStack>
              </div>
            </VStack>
          </div>
        )}
      </Card.Content>

      {explainResults ? (
        <div className="grid gap-1 tablet:grid-cols-3">
          <Card.Content className="p-4">
            <ResultFact
              label="Execution time"
              value={formatDuration(explainResults.execution_time_ms)}
              icon="speedometer"
            />
          </Card.Content>
          <Card.Content className="p-4">
            <ResultFact
              label="Scanned → returned"
              value={`${formatNumber(explainResults.rows_examined)} → ${formatNumber(explainResults.rows_returned)}`}
              icon="layers"
            />
          </Card.Content>
          <Card.Content className="p-4">
            <ResultFact
              label="Planner cost"
              value={formatCost(explainResults.cost_estimate)}
              icon="observe"
            />
          </Card.Content>
        </div>
      ) : null}
    </Card>
  )
}

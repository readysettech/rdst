import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import type { AnalysisRerunReason } from '../../../lib/analytics'
import type { AnalysisHistoryEntry } from '../../../lib/api'
import { analysisOutcome } from './resultsSelectors'
import { relativeAge, stalenessNote } from './storedAnalysis'

interface StoredAnalysisHeaderProps {
  createdAt: string
  target: string
  overallRating: string
  efficiencyScore: number | null
  /** False for analyses stored before the full result body was kept. */
  hasBody: boolean
  history: AnalysisHistoryEntry[]
  currentAnalysisId: string
  onOpenAnalysis: (analysisId: string) => void
  onReRun: (reason: AnalysisRerunReason) => void
}

/**
 * Why the user picks a re-run, taken from what is actually true of the record
 * they are looking at rather than from a menu of reasons nobody would read.
 */
function rerunReason(hasBody: boolean, createdAt: string): AnalysisRerunReason {
  if (!hasBody) return 'missing-body'
  return stalenessNote(createdAt) ? 'stale' : 'manual'
}

/**
 * The strip above a stored analysis: when it ran, what it concluded, whether
 * age has made it doubtful, and the two ways forward — re-measure now, or open
 * an earlier run. Re-run sits next to the staleness sentence that motivates it.
 */
export function StoredAnalysisHeader({
  createdAt,
  target,
  overallRating,
  efficiencyScore,
  hasBody,
  history,
  currentAnalysisId,
  onOpenAnalysis,
  onReRun,
}: StoredAnalysisHeaderProps) {
  const outcome = analysisOutcome({
    overall_rating: overallRating,
    efficiency_score: efficiencyScore,
  })
  const staleness = stalenessNote(createdAt)
  const olderRuns = history.filter(
    (entry) => entry.analysis_id !== currentAnalysisId
  )

  return (
    <Card data-testid="stored-analysis-header">
      <Card.Content className="flex flex-col items-start justify-between gap-4 p-4 tablet:flex-row tablet:items-center">
        <VStack className="min-w-0 items-start gap-1">
          <HStack className="flex-wrap items-center gap-2">
            <Text level="label-medium" className="text-content-layout-1">
              Viewing analysis from {relativeAge(createdAt)}
            </Text>
            <Show when={outcome}>
              {(value) => (
                <Tag
                  size="small"
                  variant={value.tone}
                  modifier="ghost"
                  label={value.label}
                />
              )}
            </Show>
          </HStack>
          <Text level="body-small" className="text-content-layout-3">
            {staleness
              ? staleness
              : `Saved from an earlier run against ${target || 'this database'}. Nothing re-ran to show it.`}
          </Text>
        </VStack>

        <HStack className="shrink-0 flex-wrap items-center gap-2">
          <Show when={olderRuns.length > 0}>
            <Dropdown>
              {/* The trigger is the button itself: a wrapper element would
                  take the menu's keyboard and ARIA wiring with it, leaving a
                  control that only opens on a mouse press. */}
              <Dropdown.Trigger asChild>
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  icon="layers"
                  iconPosition="left"
                  label="Analysis history"
                />
              </Dropdown.Trigger>
              <Dropdown.Content align="end" className="min-w-64">
                <Dropdown.Label>Earlier runs</Dropdown.Label>
                {history.map((entry) => {
                  const entryOutcome = analysisOutcome(entry)
                  return (
                    <Dropdown.Item
                      key={entry.analysis_id}
                      active={entry.analysis_id === currentAnalysisId}
                      label={
                        entryOutcome
                          ? `${relativeAge(entry.created_at)} · ${entryOutcome.label}`
                          : relativeAge(entry.created_at)
                      }
                      onSelect={() => onOpenAnalysis(entry.analysis_id)}
                    />
                  )
                })}
              </Dropdown.Content>
            </Dropdown>
          </Show>
          <Button
            variant="primary"
            modifier="solid"
            size="small"
            icon="play"
            iconPosition="left"
            label="Re-run analysis"
            onClick={() => onReRun(rerunReason(hasBody, createdAt))}
          />
        </HStack>
      </Card.Content>
    </Card>
  )
}

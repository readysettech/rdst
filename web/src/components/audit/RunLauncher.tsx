/**
 * Run controls for the Health Check launcher: capture window, requirement
 * checklist slot, and the run/cancel actions.
 */

import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { IconButton } from '@rs/ui-new/icon-button'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { formatDuration } from '../../lib/auditReportFormat'
import { cancelActiveAudit } from '../../lib/auditSession'

const CAPTURE_DURATIONS: Array<{
  label: string
  long: string
  seconds: number
}> = [
  { label: '30s', long: '30 seconds', seconds: 30 },
  { label: '1m', long: '1 minute', seconds: 60 },
  { label: '5m', long: '5 minutes', seconds: 300 },
  { label: '15m', long: '15 minutes', seconds: 900 },
  { label: '1h', long: '1 hour', seconds: 3600 },
]

export function RunLauncher({
  scopeControl,
  requirementsNotice,
  showRequirementsButton,
  requirementsBusy,
  onCheckRequirements,
  disabled,
  active,
  onRun,
  captureDuration,
  onDurationChange,
  selectedTargets,
  runSolid,
}: {
  scopeControl: React.ReactNode
  requirementsNotice: React.ReactNode
  showRequirementsButton: boolean
  requirementsBusy: boolean
  onCheckRequirements: () => void
  disabled: boolean
  active: boolean
  onRun: () => void
  captureDuration: number
  onDurationChange: (seconds: number) => void
  selectedTargets: string[]
  runSolid: boolean
}) {
  const durationOptions = CAPTURE_DURATIONS.map((d) => ({
    value: String(d.seconds),
    label: d.long,
  }))
  const durationLabel =
    CAPTURE_DURATIONS.find((duration) => duration.seconds === captureDuration)
      ?.long ?? formatDuration(captureDuration)
  const selectedCount = selectedTargets.length
  const targetCopy =
    selectedCount === 1
      ? selectedTargets[0]
      : `${selectedCount} targets: ${selectedTargets.join(', ')}`

  return (
    <VStack className="gap-4 items-stretch">
      {scopeControl}
      <Card className="w-full" data-testid="health-check-run-card">
        <Card.Content>
          <VStack className="gap-4 items-stretch p-1">
            <HStack className="gap-4 items-end flex-wrap">
              <VStack className="gap-1.5 items-start min-w-52">
                <HStack className="gap-1.5 items-center">
                  <Text level="caption" className="text-content-layout-3">
                    Duration
                  </Text>
                  <IconButton
                    icon="info"
                    label="About capture duration"
                    tooltip="Set a capture window during which Health Check monitors the traffic running against your database to discover query patterns, slow queries, and anything affecting performance."
                    size="small"
                    modifier="ghost"
                    classMerge="bg-transparent text-content-layout-3 hover:text-content-layout-2"
                  />
                </HStack>
                <BaseInputSelect
                  name="capture-duration"
                  options={durationOptions}
                  value={String(captureDuration)}
                  onValueChange={(value) => onDurationChange(Number(value))}
                  disabled={active}
                  triggerClassName="h-9"
                />
              </VStack>
            </HStack>
            <Show when={!active}>{requirementsNotice}</Show>
            <Show
              when={selectedCount > 0}
              fallback={
                <Text level="caption" className="text-content-layout-3">
                  Select one or more targets to check
                </Text>
              }
            >
              <Text level="body-small" className="text-content-layout-2">
                {`Health Check connects read-only to ${targetCopy} and snapshots database configuration and vital signs. For ${durationLabel}, it watches real live query traffic to find slow queries, hot spots, and sizing issues, then reports which queries Readyset could cache and accelerate. Nothing is changed on your database.`}
              </Text>
            </Show>
            <HStack className="gap-3 items-center flex-wrap">
              <div className="min-w-56">
                <Button
                  variant="primary"
                  modifier={runSolid ? 'solid' : 'outline'}
                  label="Run health check"
                  icon="play"
                  iconPosition="left"
                  onClick={onRun}
                  disabled={disabled}
                  fullWidth
                />
              </div>
              <Show
                when={showRequirementsButton && captureDuration > 0 && !active}
              >
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  label="Check requirements"
                  loading={requirementsBusy}
                  onClick={onCheckRequirements}
                  disabled={active}
                />
              </Show>
              <Show when={active}>
                <Button
                  variant="negative"
                  modifier="outline"
                  size="small"
                  label="Cancel"
                  icon="close"
                  iconPosition="left"
                  onClick={cancelActiveAudit}
                />
                <IconButton
                  icon="info"
                  label="A health check is already running"
                  tooltip="A health check is already running. Cancel it before starting another."
                  size="small"
                  modifier="ghost"
                  classMerge="bg-transparent text-content-layout-3 hover:text-content-layout-2"
                />
              </Show>
            </HStack>
          </VStack>
        </Card.Content>
      </Card>
    </VStack>
  )
}

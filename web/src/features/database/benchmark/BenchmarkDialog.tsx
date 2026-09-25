// Shared query selection and benchmark settings for the Database page and query detail.

import { BaseInputRadioGroup } from '@rs/ui-new/base-input-radio-group'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalDescription,
  ModalTitle,
} from '@rs/ui-new/modal'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import { useEffect, useRef, useState } from 'react'
import { TargetConnectivityNotice, TargetLockNotice } from '@/components'
import type { BenchmarkRunChoice } from '@/features/caching/load-test/useLoadTestController'
import { BENCHMARK_EXECUTION_CAP } from '@/features/caching/load-test/useLoadTestController'
import { plural } from '../../monitor-kit/format'
import { BenchmarkQueryPicker } from './BenchmarkQueryPicker'
import { maxRuns, PRESETS, type PresetId, presetById } from './benchmarkFlow'
import type { QueryBenchmark } from './useQueryBenchmark'

export const READYSET_EXPLAINER =
  'Readyset is a caching layer that sits between your app and your database. It keeps the results of the queries you pick in memory and updates them as your data changes, so repeated reads come back much faster. Both Readyset choices use a temporary container on this computer.'

function WhatsReadyset() {
  const [open, setOpen] = useState(false)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Pressable
          className="inline rounded-sm text-content-primary-soft underline decoration-dotted underline-offset-2 hover:text-content-primary-solid"
          onPointerEnter={() => setOpen(true)}
          onPointerLeave={() => setOpen(false)}
        >
          What's Readyset?
        </Pressable>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="max-w-80 px-3 py-2.5"
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        <p className="text-body-small text-content-layout-1">
          {READYSET_EXPLAINER}
        </p>
      </PopoverContent>
    </Popover>
  )
}

function LoadWarning({
  presetId,
  target,
  remote,
}: {
  presetId: PresetId
  target: string
  remote: boolean
}) {
  const preset = presetById(presetId)
  const runs = maxRuns(preset)
  return (
    <div
      className="flex gap-3 rounded-panel border border-border-warning-soft bg-surface-warning-soft/15 px-4 py-3"
      data-testid="benchmark-load-warning"
    >
      <Icon
        name="alert"
        label=""
        aria-hidden="true"
        className="mt-0.5 h-4 w-4 shrink-0 text-content-warning-soft"
      />
      <div className="min-w-0 space-y-1">
        <Text as="p" level="label-medium" className="text-content-layout-1">
          This runs against your live database and adds load to it while it
          runs.
        </Text>
        <Text as="p" level="body-small" className="text-content-layout-2">
          {preset.intensity}:{' '}
          {runs
            ? `at most about ${runs.toLocaleString()} runs over ${preset.durationSeconds} seconds.`
            : `it runs as fast as the database answers for ${plural(preset.durationSeconds / 60, 'minute')}, up to ${BENCHMARK_EXECUTION_CAP.toLocaleString()} runs.`}{' '}
          Only reads run, and you can stop it at any time.
        </Text>
        {remote ? (
          <Text as="p" level="body-small" className="text-content-layout-2">
            {target} is a remote database. If it serves production traffic, pick
            a quiet time.
          </Text>
        ) : null}
      </div>
    </div>
  )
}

export interface BenchmarkDialogProps {
  open: boolean
  onClose: () => void
  bench: QueryBenchmark
  target: string
  queryLabel: string
}

export function BenchmarkDialog({
  open,
  onClose,
  bench,
  target,
  queryLabel,
}: BenchmarkDialogProps) {
  const { controller, readyset } = bench
  const [starting, setStarting] = useState(false)
  // The dialog closes once the run it started shows up; a failed check keeps it open.
  const runAtStart = useRef<string | undefined>(undefined)
  useEffect(() => {
    if (!starting) return
    if (bench.run && bench.run.runId !== runAtStart.current) {
      setStarting(false)
      onClose()
    } else if (controller.connectivity.failure) setStarting(false)
  }, [starting, bench.run, controller.connectivity.failure, onClose])
  const readysetOff = readyset.state === 'unavailable'
  const gate = controller.destinationLock.isLocked ? (
    <TargetLockNotice
      message={controller.destinationLock.message}
      requirements={controller.destinationLock.missingTargetRequirements}
      keyringAvailable={controller.destinationLock.keyringAvailable}
    />
  ) : controller.connectivity.failure ? (
    <TargetConnectivityNotice
      target={controller.destinationLock.targetName ?? target}
      failure={controller.connectivity.failure}
      isChecking={controller.connectivity.isChecking}
      onRetry={() => void controller.connectivity.ensureReachable()}
    />
  ) : null

  const start = () => {
    runAtStart.current = bench.run?.runId
    setStarting(true)
    // A start the backend refused leaves no run; let the button work again.
    void bench
      .start()
      .finally(() => window.setTimeout(() => setStarting(false), 1500))
  }

  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContentContainer open={open}>
        <ModalContent
          size="large"
          className="max-tablet:max-w-[calc(100vw-2rem)] overflow-hidden p-0"
          data-testid="benchmark-dialog"
        >
          <div className="space-y-1 border-b border-border-layout-1 px-5 pt-5 pb-4 pr-14">
            <ModalTitle className="h-auto">Benchmark queries</ModalTitle>
            <ModalDescription className="text-left">
              RDST runs{' '}
              <span className="font-medium text-content-layout-2">
                {controller.selectedCount === 1
                  ? (controller.selectedQueryById?.get(
                      controller.selectedQueries?.[0]
                    )?.name ?? queryLabel)
                  : `${controller.selectedCount} queries`}
              </span>{' '}
              over and over on {target} and measures how fast it answers
              (latency) and how many times a second it runs (throughput).
            </ModalDescription>
          </div>

          <div className="max-h-[65dvh] space-y-5 overflow-y-auto px-5 py-5">
            <BenchmarkQueryPicker controller={controller} />
            <div className="space-y-2">
              <p
                className="text-label-medium text-content-layout-1"
                id="benchmark-length"
              >
                How long
              </p>
              <BaseInputRadioGroup
                aria-labelledby="benchmark-length"
                value={bench.presetId}
                onValueChange={(value) => bench.setPresetId(value as PresetId)}
                options={PRESETS.map((preset) => ({
                  value: preset.id,
                  label: preset.label,
                  description: preset.load,
                  badge: (
                    <Text level="caption" className="text-content-layout-3">
                      {preset.intensity}
                    </Text>
                  ),
                }))}
              />
            </div>

            <LoadWarning
              presetId={bench.presetId}
              target={target}
              remote={controller.destinationIsRemote}
            />

            <div className="space-y-2">
              <p className="text-label-medium" id="benchmark-destination">
                Where to run
              </p>
              <BaseInputRadioGroup
                aria-labelledby="benchmark-destination"
                value={bench.runChoice}
                onValueChange={(value) =>
                  bench.setRunChoice(value as BenchmarkRunChoice)
                }
                options={[
                  {
                    value: 'source',
                    label: 'Source only',
                    description: 'Measure your database.',
                  },
                  {
                    value: 'both',
                    label: 'Source plus Readyset',
                    disabled: readysetOff,
                    description: readyset.note,
                  },
                  {
                    value: 'readyset',
                    label: 'Readyset only',
                    disabled: readysetOff,
                    description: readyset.note,
                  },
                ]}
              />
              <WhatsReadyset />
              {bench.runChoice !== 'source' ? (
                <Text as="p" level="caption" className="text-content-layout-3">
                  The first Readyset run downloads a small container. RDST
                  starts and manages it in the background.
                </Text>
              ) : null}
              {bench.runChoice === 'both' ? (
                <Text as="p" level="caption" className="text-content-layout-3">
                  Both sides run the same queries at the same pace. If Readyset
                  can't start, the benchmark still measures your database.
                </Text>
              ) : null}
            </div>

            {controller.error ? (
              <p
                role="alert"
                className="text-body-small text-content-negative-soft"
              >
                {controller.error}
              </p>
            ) : null}
            {gate}
          </div>

          <div className="flex flex-col-reverse gap-3 border-t border-border-layout-1 px-5 py-4 tablet:flex-row tablet:items-center tablet:justify-end">
            <div className="flex items-center justify-end gap-2">
              {bench.blocker && !gate ? (
                <Text
                  level="caption"
                  className="text-content-layout-3"
                  data-testid="benchmark-blocker"
                >
                  {bench.blocker}
                </Text>
              ) : null}
              <Button
                variant="primary"
                modifier="ghost"
                label="Cancel"
                onClick={onClose}
              />
              <Button
                variant="primary"
                modifier="solid"
                icon="play"
                iconPosition="left"
                label={starting ? 'Starting' : 'Start benchmark'}
                disabled={Boolean(bench.blocker) || Boolean(gate) || starting}
                onClick={start}
              />
            </div>
          </div>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}

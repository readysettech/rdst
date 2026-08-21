import { CopyButton } from '@rs/ui-new/copy-button'
import { IconButton } from '@rs/ui-new/icon-button'
import {
  Modal,
  ModalClose,
  ModalContent,
  ModalContentContainer,
} from '@rs/ui-new/modal'
import { LayoutGroup, m } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { getTransition } from '@rs/ui-new/transition'
import { useState } from 'react'
import { QueryCard, type QueryCardProps } from '../../../components/QueryCard'
import { QueryCardImpact } from '../../../components/QueryCardImpact'
import {
  formatMeta,
  formatMs,
  formatTimestamp,
  shortHash,
} from '../../../lib/formatters'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import { SavedQueryRow } from '../saved/SavedQueryRow'
import {
  reportsCacheTestRun,
  SavedQueryTestPanel,
} from '../saved/SavedQueryTestPanel'
import {
  QueryCardResultExperiment,
  QueryCardResultVariants,
} from './QueryCardResultExperiment'
import { QueryLabBadges, QueryLabMetric, queryLabMetric } from './QueryLabQuery'
import {
  type QueriesLabController,
  queryLabParameterLabel,
  queryLabTitle,
} from './queryLabModel'

const INTERACTIVE_PREVIEW_LIMIT = 4

function contextLabel(
  entry: QueryRegistryEntry,
  controller: QueriesLabController,
  index: number
) {
  if (entry.is_new) return 'New observation'
  if (controller.rowActions.isCached(entry.hash)) return 'Cached query'
  if (queryLabParameterLabel(entry) !== 'No parameters') {
    return 'Parameterized query'
  }
  if (index === 0) return 'Primary library card'
  return 'Saved query'
}

function ContextHeading({ label }: { label: string }) {
  return (
    <HStack className="items-center justify-between gap-3">
      <Text level="label-small" className="text-content-layout-2">
        {label}
      </Text>
      <Tag
        size="small"
        variant="neutral"
        modifier="ghost"
        label="Interactive"
      />
    </HStack>
  )
}

function ImpactRail({
  entry,
  detailed = false,
  target,
}: {
  entry: QueryRegistryEntry
  detailed?: boolean
  target?: string | null
}) {
  const impact = queryLabMetric(entry, 'impact')
  const lastObserved = entry.last_observed_at ?? entry.last_analyzed_at
  const parameterKeys = Object.keys(entry.most_recent_params ?? {})

  return (
    <VStack
      className={`h-full min-h-44 items-stretch justify-between ${detailed ? 'gap-16' : 'gap-6'}`}
    >
      <VStack className="items-start gap-1">
        <Text level="caption" className="text-content-layout-3">
          {impact.label}
        </Text>
        <Text level="stat-hero" className="text-content-layout-1 tabular-nums">
          {impact.value}
        </Text>
        <Text level="caption" className="text-content-layout-3">
          Across the captured workload
        </Text>
      </VStack>
      <VStack className="items-stretch gap-3">
        <QueryLabMetric entry={entry} kind="frequency" layout="inline" />
        <QueryLabMetric entry={entry} kind="latency" layout="inline" />
        {detailed ? (
          <VStack className="items-stretch gap-3 border-t border-border-layout-1 pt-3">
            <QueryLabMetric entry={entry} kind="parameters" layout="inline" />
            {(entry.max_duration_ms ?? 0) > 0 ? (
              <HStack className="items-center justify-between gap-3">
                <Text level="caption" className="text-content-layout-3">
                  Max latency
                </Text>
                <Text level="mono-small" className="text-content-layout-1">
                  {formatMs(entry.max_duration_ms ?? 0)}
                </Text>
              </HStack>
            ) : null}
            {lastObserved ? (
              <HStack className="items-center justify-between gap-3">
                <Text level="caption" className="text-content-layout-3">
                  Last observed
                </Text>
                <Text
                  level="caption"
                  className="text-right text-content-layout-2"
                >
                  {formatTimestamp(lastObserved)}
                </Text>
              </HStack>
            ) : null}
            <div className="border-t border-border-layout-1 pt-3">
              <VStack className="items-stretch gap-3">
                <HStack className="items-center justify-between gap-3">
                  <Text level="caption" className="text-content-layout-3">
                    Hash
                  </Text>
                  <HStack className="items-center gap-1.5">
                    <Text level="mono-small" className="text-content-layout-1">
                      {shortHash(entry.hash)}
                    </Text>
                    <CopyButton text={entry.hash} />
                  </HStack>
                </HStack>
                <HStack className="items-center justify-between gap-3">
                  <Text level="caption" className="text-content-layout-3">
                    Target
                  </Text>
                  <Text level="mono-small" className="text-content-layout-1">
                    {entry.target ?? target ?? 'Default target'}
                  </Text>
                </HStack>
                {parameterKeys.length > 0 ? (
                  <HStack className="items-start justify-between gap-3">
                    <Text
                      level="caption"
                      className="pt-1 text-content-layout-3"
                    >
                      Values
                    </Text>
                    <HStack className="max-w-48 flex-wrap justify-end gap-1.5">
                      {parameterKeys.map((key) => (
                        <Tag
                          key={key}
                          size="small"
                          variant="neutral"
                          modifier="ghost"
                          label={key}
                        />
                      ))}
                    </HStack>
                  </HStack>
                ) : null}
                <HStack className="items-center justify-between gap-3">
                  <Text level="caption" className="text-content-layout-3">
                    Updated
                  </Text>
                  <Text
                    level="caption"
                    className="text-right text-content-layout-2"
                  >
                    {formatTimestamp(entry.last_analyzed)}
                  </Text>
                </HStack>
                {entry.first_analyzed ? (
                  <HStack className="items-center justify-between gap-3">
                    <Text level="caption" className="text-content-layout-3">
                      Created
                    </Text>
                    <Text
                      level="caption"
                      className="text-right text-content-layout-2"
                    >
                      {formatTimestamp(entry.first_analyzed)}
                    </Text>
                  </HStack>
                ) : null}
              </VStack>
            </div>
          </VStack>
        ) : null}
      </VStack>
    </VStack>
  )
}

function impactFooterMeta(
  entry: QueryRegistryEntry,
  controller: QueriesLabController
) {
  return formatMeta([
    queryLabParameterLabel(entry).toLowerCase(),
    `hash ${shortHash(entry.hash)}`,
    entry.target ?? controller.target,
  ])
}

function SelectablePreview({
  entry,
  controller,
  kind,
}: {
  entry: QueryRegistryEntry
  controller: QueriesLabController
  kind: 'current' | 'impact'
}) {
  const [selected, setSelected] = useState(false)
  const commonProps: QueryCardProps = {
    sql: entry.sql,
    title: (
      <Text
        level="label-medium"
        className="font-semibold text-content-layout-1"
      >
        {queryLabTitle(entry)}
      </Text>
    ),
    badges: <QueryLabBadges entry={entry} controller={controller} />,
    meta: formatMeta([
      'Benchmark selection',
      `hash ${shortHash(entry.hash)}`,
      entry.target ?? controller.target,
    ]),
    selectable: true,
    selected,
    selectionLabel: `Select ${queryLabTitle(entry)}`,
    onSelect: () => setSelected((value) => !value),
  }

  return kind === 'current' ? (
    <QueryCard {...commonProps} />
  ) : (
    <QueryCardImpact {...commonProps} rail={<ImpactRail entry={entry} />} />
  )
}

function QueryCardGallery({
  controller,
  kind,
}: {
  controller: QueriesLabController
  kind: 'current' | 'impact'
}) {
  const entries = controller.lab.queries.slice(0, INTERACTIVE_PREVIEW_LIMIT)

  return (
    <VStack className="items-stretch gap-6">
      {entries.map((entry, index) => (
        <section key={entry.hash} className="space-y-2">
          <ContextHeading label={contextLabel(entry, controller, index)} />
          <SavedQueryRow
            entry={entry}
            target={controller.target}
            state={controller.rowState}
            actions={controller.rowActions}
            renderCard={
              kind === 'impact'
                ? (props) => (
                    <QueryCardImpact
                      {...props}
                      meta={impactFooterMeta(entry, controller)}
                      rail={<ImpactRail entry={entry} />}
                    />
                  )
                : undefined
            }
          />
        </section>
      ))}

      {entries[0] ? (
        <section className="space-y-2">
          <ContextHeading label="Selectable benchmark card" />
          <SelectablePreview
            entry={entries[0]}
            controller={controller}
            kind={kind}
          />
        </section>
      ) : null}
    </VStack>
  )
}

function ImpactDetailsModal({
  entry,
  controller,
  onClose,
}: {
  entry: QueryRegistryEntry | null
  controller: QueriesLabController
  onClose: () => void
}) {
  if (!entry) return null

  const layoutId = `query-impact-details-${entry.hash}`
  const cacheTestRun = controller.rowActions.cacheRunFor(entry.hash)
  const showPerformanceDetails = reportsCacheTestRun(cacheTestRun)
  const isRenaming = controller.rowState.editingHash === entry.hash
  const isEditingSql = controller.rowState.editingSqlHash === entry.hash
  const isConfirmingDelete = controller.rowState.confirmingHash === entry.hash
  const closeModal = () => {
    if (isRenaming) controller.rowActions.cancelRename()
    if (isEditingSql) controller.rowActions.cancelEditSql()
    if (isConfirmingDelete) controller.rowActions.cancelDelete()
    onClose()
  }

  return (
    <Modal open onOpenChange={(open) => !open && closeModal()}>
      <ModalContentContainer open>
        <ModalContent
          layoutId={layoutId}
          sharedLayout
          innerClassName="!opacity-100"
          data-query-layout-modal={entry.hash}
          size="extra-large"
          hideClose
          title={`${queryLabTitle(entry)} details`}
          description="Expanded query evidence and performance details"
          className="inset-0 top-0 left-0 m-auto max-w-5xl border-0 bg-transparent p-0 shadow-none"
        >
          <m.div
            layout="position"
            className="h-full w-full !opacity-100"
            data-query-layout-content="modal"
          >
            <SavedQueryRow
              entry={entry}
              target={controller.target}
              state={controller.rowState}
              actions={controller.rowActions}
              animateEntry={false}
              renderCard={(props) => {
                if (props.selectable) return null

                return (
                  <QueryCardImpact
                    {...props}
                    sqlInitiallyExpanded
                    menu={
                      <HStack className="items-center gap-1">
                        {props.menu}
                        <ModalClose asChild>
                          <IconButton
                            autoFocus
                            variant="primary"
                            modifier="ghost"
                            size="small"
                            icon="close"
                            label="Close query details"
                          />
                        </ModalClose>
                      </HStack>
                    }
                    meta={undefined}
                    rail={
                      <ImpactRail
                        entry={entry}
                        detailed
                        target={controller.target}
                      />
                    }
                    railSize="wide"
                    detailsOpen={false}
                    onToggleDetails={undefined}
                    expansionPlacement="before-footer"
                    motionLayout
                    expansion={
                      showPerformanceDetails &&
                      cacheTestRun &&
                      !props.editor ? (
                        <SavedQueryTestPanel
                          run={cacheTestRun}
                          onDismissRun={controller.rowActions.dismissRun}
                          onClose={closeModal}
                        />
                      ) : undefined
                    }
                    className="border-border-layout-1 bg-surface-layout-2 shadow-elevation-3"
                  />
                )
              }}
            />
          </m.div>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}

function ImpactModalGallery({
  controller,
}: {
  controller: QueriesLabController
}) {
  const entries = controller.lab.queries.slice(0, INTERACTIVE_PREVIEW_LIMIT)
  const [activeEntryHash, setActiveEntryHash] = useState<string | null>(null)
  const resolvedActiveHash = activeEntryHash
    ? (controller.rowState.hashAliases[activeEntryHash] ?? activeEntryHash)
    : null
  const activeEntry = resolvedActiveHash
    ? (controller.lab.queries.find(
        (entry) => entry.hash === resolvedActiveHash
      ) ?? null)
    : null

  return (
    <LayoutGroup id="query-impact-details">
      <VStack className="items-stretch gap-6">
        {entries.map((entry, index) => {
          const layoutId = `query-impact-details-${entry.hash}`
          const isEntryActive = activeEntry?.hash === entry.hash
          return (
            <section key={entry.hash} className="space-y-2">
              <ContextHeading label={contextLabel(entry, controller, index)} />
              <SavedQueryRow
                entry={entry}
                target={controller.target}
                state={controller.rowState}
                actions={controller.rowActions}
                animateEntry={false}
                renderCard={(props) => {
                  if (props.selectable) {
                    return (
                      <QueryCardImpact
                        {...props}
                        meta={impactFooterMeta(entry, controller)}
                        rail={<ImpactRail entry={entry} />}
                      />
                    )
                  }

                  return (
                    <div
                      data-query-layout-source={entry.hash}
                      aria-hidden={isEntryActive}
                      className="relative"
                      style={{
                        opacity: isEntryActive ? 0 : 1,
                        pointerEvents: isEntryActive ? 'none' : undefined,
                        zIndex: isEntryActive ? 20 : undefined,
                      }}
                    >
                      <m.div
                        layoutId={layoutId}
                        className="!opacity-100"
                        transition={{ layout: getTransition('cubicSlow') }}
                      >
                        <m.div
                          layout="position"
                          className="h-full w-full !opacity-100"
                          data-query-layout-content="source"
                        >
                          <QueryCardImpact
                            {...props}
                            detailsOpen={activeEntry?.hash === entry.hash}
                            onToggleDetails={() => {
                              setActiveEntryHash(entry.hash)
                            }}
                            expansion={undefined}
                            motionLayout
                            meta={impactFooterMeta(entry, controller)}
                            rail={<ImpactRail entry={entry} />}
                          />
                        </m.div>
                      </m.div>
                    </div>
                  )
                }}
              />
            </section>
          )
        })}

        {entries[0] ? (
          <section className="space-y-2">
            <ContextHeading label="Selectable benchmark card" />
            <SelectablePreview
              entry={entries[0]}
              controller={controller}
              kind="impact"
            />
          </section>
        ) : null}

        {entries[0] ? (
          <section className="space-y-2">
            <ContextHeading label="Quick-test result anatomy" />
            <SavedQueryRow
              entry={entries[0]}
              target={controller.target}
              state={controller.rowState}
              actions={controller.rowActions}
              animateEntry={false}
              renderCard={(props) => {
                if (props.selectable) return null
                return <QueryCardResultExperiment card={props} />
              }}
            />
          </section>
        ) : null}

        {entries[0] ? (
          <section className="space-y-2">
            <ContextHeading label="Quick-test result directions" />
            <SavedQueryRow
              entry={entries[0]}
              target={controller.target}
              state={controller.rowState}
              actions={controller.rowActions}
              animateEntry={false}
              renderCard={(props) => {
                if (props.selectable) return null
                return <QueryCardResultVariants card={props} />
              }}
            />
          </section>
        ) : null}

        <ImpactDetailsModal
          entry={activeEntry}
          controller={controller}
          onClose={() => setActiveEntryHash(null)}
        />
      </VStack>
    </LayoutGroup>
  )
}

export function QueryLabVariantOne({
  controller,
}: {
  controller: QueriesLabController
}) {
  return <QueryCardGallery controller={controller} kind="current" />
}

export function QueryLabVariantTwo({
  controller,
}: {
  controller: QueriesLabController
}) {
  return <ImpactModalGallery controller={controller} />
}

export const QUERY_LAB_VARIANT_COMPONENTS = {
  '1': QueryLabVariantOne,
  '2': QueryLabVariantTwo,
} as const

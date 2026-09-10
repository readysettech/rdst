import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { EmptyState } from '@rs/ui-new/empty-state'
import { ErrorState } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { IconButton } from '@rs/ui-new/icon-button'
import { m } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Show } from '@rs/ui-new/show'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { getTransition } from '@rs/ui-new/transition'
import { toast } from '@rs/ui-new/use-toast'
import { createFileRoute } from '@tanstack/react-router'
import { type ReactNode, useEffect, useId, useRef, useState } from 'react'
import { ConnectionFailureActions } from '../components/ConnectionFailureActions'
import {
  SchemaAddMetricDialog,
  SchemaAddRelationshipDialog,
  SchemaAddTermDialog,
  SchemaEditColumnDialog,
  SchemaEditEnumDialog,
  SchemaEditTableDialog,
  SchemaEmptyState,
  SchemaGuidedSequence,
  SchemaManageMenu,
  SchemaMetricsList,
  SchemaReinitDialog,
  SchemaTableTree,
  SchemaTerminologyList,
} from '../components/schema'
import { TargetDropdown } from '../components/TargetDropdown'
import { TargetLockNotice } from '../components/TargetLockNotice'
import { useTargetResolution } from '../hooks/useTarget'
import { useBackgroundRun } from '../lib/backgroundRuns'
import {
  classifyError,
  isConnectionFailure,
  sanitizeWebError,
} from '../lib/errorContract'
import { useSchema } from '../lib/useSchema'
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock'
import type {
  AddColumnData,
  AddEnumData,
  AddMetricData,
  AddRelationshipData,
  AddTableData,
  AddTerminologyData,
  SchemaMetric,
  SchemaTable,
  SchemaTableColumn,
  SchemaTableRelationship,
  SchemaTerminology,
} from '../types/schema'

export const Route = createFileRoute('/schema')({
  component: SchemaPage,
})

const SEMANTIC_LAYER_TOOLTIP_LABEL =
  "The semantic layer lets you document business logic that isn't obvious from the schema alone, like enum meanings, domain terms, and relationships, so RDST can provide better AI recommendations."

// Promotes the tooltip's explanation into a persistent, self-evident value prop
// (redesign §Copy #1) — the one sentence that says why this screen matters.
const SEMANTIC_LAYER_SUBTITLE =
  "Teach RDST's AI what your data means, so Ask and Analyze write better SQL for this database."

type SchemaContentTab = 'tables' | 'terminology' | 'metrics'

// The design system's own info control: a named `IconButton` at a real target
// size, with the tooltip it already carries, in place of a hand-drawn letter in
// a 24px circle. [C-74]
function SemanticLayerInfoTooltip() {
  return (
    <IconButton
      icon="info"
      size="small"
      variant="primary"
      modifier="ghost"
      label="What is the semantic layer?"
      tooltip={SEMANTIC_LAYER_TOOLTIP_LABEL}
    />
  )
}

function SchemaPage() {
  const { target, setTarget, isResolving, isUnavailable, refetch } =
    useTargetResolution()
  const passwordLock = useTargetPasswordLock(target)
  const {
    status,
    schema,
    error,
    errorEnvelope,
    loading,
    checkStatus,
    loadSchema,
    initSchema,
    exportSchema,
    deleteSchema,
    addColumn,
    addTable,
    addTerminology,
    addEnum,
    addRelationship,
    addMetric,
    refreshSchema,
    profileSchema,
    clearError,
  } = useSchema()
  const connectionFailure =
    target && errorEnvelope && isConnectionFailure(errorEnvelope)
      ? errorEnvelope
      : null

  // useSchema retains the last successful response while the next request is
  // in flight. Never let that previous target's state drive effects, actions,
  // or rendering beneath the newly selected target name.
  const currentStatus = target && status?.target === target ? status : null
  const currentSchema = target && schema?.target === target ? schema : null

  const annotationRun = useBackgroundRun('schema_annotation', target)
  const bootstrapRun = useBackgroundRun('bootstrap', target)
  const annotationActive =
    annotationRun?.status === 'running' ||
    annotationRun?.status === 'reconnecting' ||
    annotationRun?.status === 'needs_key'
  const bootstrapActive =
    bootstrapRun?.status === 'running' ||
    bootstrapRun?.status === 'reconnecting' ||
    bootstrapRun?.status === 'needs_key'
  const annotateLoading = annotationActive
  const annotationCompletionRef = useRef<string | null>(null)

  const [initLoading, setInitLoading] = useState(false)
  const [refreshLoading, setRefreshLoading] = useState(false)
  const [profileLoading, setProfileLoading] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [reinitConfirmOpen, setReinitConfirmOpen] = useState(false)

  // Content-region tab (Tables default). Terminology + Metrics defer behind
  // this segmented control instead of stacking as always-on cards.
  const [activeTab, setActiveTab] = useState<SchemaContentTab>('tables')
  const schemaTabsId = useId()

  // Dialog state
  const [editingColumn, setEditingColumn] = useState<{
    tableName: string
    column: SchemaTableColumn
  } | null>(null)
  const [editingTable, setEditingTable] = useState<SchemaTable | null>(null)
  const [editingEnum, setEditingEnum] = useState<{
    tableName: string
    column: SchemaTableColumn
  } | null>(null)

  // Terminology dialog state
  const [termDialogOpen, setTermDialogOpen] = useState(false)
  const [editingTerm, setEditingTerm] = useState<SchemaTerminology | null>(null)

  // Relationship dialog state
  const [relationshipDialogOpen, setRelationshipDialogOpen] = useState(false)
  const [addingRelationshipSource, setAddingRelationshipSource] =
    useState<SchemaTable | null>(null)
  const [editingRelationship, setEditingRelationship] = useState<{
    sourceTable: string
    relationship: SchemaTableRelationship
  } | null>(null)

  // Metric dialog state
  const [metricDialogOpen, setMetricDialogOpen] = useState(false)
  const [editingMetric, setEditingMetric] = useState<SchemaMetric | null>(null)

  const [dialogLoading, setDialogLoading] = useState(false)

  // Load the selected target in order. A separate status-driven effect used
  // to see the previous target's `exists: true`, start the wrong schema load,
  // and abort this status check during target switches.
  useEffect(() => {
    if (target && passwordLock.isResolved && !passwordLock.isLocked) {
      void (async () => {
        const nextStatus = await checkStatus(target)
        if (nextStatus?.exists) {
          await loadSchema(target)
        }
      })()
    }
  }, [
    target,
    checkStatus,
    loadSchema,
    passwordLock.isResolved,
    passwordLock.isLocked,
  ])

  // The lock notice is the actionable presentation for a missing target
  // password. Do not leave a stale raw 423 response underneath it.
  useEffect(() => {
    if (passwordLock.isLocked) clearError()
  }, [clearError, passwordLock.isLocked])

  // Every real terminal annotation state may have persisted completed batches.
  // Refresh once so cancellation or a late failure does not leave this route
  // showing an older copy of the semantic layer.
  useEffect(() => {
    const refreshableTerminal =
      annotationRun?.status === 'done' ||
      annotationRun?.status === 'partial' ||
      annotationRun?.status === 'cancelled' ||
      annotationRun?.status === 'failed'
    if (
      !target ||
      passwordLock.isLocked ||
      !refreshableTerminal ||
      annotationCompletionRef.current === annotationRun.runId
    ) {
      return
    }
    annotationCompletionRef.current = annotationRun.runId
    void (async () => {
      // useSchema intentionally aborts an older request when a new one starts.
      // Running these concurrently makes loadSchema abort checkStatus; the
      // aborted status used to become null and blank the entire content area.
      const refreshedStatus = await checkStatus(target)
      if (refreshedStatus?.exists) {
        await loadSchema(target)
      }
    })()
  }, [annotationRun, checkStatus, loadSchema, passwordLock.isLocked, target])

  const runInit = async () => {
    if (passwordLock.isLocked) return
    if (!target) return
    setInitLoading(true)
    clearError()
    const result = await initSchema(target, { force: currentStatus?.exists })
    if (result?.success) {
      // Refresh status and schema
      await checkStatus(target)
      await loadSchema(target)
    }
    setInitLoading(false)
  }

  // Entry point for both the toolbar "Re-init" and the empty-state "Initialize".
  // Re-init on an existing layer is destructive — the backend force path
  // re-introspects and overwrites without merging annotations (B4) — so it must
  // pass through an explicit confirmation. First-time init has nothing to
  // destroy, so it runs immediately.
  const handleInit = () => {
    if (passwordLock.isLocked) return
    if (!target) return
    if (currentStatus?.exists) {
      setReinitConfirmOpen(true)
      return
    }
    void runInit()
  }

  const handleConfirmReinit = async () => {
    await runInit()
    setReinitConfirmOpen(false)
  }

  const handleDelete = async () => {
    if (passwordLock.isLocked) return
    if (!target) return
    const success = await deleteSchema(target)
    if (success) {
      setDeleteConfirm(false)
      await checkStatus(target)
    }
  }

  // Export moved into the Manage (⋯) menu's system submenu; the download plumbing
  // that lived in the bespoke SchemaExportButton lives here now.
  const handleExportDownload = async (format: 'yaml' | 'json') => {
    if (passwordLock.isLocked) return
    if (!target) return
    const content = await exportSchema(target, format)
    if (!content) return
    const blob = new Blob([content], {
      type: format === 'yaml' ? 'text/yaml' : 'application/json',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `semantic-layer.${format}`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const handleRefresh = async () => {
    if (passwordLock.isLocked) return
    if (!target) return
    setRefreshLoading(true)
    clearError()
    const result = await refreshSchema(target)
    setRefreshLoading(false)
    if (!result) return
    if (result.ok) {
      toast({
        title: 'Schema refreshed',
        description: result.message,
        variant: 'positive',
      })
      await checkStatus(target)
      await loadSchema(target)
    } else {
      toast({
        title: 'Refresh failed',
        description: result.message,
        variant: 'negative',
      })
    }
  }

  const handleProfile = async () => {
    if (passwordLock.isLocked) return
    if (!target) return
    setProfileLoading(true)
    clearError()
    const result = await profileSchema(target)
    setProfileLoading(false)
    if (!result) return
    if (result.ok) {
      toast({
        title: 'Profiling complete',
        description: result.message,
        variant: 'positive',
      })
      await checkStatus(target)
      await loadSchema(target)
    } else {
      toast({
        title: 'Profiling failed',
        description: result.message,
        variant: 'negative',
      })
    }
  }

  // Column/Table/Enum handlers
  const handleEditColumn = (tableName: string, column: SchemaTableColumn) => {
    if (passwordLock.isLocked) return
    setEditingColumn({ tableName, column })
  }

  const handleEditTable = (table: SchemaTable) => {
    if (passwordLock.isLocked) return
    setEditingTable(table)
  }

  const handleEditEnum = (tableName: string, column: SchemaTableColumn) => {
    if (passwordLock.isLocked) return
    setEditingEnum({ tableName, column })
  }

  // Terminology handlers
  const handleAddTerm = () => {
    if (passwordLock.isLocked) return
    setEditingTerm(null)
    setTermDialogOpen(true)
  }

  const handleEditTerm = (term: SchemaTerminology) => {
    if (passwordLock.isLocked) return
    setEditingTerm(term)
    setTermDialogOpen(true)
  }

  // Relationship handlers
  const handleAddRelationship = (sourceTable?: SchemaTable) => {
    if (passwordLock.isLocked) return
    setAddingRelationshipSource(sourceTable || null)
    setEditingRelationship(null)
    setRelationshipDialogOpen(true)
  }

  const handleEditRelationship = (
    tableName: string,
    relationship: SchemaTableRelationship
  ) => {
    if (passwordLock.isLocked) return
    setEditingRelationship({ sourceTable: tableName, relationship })
    setAddingRelationshipSource(null)
    setRelationshipDialogOpen(true)
  }

  // Metric handlers
  const handleAddMetric = () => {
    if (passwordLock.isLocked) return
    setEditingMetric(null)
    setMetricDialogOpen(true)
  }

  const handleEditMetric = (metric: SchemaMetric) => {
    if (passwordLock.isLocked) return
    setEditingMetric(metric)
    setMetricDialogOpen(true)
  }

  // Save handlers
  const handleSaveColumn = async (data: AddColumnData) => {
    if (passwordLock.isLocked) return false
    if (!target) return false
    setDialogLoading(true)
    const success = await addColumn(target, data)
    if (success) {
      await loadSchema(target)
    }
    setDialogLoading(false)
    return success
  }

  const handleSaveTable = async (data: AddTableData) => {
    if (passwordLock.isLocked) return false
    if (!target) return false
    setDialogLoading(true)
    const success = await addTable(target, data)
    if (success) {
      await loadSchema(target)
    }
    setDialogLoading(false)
    return success
  }

  const handleSaveEnum = async (data: AddEnumData) => {
    if (passwordLock.isLocked) return false
    if (!target) return false
    setDialogLoading(true)
    const success = await addEnum(target, data)
    if (success) {
      await loadSchema(target)
    }
    setDialogLoading(false)
    return success
  }

  const handleSaveTerm = async (data: AddTerminologyData) => {
    if (passwordLock.isLocked) return false
    if (!target) return false
    setDialogLoading(true)
    const success = await addTerminology(target, data)
    if (success) {
      await loadSchema(target)
      await checkStatus(target)
    }
    setDialogLoading(false)
    return success
  }

  const handleSaveRelationship = async (data: AddRelationshipData) => {
    if (passwordLock.isLocked) return false
    if (!target) return false
    setDialogLoading(true)
    const success = await addRelationship(target, data)
    if (success) {
      await loadSchema(target)
      await checkStatus(target)
    }
    setDialogLoading(false)
    return success
  }

  const handleSaveMetric = async (data: AddMetricData) => {
    if (passwordLock.isLocked) return false
    if (!target) return false
    setDialogLoading(true)
    const success = await addMetric(target, data)
    if (success) {
      await loadSchema(target)
    }
    setDialogLoading(false)
    return success
  }

  // A page header plus one centred slot: the three no-target outcomes
  // (resolving, unreachable, genuinely none) differ only in what fills it, and
  // must never be collapsed into the last one. [F-20, F-25]
  const noTargetFrame = (body: ReactNode) => (
    <div className="space-y-6 w-full">
      <m.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={getTransition()}
      >
        <HStack className="gap-4 items-center">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
            <Icon
              name="database"
              label="Schema"
              className="w-6 h-6 text-content-primary-soft"
            />
          </div>
          <VStack className="gap-1 items-start">
            <HStack className="gap-2 items-center">
              <Text
                as="h1"
                level="headline-3"
                className="text-content-layout-1"
              >
                Schema
              </Text>
              <SemanticLayerInfoTooltip />
            </HStack>
            <Text
              level="body-small"
              className="text-content-layout-3 max-w-2xl"
            >
              {SEMANTIC_LAYER_SUBTITLE}
            </Text>
          </VStack>
        </HStack>
      </m.div>
      {body}
    </div>
  )

  if (isResolving) {
    return noTargetFrame(
      <Card className="w-full">
        <Card.Content className="py-16">
          <VStack aria-busy="true" className="gap-4 items-center">
            <Skeleton className="h-16 w-16 rounded-2xl" />
            <Skeleton className="h-4 w-48 rounded-md" />
            <Skeleton className="h-3 w-72 rounded-md" />
          </VStack>
        </Card.Content>
      </Card>
    )
  }

  if (isUnavailable) {
    return noTargetFrame(
      <ErrorState
        errorClass="rdst-service"
        title="Could not load your database targets"
        message="RDST could not reach its own service to list the configured targets, so it cannot tell which one to show."
        trustworthy="Nothing was changed. Your semantic layers are untouched."
        onRetry={() => void refetch()}
      />
    )
  }

  // No target selected
  if (!target) {
    return noTargetFrame(
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ ...getTransition(), delay: 0.1 }}
      >
        <Card className="w-full">
          <Card.Content className="p-0">
            <EmptyState
              className="pb-4"
              icon="database"
              title="No target selected"
              body="Choose the database whose semantic layer you want to view or edit."
            />
            <div className="flex justify-center px-6 pb-16">
              <TargetDropdown
                selectedTarget={target}
                onSelectTarget={setTarget}
              />
            </div>
          </Card.Content>
        </Card>
      </m.div>
    )
  }

  return (
    <div className="space-y-6 w-full">
      {/* Header */}
      <m.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={getTransition()}
      >
        <HStack className="justify-between items-start">
          <HStack className="gap-4 items-center">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
              <Icon
                name="database"
                label="Schema"
                className="w-6 h-6 text-content-primary-soft"
              />
            </div>
            <VStack className="gap-1 items-start">
              <HStack className="gap-3 items-center">
                <Text
                  as="h1"
                  level="headline-3"
                  className="text-content-layout-1"
                >
                  Schema
                </Text>
                <SemanticLayerInfoTooltip />
                <Tag
                  label={target}
                  size="small"
                  variant="informative"
                  modifier="ghost"
                />
                {connectionFailure && (
                  <Tag
                    label="Connection failed"
                    size="small"
                    variant="negative"
                    modifier="ghost"
                    icon="alert"
                    iconPosition="left"
                  />
                )}
              </HStack>
              <Text
                level="body-small"
                className="text-content-layout-3 max-w-2xl"
              >
                {SEMANTIC_LAYER_SUBTITLE}
              </Text>
            </VStack>
          </HStack>

          {/* Schema maintenance actions. */}
          <Show when={currentStatus?.exists}>
            <HStack className="gap-2 items-center">
              <SchemaManageMenu
                onRefresh={handleRefresh}
                onProfile={handleProfile}
                onExport={handleExportDownload}
                onReinit={handleInit}
                onDelete={() => setDeleteConfirm(true)}
                disabled={
                  passwordLock.isLocked ||
                  refreshLoading ||
                  profileLoading ||
                  annotateLoading ||
                  initLoading ||
                  loading
                }
              />
            </HStack>
          </Show>
        </HStack>
      </m.div>

      <Show when={passwordLock.isLocked}>
        <TargetLockNotice
          message={passwordLock.message}
          requirements={passwordLock.missingTargetRequirements}
          keyringAvailable={passwordLock.keyringAvailable}
        />
      </Show>

      {/* Error alert */}
      <Show when={!!error}>
        <m.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-6"
        >
          {connectionFailure && target ? (
            <div className="rounded-xl border border-border-negative-soft bg-surface-negative-soft/20 p-4">
              <ConnectionFailureActions
                failure={{
                  target: connectionFailure.target || target,
                  message: connectionFailure.message,
                  category: connectionFailure.category,
                  code: connectionFailure.code,
                }}
                onRetry={async () => {
                  if (currentStatus?.exists) {
                    return (await refreshSchema(target))?.ok === true
                  }
                  return (await initSchema(target))?.success === true
                }}
                featureRecovery
              />
            </div>
          ) : (
            <ErrorState
              errorClass={classifyError(
                errorEnvelope ?? { code: '', message: error ?? '' }
              )}
              title="The semantic layer couldn't be updated"
              message={sanitizeWebError(
                error ?? undefined,
                'RDST could not complete that change to the semantic layer.'
              )}
              trustworthy="The layer stored for this target is unchanged."
              detail={error ?? undefined}
              action={{ label: 'Dismiss', onClick: clearError }}
            />
          )}
        </m.div>
      </Show>

      {/* Loading state */}
      <Show when={loading && !currentSchema && !initLoading && !currentStatus}>
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={getTransition()}
        >
          {/* The container that is about to hold the table list is skeletoned
              in its own shape, so the page does not jump when the layer lands.
              [C-71] */}
          <Card className="w-full">
            <Card.Content className="p-0">
              <VStack aria-busy="true" className="gap-0 items-stretch">
                <span className="sr-only">Loading semantic layer</span>
                <div className="px-5 py-3 border-b border-border-layout-1">
                  <HStack className="gap-3 items-center justify-between">
                    <Skeleton className="h-10 w-80 max-w-full rounded-lg" />
                    <Skeleton className="h-3 w-20 rounded-md" />
                  </HStack>
                </div>
                {[0, 1, 2, 3].map((row) => (
                  <div
                    key={row}
                    className="px-5 py-3 border-b border-border-layout-1 last:border-b-0"
                  >
                    <HStack className="gap-3 items-center">
                      <Skeleton className="h-4 w-4 rounded-sm shrink-0" />
                      <Skeleton className="h-3.5 w-48 rounded-md" />
                      <Skeleton className="h-3 w-24 rounded-md" />
                    </HStack>
                  </div>
                ))}
              </VStack>
            </Card.Content>
          </Card>
        </m.div>
      </Show>

      {/* Empty state - no schema exists */}
      <Show when={currentStatus && !currentStatus.exists}>
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={getTransition()}
        >
          <SchemaEmptyState
            target={target}
            onInit={handleInit}
            isLoading={initLoading || passwordLock.isLocked}
          />
        </m.div>
      </Show>

      {/* Schema exists - show content */}
      {currentStatus?.exists && currentSchema && (
        <div className="space-y-6">
          {/* Region B — the discovery pipeline as a guided sequence (dma.7):
                Structure -> Column profile -> AI descriptions, each explained,
                collapsing to a quiet line once well-documented. */}
          <m.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ ...getTransition(), delay: 0.1 }}
          >
            <SchemaGuidedSequence
              schema={currentSchema}
              status={currentStatus}
              onRefresh={handleRefresh}
              onProfile={handleProfile}
              refreshing={refreshLoading}
              profiling={profileLoading}
              disabled={
                passwordLock.isLocked ||
                annotateLoading ||
                bootstrapActive ||
                refreshLoading ||
                profileLoading ||
                initLoading ||
                loading
              }
            />
          </m.div>

          {/* Region C — one tabbed card: Tables (default) · Terminology · Metrics.
                Counts live on the tabs; only the active tab renders. */}
          <m.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ ...getTransition(), delay: 0.2 }}
          >
            <Card className="w-full overflow-hidden border-transparent shadow-small">
              <Card.Header>
                <HStack className="justify-between items-center w-full flex-wrap gap-3">
                  <div
                    role="tablist"
                    aria-label="Semantic layer sections"
                    className="inline-flex max-w-full flex-wrap items-center gap-1 rounded-xl bg-surface-layout-2 p-1"
                  >
                    {(
                      [
                        {
                          id: 'tables',
                          label: 'Tables',
                          count: currentSchema.tables.length,
                        },
                        {
                          id: 'terminology',
                          label: 'Terminology',
                          count: currentSchema.terminology.length,
                        },
                        {
                          id: 'metrics',
                          label: 'Metrics',
                          count: currentSchema.metrics.length,
                        },
                      ] as const
                    ).map((tab, index, tabs) => {
                      const isActive = activeTab === tab.id
                      return (
                        <Pressable
                          key={tab.id}
                          type="button"
                          role="tab"
                          id={`${schemaTabsId}-tab-${tab.id}`}
                          aria-selected={isActive}
                          aria-controls={`${schemaTabsId}-panel`}
                          // Roving tabindex: the tablist is one tab stop and
                          // the arrow keys move between the tabs inside it.
                          tabIndex={isActive ? 0 : -1}
                          onKeyDown={(event) => {
                            const step =
                              event.key === 'ArrowRight'
                                ? 1
                                : event.key === 'ArrowLeft'
                                  ? -1
                                  : 0
                            if (!step) return
                            event.preventDefault()
                            const next =
                              tabs[(index + step + tabs.length) % tabs.length]
                            setActiveTab(next.id)
                            document
                              .getElementById(`${schemaTabsId}-tab-${next.id}`)
                              ?.focus()
                          }}
                          onClick={() => setActiveTab(tab.id)}
                          className={`flex shrink-0 items-center gap-2 rounded-lg px-3 py-1.5 transition-colors focus-visible:outline-none focus-visible:shadow-focus ${
                            isActive
                              ? 'bg-surface-raised text-content-layout-1 shadow-small'
                              : 'text-content-layout-3 hover:text-content-layout-1'
                          }`}
                        >
                          <Text level="label-small">{tab.label}</Text>
                          <Tag
                            size="small"
                            variant={isActive ? 'informative' : 'muted'}
                            modifier="ghost"
                            label={String(tab.count)}
                          />
                        </Pressable>
                      )
                    })}
                  </div>

                  <Show when={activeTab === 'tables'}>
                    <Button
                      modifier="ghost"
                      size="small"
                      icon="connect"
                      iconPosition="left"
                      label="Add relationship"
                      onClick={() => handleAddRelationship()}
                      disabled={passwordLock.isLocked}
                    />
                  </Show>
                  <Show when={activeTab === 'terminology'}>
                    <Button
                      modifier="ghost"
                      size="small"
                      icon="add"
                      iconPosition="left"
                      label="Add term"
                      onClick={handleAddTerm}
                      disabled={passwordLock.isLocked}
                    />
                  </Show>
                  <Show when={activeTab === 'metrics'}>
                    <Button
                      modifier="ghost"
                      size="small"
                      icon="add"
                      iconPosition="left"
                      label="Add metric"
                      onClick={handleAddMetric}
                      disabled={passwordLock.isLocked}
                    />
                  </Show>
                </HStack>
              </Card.Header>
              <Card.Content
                className="p-0"
                role="tabpanel"
                id={`${schemaTabsId}-panel`}
                aria-labelledby={`${schemaTabsId}-tab-${activeTab}`}
              >
                <Show when={activeTab === 'tables'}>
                  <SchemaTableTree
                    tables={currentSchema.tables}
                    onEditColumn={handleEditColumn}
                    onEditTable={handleEditTable}
                    onEditEnum={handleEditEnum}
                    onEditRelationship={handleEditRelationship}
                    onRefreshStructure={() => void handleRefresh()}
                  />
                </Show>
                <Show when={activeTab === 'terminology'}>
                  <SchemaTerminologyList
                    terminology={currentSchema.terminology}
                    onEdit={handleEditTerm}
                  />
                </Show>
                <Show when={activeTab === 'metrics'}>
                  <SchemaMetricsList
                    metrics={currentSchema.metrics}
                    onEdit={handleEditMetric}
                  />
                </Show>
              </Card.Content>
            </Card>
          </m.div>
        </div>
      )}

      {/* Destructive Re-init confirmation (B4/T4) */}
      <SchemaReinitDialog
        isOpen={reinitConfirmOpen}
        target={target ?? ''}
        isLoading={initLoading}
        onConfirm={handleConfirmReinit}
        onClose={() => setReinitConfirmOpen(false)}
      />

      {/* Destructive Delete confirmation — the former Danger-Zone Delete now
            lives in the Manage menu and confirms through the shared primitive
            (the red lives in the confirm). */}
      <ConfirmDialog
        isOpen={deleteConfirm}
        onClose={() => setDeleteConfirm(false)}
        onConfirm={handleDelete}
        title="Delete semantic layer?"
        subtitle={`Target: ${target ?? ''}`}
        notice={{
          accent: 'negative',
          icon: 'alert',
          title: 'This removes every annotation',
          message:
            'Deleting the semantic layer removes all table and column descriptions, enum meanings, business terminology, metrics, and relationships for this target. This cannot be undone.',
        }}
        confirmLabel="Delete semantic layer"
        confirmVariant="negative"
        loading={loading}
        blockCloseWhileLoading
      />

      {/* Edit dialogs */}
      <SchemaEditColumnDialog
        isOpen={!!editingColumn}
        tableName={editingColumn?.tableName || ''}
        column={editingColumn?.column || null}
        onClose={() => setEditingColumn(null)}
        onSave={handleSaveColumn}
        isLoading={dialogLoading}
      />

      <SchemaEditTableDialog
        isOpen={!!editingTable}
        table={editingTable}
        onClose={() => setEditingTable(null)}
        onSave={handleSaveTable}
        isLoading={dialogLoading}
      />

      <SchemaEditEnumDialog
        isOpen={!!editingEnum}
        tableName={editingEnum?.tableName || ''}
        column={editingEnum?.column || null}
        onClose={() => setEditingEnum(null)}
        onSave={handleSaveEnum}
        isLoading={dialogLoading}
      />

      <SchemaAddTermDialog
        isOpen={termDialogOpen}
        editingTerm={editingTerm}
        onClose={() => {
          setTermDialogOpen(false)
          setEditingTerm(null)
        }}
        onSave={handleSaveTerm}
        isLoading={dialogLoading}
      />

      <SchemaAddRelationshipDialog
        isOpen={relationshipDialogOpen}
        tables={currentSchema?.tables || []}
        sourceTable={addingRelationshipSource}
        editingRelationship={editingRelationship}
        onClose={() => {
          setRelationshipDialogOpen(false)
          setAddingRelationshipSource(null)
          setEditingRelationship(null)
        }}
        onSave={handleSaveRelationship}
        isLoading={dialogLoading}
      />

      <SchemaAddMetricDialog
        isOpen={metricDialogOpen}
        editingMetric={editingMetric}
        onClose={() => {
          setMetricDialogOpen(false)
          setEditingMetric(null)
        }}
        onSave={handleSaveMetric}
        isLoading={dialogLoading}
      />
    </div>
  )
}

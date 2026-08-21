import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import { toast } from '@rs/ui-new/use-toast'
import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { ConnectionFailureActions } from '../components/ConnectionFailureActions'
import { RoutableNotice } from '../components/RoutableNotice'
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
import { useTarget } from '../hooks/useTarget'
import {
  startSchemaAnnotationRun,
  useBackgroundRun,
} from '../lib/backgroundRuns'
import { isConnectionFailure } from '../lib/errorContract'
import { useTrialSource } from '../lib/trialQueries'
import { useAnthropicValidity } from '../lib/useAnthropicValidity'
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

function SemanticLayerInfoTooltip() {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Pressable
            type="button"
            className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-border-primary-soft/50 bg-surface-primary-soft/20 text-content-primary-soft hover:bg-surface-primary-soft/30 hover:border-border-primary-soft transition-colors"
            aria-label="What is the semantic layer?"
          >
            <Text level="caption" className="leading-none font-semibold">
              i
            </Text>
          </Pressable>
        </TooltipTrigger>
        <TooltipContent label={SEMANTIC_LAYER_TOOLTIP_LABEL} />
      </Tooltip>
    </TooltipProvider>
  )
}

function SchemaPage() {
  const { target, setTarget } = useTarget()
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

  // AI Annotate needs a *working* Anthropic key. Presence isn't enough — a
  // saved key can be stale/rejected — so probe validity when a key is present.
  // Both a missing key and a rejected one route to /configure up front rather
  // than run-then-error on the activation path (dma.7 req #3, rdst-0yy.11).
  const { envRequirements, anthropicRequirement } = useTrialSource()
  const keyRequirementPending = envRequirements === undefined
  const needsApiKey = anthropicRequirement
    ? !anthropicRequirement.satisfied
    : false
  // Probe only once requirements have resolved: probing while they load asks
  // the provider about a key that may not exist, and that "no key" verdict is
  // cached long enough to keep every AI gate blocked after a key is set.
  const keyValidity = useAnthropicValidity(
    !keyRequirementPending && !needsApiKey
  ).data
  const keyRejected =
    keyValidity?.valid === false && keyValidity.reason === 'rejected'
  // Fail closed while requirements load so a quick click cannot start an AI
  // request before we know whether a key is available.
  const annotateKeyBlocked = keyRequirementPending || needsApiKey || keyRejected

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
  const annotateProgress = annotationActive
    ? annotationRun.current !== null &&
      annotationRun.total !== null &&
      annotationRun.total > 0
      ? `${annotationRun.message} (${annotationRun.current}/${annotationRun.total})`
      : annotationRun.message
    : null
  const annotationCompletionRef = useRef<string | null>(null)

  const [initLoading, setInitLoading] = useState(false)
  const [refreshLoading, setRefreshLoading] = useState(false)
  const [profileLoading, setProfileLoading] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [reinitConfirmOpen, setReinitConfirmOpen] = useState(false)

  // Content-region tab (Tables default). Terminology + Metrics defer behind
  // this segmented control instead of stacking as always-on cards.
  const [activeTab, setActiveTab] = useState<SchemaContentTab>('tables')

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

  const handleAnnotateWithLLM = () => {
    if (passwordLock.isLocked) return
    if (annotateKeyBlocked) return
    if (annotationActive || bootstrapActive) return
    if (!target) return
    clearError()
    void startSchemaAnnotationRun(target)
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

  // No target selected
  if (!target) {
    return (
      <div className="space-y-6 w-full">
        {/* Header */}
        <m.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
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

        {/* Empty state card */}
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.1 }}
        >
          <Card className="w-full">
            <Card.Content className="py-16">
              <VStack className="gap-6 items-center">
                <div className="w-16 h-16 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
                  <Icon
                    name="database"
                    label="Select target"
                    className="w-8 h-8 text-content-layout-3"
                  />
                </div>
                <VStack className="gap-2 items-center">
                  <Text level="headline-5" className="text-content-layout-2">
                    Select a Target
                  </Text>
                  <Text
                    level="body-small"
                    className="text-content-layout-3 text-center max-w-md"
                  >
                    Choose a database target to view or manage its semantic
                    layer
                  </Text>
                </VStack>
                <TargetDropdown
                  selectedTarget={target}
                  onSelectTarget={setTarget}
                />
              </VStack>
            </Card.Content>
          </Card>
        </m.div>
      </div>
    )
  }

  return (
    <div className="space-y-6 w-full">
      {/* Header */}
      <m.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
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

          {/* One primary action + one overflow trigger (redesign §A / VIS-022). */}
          <Show when={currentStatus?.exists}>
            <HStack className="gap-2 items-center">
              <Button
                variant="rising"
                modifier="solid"
                icon="sparkles"
                iconPosition="left"
                label={annotateProgress || 'Annotate with AI'}
                onClick={handleAnnotateWithLLM}
                loading={annotateLoading}
                disabled={
                  passwordLock.isLocked ||
                  annotateKeyBlocked ||
                  annotateLoading ||
                  bootstrapActive ||
                  refreshLoading ||
                  profileLoading ||
                  initLoading ||
                  loading
                }
              />
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

      {/* AI Annotate prerequisite: coax to /configure up front rather than
            letting a missing- or rejected-key run fail after the click, on the
            compelled activation path (rdst-dma.7, rdst-0yy.11). */}
      <Show
        when={
          currentStatus?.exists &&
          !keyRequirementPending &&
          (needsApiKey || keyRejected)
        }
      >
        <div className="mb-6">
          {keyRejected ? (
            <RoutableNotice
              kind="key-needed"
              title="Anthropic key rejected"
              message="Anthropic rejected the configured key. Update it with a valid one to run AI Annotate."
            />
          ) : (
            <RoutableNotice kind="key-needed" />
          )}
        </div>
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
            <Alert
              variant="negative"
              modifier="outline"
              label={`Error: ${error}`}
              onClick={clearError}
            />
          )}
        </m.div>
      </Show>

      {/* Loading state */}
      <Show when={loading && !currentSchema && !initLoading && !currentStatus}>
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          <Card className="w-full">
            <Card.Content className="py-16">
              <VStack className="gap-4 items-center">
                <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center animate-pulse">
                  <Icon
                    name="database"
                    label="Loading"
                    className="w-7 h-7 text-content-layout-3"
                  />
                </div>
                <Text level="body-medium" className="text-content-layout-3">
                  Loading schema...
                </Text>
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
          transition={{ duration: 0.4 }}
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
            transition={{ duration: 0.4, delay: 0.1 }}
          >
            <SchemaGuidedSequence
              schema={currentSchema}
              status={currentStatus}
              onRefresh={handleRefresh}
              onProfile={handleProfile}
              onAnnotate={handleAnnotateWithLLM}
              refreshing={refreshLoading}
              profiling={profileLoading}
              annotating={annotateLoading}
              annotateLabel={annotateProgress}
              annotateBlocked={annotateKeyBlocked || bootstrapActive}
              annotateBlockedLabel={
                bootstrapActive
                  ? 'Setup in progress'
                  : keyRequirementPending
                    ? 'Checking AI key'
                    : keyRejected
                      ? 'Key rejected'
                      : 'Needs a key'
              }
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
            transition={{ duration: 0.4, delay: 0.2 }}
          >
            <Card className="w-full overflow-hidden border-transparent shadow-small">
              <Card.Header>
                <HStack className="justify-between items-center w-full flex-wrap gap-3">
                  <div
                    role="tablist"
                    aria-label="Semantic layer sections"
                    className="inline-flex items-center gap-1 rounded-xl bg-surface-layout-2 p-1"
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
                    ).map((tab) => {
                      const isActive = activeTab === tab.id
                      return (
                        <Pressable
                          key={tab.id}
                          type="button"
                          role="tab"
                          aria-selected={isActive}
                          onClick={() => setActiveTab(tab.id)}
                          className={`flex items-center gap-2 rounded-lg px-3 py-1.5 transition-colors focus-visible:outline-none focus-visible:shadow-focus ${
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
              <Card.Content className="p-0">
                <Show when={activeTab === 'tables'}>
                  <SchemaTableTree
                    tables={currentSchema.tables}
                    onEditColumn={handleEditColumn}
                    onEditTable={handleEditTable}
                    onEditEnum={handleEditEnum}
                    onEditRelationship={handleEditRelationship}
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

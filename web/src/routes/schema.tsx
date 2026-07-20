import { useEffect, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Text } from "@rs/ui-new/text";
import { Alert } from "@rs/ui-new/alert";
import { Button } from "@rs/ui-new/button";
import { Tag } from "@rs/ui-new/tag";
import { Icon } from "@rs/ui-new/icon";
import { Card } from "@rs/ui-new/card";
import { HStack, VStack } from "@rs/ui-new/stack";
import { Show } from "@rs/ui-new/show";
import { m } from "@rs/ui-new/motion";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@rs/ui-new/tooltip";
import { ConfirmDialog } from "@rs/ui-new/confirm-dialog";
import { toast } from "@rs/ui-new/use-toast";
import { useTarget } from "../hooks/useTarget";
import { useSchema } from "../lib/useSchema";
import { useTargetPasswordLock } from "../lib/useTargetPasswordLock";
import {
  SchemaReadinessBand,
  SchemaManageMenu,
  SchemaTableTree,
  SchemaTerminologyList,
  SchemaMetricsList,
  SchemaEmptyState,
  SchemaReinitDialog,
  SchemaEditColumnDialog,
  SchemaEditTableDialog,
  SchemaEditEnumDialog,
  SchemaAddTermDialog,
  SchemaAddRelationshipDialog,
  SchemaAddMetricDialog,
} from "../components/schema";
import { TargetLockNotice } from "../components/TargetLockNotice";
import { TargetDropdown } from "../components/TargetDropdown";
import type {
  SchemaTable,
  SchemaTableColumn,
  SchemaTableRelationship,
  SchemaTerminology,
  SchemaMetric,
  AddColumnData,
  AddTableData,
  AddTerminologyData,
  AddEnumData,
  AddRelationshipData,
  AddMetricData,
} from "../types/schema";

export const Route = createFileRoute("/schema")({
  component: SchemaPage,
});

const SEMANTIC_LAYER_TOOLTIP_LABEL =
  "The semantic layer lets you document business logic that isn't obvious from the schema alone, like enum meanings, domain terms, and relationships, so RDST can provide better AI recommendations.";

// Promotes the tooltip's explanation into a persistent, self-evident value prop
// (redesign §Copy #1) — the one sentence that says why this screen matters.
const SEMANTIC_LAYER_SUBTITLE =
  "Teach RDST's AI what your data means, so Ask and Analyze write better SQL for this database.";

type SchemaContentTab = "tables" | "terminology" | "metrics";

function SemanticLayerInfoTooltip() {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-border-primary-soft/50 bg-surface-primary-soft/20 text-content-primary-soft hover:bg-surface-primary-soft/30 hover:border-border-primary-soft transition-colors"
            aria-label="What is the semantic layer?"
          >
            <Text level="caption" className="leading-none font-semibold">
              i
            </Text>
          </button>
        </TooltipTrigger>
        <TooltipContent label={SEMANTIC_LAYER_TOOLTIP_LABEL} />
      </Tooltip>
    </TooltipProvider>
  );
}

function SchemaPage() {
  const { target, setTarget } = useTarget();
  const passwordLock = useTargetPasswordLock(target);
  const {
    status,
    schema,
    error,
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
    annotateWithLLM,
    clearError,
  } = useSchema();

  const [initLoading, setInitLoading] = useState(false);
  const [refreshLoading, setRefreshLoading] = useState(false);
  const [profileLoading, setProfileLoading] = useState(false);
  const [annotateLoading, setAnnotateLoading] = useState(false);
  const [annotateProgress, setAnnotateProgress] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [reinitConfirmOpen, setReinitConfirmOpen] = useState(false);

  // Content-region tab (Tables default). Terminology + Metrics defer behind
  // this segmented control instead of stacking as always-on cards.
  const [activeTab, setActiveTab] = useState<SchemaContentTab>("tables");

  // Dialog state
  const [editingColumn, setEditingColumn] = useState<{
    tableName: string;
    column: SchemaTableColumn;
  } | null>(null);
  const [editingTable, setEditingTable] = useState<SchemaTable | null>(null);
  const [editingEnum, setEditingEnum] = useState<{
    tableName: string;
    column: SchemaTableColumn;
  } | null>(null);

  // Terminology dialog state
  const [termDialogOpen, setTermDialogOpen] = useState(false);
  const [editingTerm, setEditingTerm] = useState<SchemaTerminology | null>(null);

  // Relationship dialog state
  const [relationshipDialogOpen, setRelationshipDialogOpen] = useState(false);
  const [addingRelationshipSource, setAddingRelationshipSource] = useState<SchemaTable | null>(
    null,
  );
  const [editingRelationship, setEditingRelationship] = useState<{
    sourceTable: string;
    relationship: SchemaTableRelationship;
  } | null>(null);

  // Metric dialog state
  const [metricDialogOpen, setMetricDialogOpen] = useState(false);
  const [editingMetric, setEditingMetric] = useState<SchemaMetric | null>(null);

  const [dialogLoading, setDialogLoading] = useState(false);

  // Load status when target changes
  useEffect(() => {
    if (target && !passwordLock.isLocked) {
      checkStatus(target);
    }
  }, [target, checkStatus, passwordLock.isLocked]);

  // Load full schema when status indicates it exists
  useEffect(() => {
    if (target && status?.exists && !passwordLock.isLocked) {
      loadSchema(target);
    }
  }, [target, status?.exists, loadSchema, passwordLock.isLocked]);

  const runInit = async () => {
    if (passwordLock.isLocked) return;
    if (!target) return;
    setInitLoading(true);
    clearError();
    const result = await initSchema(target, { force: status?.exists });
    if (result?.success) {
      // Refresh status and schema
      await checkStatus(target);
      await loadSchema(target);
    }
    setInitLoading(false);
  };

  // Entry point for both the toolbar "Re-init" and the empty-state "Initialize".
  // Re-init on an existing layer is destructive — the backend force path
  // re-introspects and overwrites without merging annotations (B4) — so it must
  // pass through an explicit confirmation. First-time init has nothing to
  // destroy, so it runs immediately.
  const handleInit = () => {
    if (passwordLock.isLocked) return;
    if (!target) return;
    if (status?.exists) {
      setReinitConfirmOpen(true);
      return;
    }
    void runInit();
  };

  const handleConfirmReinit = async () => {
    await runInit();
    setReinitConfirmOpen(false);
  };

  const handleDelete = async () => {
    if (passwordLock.isLocked) return;
    if (!target) return;
    const success = await deleteSchema(target);
    if (success) {
      setDeleteConfirm(false);
      await checkStatus(target);
    }
  };

  // Export moved into the Manage (⋯) menu's system submenu; the download plumbing
  // that lived in the bespoke SchemaExportButton lives here now.
  const handleExportDownload = async (format: "yaml" | "json") => {
    if (passwordLock.isLocked) return;
    if (!target) return;
    const content = await exportSchema(target, format);
    if (!content) return;
    const blob = new Blob([content], {
      type: format === "yaml" ? "text/yaml" : "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `semantic-layer.${format}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleAnnotateWithLLM = async () => {
    if (passwordLock.isLocked) return;
    if (!target) return;
    setAnnotateLoading(true);
    setAnnotateProgress('Starting...');
    clearError();
    const success = await annotateWithLLM(target, undefined, (message, tableIndex, totalTables) => {
      if (tableIndex && totalTables) {
        setAnnotateProgress(`${message} (${tableIndex}/${totalTables})`);
      } else {
        setAnnotateProgress(message);
      }
    });
    if (success) {
      await loadSchema(target);
    }
    setAnnotateLoading(false);
    setAnnotateProgress(null);
  };

  const handleRefresh = async () => {
    if (passwordLock.isLocked) return;
    if (!target) return;
    setRefreshLoading(true);
    clearError();
    const result = await refreshSchema(target);
    setRefreshLoading(false);
    if (!result) return;
    if (result.ok) {
      toast({ title: "Schema refreshed", description: result.message, variant: "positive" });
      await checkStatus(target);
      await loadSchema(target);
    } else {
      toast({ title: "Refresh failed", description: result.message, variant: "negative" });
    }
  };

  const handleProfile = async () => {
    if (passwordLock.isLocked) return;
    if (!target) return;
    setProfileLoading(true);
    clearError();
    const result = await profileSchema(target);
    setProfileLoading(false);
    if (!result) return;
    if (result.ok) {
      toast({ title: "Profiling complete", description: result.message, variant: "positive" });
      await checkStatus(target);
      await loadSchema(target);
    } else {
      toast({ title: "Profiling failed", description: result.message, variant: "negative" });
    }
  };

  // Column/Table/Enum handlers
  const handleEditColumn = (tableName: string, column: SchemaTableColumn) => {
    if (passwordLock.isLocked) return;
    setEditingColumn({ tableName, column });
  };

  const handleEditTable = (table: SchemaTable) => {
    if (passwordLock.isLocked) return;
    setEditingTable(table);
  };

  const handleEditEnum = (tableName: string, column: SchemaTableColumn) => {
    if (passwordLock.isLocked) return;
    setEditingEnum({ tableName, column });
  };

  // Terminology handlers
  const handleAddTerm = () => {
    if (passwordLock.isLocked) return;
    setEditingTerm(null);
    setTermDialogOpen(true);
  };

  const handleEditTerm = (term: SchemaTerminology) => {
    if (passwordLock.isLocked) return;
    setEditingTerm(term);
    setTermDialogOpen(true);
  };

  // Relationship handlers
  const handleAddRelationship = (sourceTable?: SchemaTable) => {
    if (passwordLock.isLocked) return;
    setAddingRelationshipSource(sourceTable || null);
    setEditingRelationship(null);
    setRelationshipDialogOpen(true);
  };

  const handleEditRelationship = (tableName: string, relationship: SchemaTableRelationship) => {
    if (passwordLock.isLocked) return;
    setEditingRelationship({ sourceTable: tableName, relationship });
    setAddingRelationshipSource(null);
    setRelationshipDialogOpen(true);
  };

  // Metric handlers
  const handleAddMetric = () => {
    if (passwordLock.isLocked) return;
    setEditingMetric(null);
    setMetricDialogOpen(true);
  };

  const handleEditMetric = (metric: SchemaMetric) => {
    if (passwordLock.isLocked) return;
    setEditingMetric(metric);
    setMetricDialogOpen(true);
  };

  // Save handlers
  const handleSaveColumn = async (data: AddColumnData) => {
    if (passwordLock.isLocked) return false;
    if (!target) return false;
    setDialogLoading(true);
    const success = await addColumn(target, data);
    if (success) {
      await loadSchema(target);
    }
    setDialogLoading(false);
    return success;
  };

  const handleSaveTable = async (data: AddTableData) => {
    if (passwordLock.isLocked) return false;
    if (!target) return false;
    setDialogLoading(true);
    const success = await addTable(target, data);
    if (success) {
      await loadSchema(target);
    }
    setDialogLoading(false);
    return success;
  };

  const handleSaveEnum = async (data: AddEnumData) => {
    if (passwordLock.isLocked) return false;
    if (!target) return false;
    setDialogLoading(true);
    const success = await addEnum(target, data);
    if (success) {
      await loadSchema(target);
    }
    setDialogLoading(false);
    return success;
  };

  const handleSaveTerm = async (data: AddTerminologyData) => {
    if (passwordLock.isLocked) return false;
    if (!target) return false;
    setDialogLoading(true);
    const success = await addTerminology(target, data);
    if (success) {
      await loadSchema(target);
      await checkStatus(target);
    }
    setDialogLoading(false);
    return success;
  };

  const handleSaveRelationship = async (data: AddRelationshipData) => {
    if (passwordLock.isLocked) return false;
    if (!target) return false;
    setDialogLoading(true);
    const success = await addRelationship(target, data);
    if (success) {
      await loadSchema(target);
      await checkStatus(target);
    }
    setDialogLoading(false);
    return success;
  };

  const handleSaveMetric = async (data: AddMetricData) => {
    if (passwordLock.isLocked) return false;
    if (!target) return false;
    setDialogLoading(true);
    const success = await addMetric(target, data);
    if (success) {
      await loadSchema(target);
    }
    setDialogLoading(false);
    return success;
  };

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
              <Icon name="database" label="Schema" className="w-6 h-6 text-content-primary-soft" />
            </div>
            <VStack className="gap-1 items-start">
              <HStack className="gap-2 items-center">
                <Text as="h1" level="headline-3" className="text-content-layout-1">
                  Semantic Layer
                </Text>
                <SemanticLayerInfoTooltip />
              </HStack>
              <Text level="body-small" className="text-content-layout-3 max-w-2xl">
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
                  <Icon name="database" label="Select target" className="w-8 h-8 text-content-layout-3" />
                </div>
                <VStack className="gap-2 items-center">
                  <Text level="headline-5" className="text-content-layout-2">
                    Select a Target
                  </Text>
                  <Text level="body-small" className="text-content-layout-3 text-center max-w-md">
                    Choose a database target to view or manage its semantic layer
                  </Text>
                </VStack>
                <TargetDropdown selectedTarget={target} onSelectTarget={setTarget} />
              </VStack>
            </Card.Content>
          </Card>
        </m.div>
      </div>
    );
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
                <Icon name="database" label="Schema" className="w-6 h-6 text-content-primary-soft" />
              </div>
              <VStack className="gap-1 items-start">
                <HStack className="gap-3 items-center">
                  <Text as="h1" level="headline-3" className="text-content-layout-1">
                    Semantic Layer
                  </Text>
                  <SemanticLayerInfoTooltip />
                  <Tag
                    label={target}
                    size="small"
                    variant="informative"
                    modifier="ghost"
                  />
                </HStack>
                <Text level="body-small" className="text-content-layout-3 max-w-2xl">
                  {SEMANTIC_LAYER_SUBTITLE}
                </Text>
              </VStack>
            </HStack>

            {/* One primary action + one overflow trigger (redesign §A / VIS-022). */}
            <Show when={status?.exists}>
              <HStack className="gap-2 items-center">
                <Button
                  variant="rising"
                  modifier="solid"
                  icon="sparkles"
                  iconPosition="left"
                  label={annotateProgress || "Annotate with AI"}
                  onClick={handleAnnotateWithLLM}
                  loading={annotateLoading}
                  disabled={passwordLock.isLocked || annotateLoading || refreshLoading || profileLoading || initLoading || loading}
                />
                <SchemaManageMenu
                  onRefresh={handleRefresh}
                  onProfile={handleProfile}
                  onExport={handleExportDownload}
                  onReinit={handleInit}
                  onDelete={() => setDeleteConfirm(true)}
                  disabled={passwordLock.isLocked || refreshLoading || profileLoading || annotateLoading || initLoading || loading}
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
            <Alert
              variant="negative"
              modifier="outline"
              label={`Error: ${error}`}
              onClick={clearError}
            />
          </m.div>
        </Show>

        {/* Loading state */}
        <Show when={loading && !schema && !initLoading && !status}>
          <m.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4 }}
          >
            <Card className="w-full">
              <Card.Content className="py-16">
                <VStack className="gap-4 items-center">
                  <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center animate-pulse">
                    <Icon name="database" label="Loading" className="w-7 h-7 text-content-layout-3" />
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
        <Show when={status && !status.exists}>
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
        {status?.exists && schema && (
          <div className="space-y-6">
            {/* Region B — readiness band (prominent while under-documented, then a
                quiet line). Absorbs the old separate Summary stat card. */}
            <m.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, delay: 0.1 }}
            >
              <SchemaReadinessBand
                schema={schema!}
                status={status!}
                onAnnotate={handleAnnotateWithLLM}
                annotating={annotateLoading}
                annotateLabel={annotateProgress}
                disabled={passwordLock.isLocked || annotateLoading || refreshLoading || profileLoading || initLoading || loading}
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
                      {([
                        { id: "tables", label: "Tables", count: schema!.tables.length },
                        { id: "terminology", label: "Terminology", count: schema!.terminology.length },
                        { id: "metrics", label: "Metrics", count: schema!.metrics.length },
                      ] as const).map((tab) => {
                        const isActive = activeTab === tab.id;
                        return (
                          <button
                            key={tab.id}
                            type="button"
                            role="tab"
                            aria-selected={isActive}
                            onClick={() => setActiveTab(tab.id)}
                            className={`flex items-center gap-2 rounded-lg px-3 py-1.5 transition-colors focus-visible:outline-none focus-visible:shadow-focus ${
                              isActive
                                ? "bg-surface-raised text-content-layout-1 shadow-small"
                                : "text-content-layout-3 hover:text-content-layout-1"
                            }`}
                          >
                            <Text level="label-small">{tab.label}</Text>
                            <Tag
                              size="small"
                              variant={isActive ? "informative" : "muted"}
                              modifier="ghost"
                              label={String(tab.count)}
                            />
                          </button>
                        );
                      })}
                    </div>

                    <Show when={activeTab === "tables"}>
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
                    <Show when={activeTab === "terminology"}>
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
                    <Show when={activeTab === "metrics"}>
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
                  <Show when={activeTab === "tables"}>
                    <SchemaTableTree
                      tables={schema!.tables}
                      onEditColumn={handleEditColumn}
                      onEditTable={handleEditTable}
                      onEditEnum={handleEditEnum}
                      onEditRelationship={handleEditRelationship}
                    />
                  </Show>
                  <Show when={activeTab === "terminology"}>
                    <SchemaTerminologyList terminology={schema!.terminology} onEdit={handleEditTerm} />
                  </Show>
                  <Show when={activeTab === "metrics"}>
                    <SchemaMetricsList metrics={schema!.metrics} onEdit={handleEditMetric} />
                  </Show>
                </Card.Content>
              </Card>
            </m.div>
          </div>
        )}

        {/* Destructive Re-init confirmation (B4/T4) */}
        <SchemaReinitDialog
          isOpen={reinitConfirmOpen}
          target={target ?? ""}
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
          subtitle={`Target: ${target ?? ""}`}
          notice={{
            accent: "negative",
            icon: "alert",
            title: "This removes every annotation",
            message:
              "Deleting the semantic layer removes all table and column descriptions, enum meanings, business terminology, metrics, and relationships for this target. This cannot be undone.",
          }}
          confirmLabel="Delete semantic layer"
          confirmVariant="negative"
          loading={loading}
          blockCloseWhileLoading
        />

        {/* Edit dialogs */}
        <SchemaEditColumnDialog
          isOpen={!!editingColumn}
          tableName={editingColumn?.tableName || ""}
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
          tableName={editingEnum?.tableName || ""}
          column={editingEnum?.column || null}
          onClose={() => setEditingEnum(null)}
          onSave={handleSaveEnum}
          isLoading={dialogLoading}
        />

        <SchemaAddTermDialog
          isOpen={termDialogOpen}
          editingTerm={editingTerm}
          onClose={() => {
            setTermDialogOpen(false);
            setEditingTerm(null);
          }}
          onSave={handleSaveTerm}
          isLoading={dialogLoading}
        />

        <SchemaAddRelationshipDialog
          isOpen={relationshipDialogOpen}
          tables={schema?.tables || []}
          sourceTable={addingRelationshipSource}
          editingRelationship={editingRelationship}
          onClose={() => {
            setRelationshipDialogOpen(false);
            setAddingRelationshipSource(null);
            setEditingRelationship(null);
          }}
          onSave={handleSaveRelationship}
          isLoading={dialogLoading}
        />

        <SchemaAddMetricDialog
          isOpen={metricDialogOpen}
          editingMetric={editingMetric}
          onClose={() => {
            setMetricDialogOpen(false);
            setEditingMetric(null);
          }}
          onSave={handleSaveMetric}
          isLoading={dialogLoading}
        />
    </div>
  );
}

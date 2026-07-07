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
import { toast } from "@rs/ui-new/use-toast";
import { useTarget } from "../hooks/useTarget";
import { useSchema } from "../lib/useSchema";
import { useTargetPasswordLock } from "../lib/useTargetPasswordLock";
import {
  SchemaSummaryCard,
  SchemaTableTree,
  SchemaTerminologyList,
  SchemaMetricsList,
  SchemaEmptyState,
  SchemaInitButton,
  SchemaExportButton,
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

  const handleInit = async () => {
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

  const handleDelete = async () => {
    if (passwordLock.isLocked) return;
    if (!target) return;
    const success = await deleteSchema(target);
    if (success) {
      setDeleteConfirm(false);
      await checkStatus(target);
    }
  };

  const handleExport = async (format: "yaml" | "json") => {
    if (passwordLock.isLocked) return null;
    if (!target) return null;
    return exportSchema(target, format);
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
              <Text level="body-small" className="text-content-layout-3">
                Schema annotations for AI-powered queries
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
                <Text level="body-small" className="text-content-layout-3">
                  Schema annotations for AI-powered queries
                </Text>
              </VStack>
            </HStack>

            <Show when={status?.exists}>
              <HStack className="gap-2 items-center">
                <Button
                  variant="primary"
                  modifier="outline"
                  icon="database-settings"
                  iconPosition="left"
                  label="Refresh"
                  onClick={handleRefresh}
                  loading={refreshLoading}
                  disabled={passwordLock.isLocked || refreshLoading || profileLoading || loading}
                />
                <Button
                  variant="primary"
                  modifier="outline"
                  icon="speedometer"
                  iconPosition="left"
                  label="Profile"
                  onClick={handleProfile}
                  loading={profileLoading}
                  disabled={passwordLock.isLocked || refreshLoading || profileLoading || loading}
                />
                <Button
                  variant="rising"
                  modifier="solid"
                  icon="sparkles"
                  iconPosition="left"
                  label={annotateProgress || "AI Annotate"}
                  onClick={handleAnnotateWithLLM}
                  loading={annotateLoading}
                  disabled={passwordLock.isLocked || annotateLoading || loading}
                />
                <SchemaExportButton
                  onExport={handleExport}
                  disabled={passwordLock.isLocked || loading}
                />
                <SchemaInitButton
                  onInit={handleInit}
                  isLoading={initLoading}
                  hasExistingSchema={status?.exists ?? false}
                  disabled={passwordLock.isLocked}
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
            {/* Summary card */}
            <m.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, delay: 0.1 }}
            >
              <SchemaSummaryCard status={status!} />
            </m.div>

            {/* Tables section */}
            <m.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, delay: 0.2 }}
            >
              <Card className="w-full overflow-hidden">
                <Card.Header>
                  <HStack className="justify-between items-center w-full">
                    <HStack className="gap-2 items-center">
                      <Icon name="layers" label="Tables" className="w-4 h-4 text-content-layout-3" />
                      <Text level="label-medium" className="text-content-layout-1">
                        Tables
                      </Text>
                      <Tag
                        size="small"
                        variant="informative"
                        modifier="ghost"
                        label={String(schema!.tables.length)}
                      />
                    </HStack>
                    <Button
                      modifier="ghost"
                      size="small"
                      icon="connect"
                      iconPosition="left"
                      label="Add Relationship"
                      onClick={() => handleAddRelationship()}
                      disabled={passwordLock.isLocked}
                    />
                  </HStack>
                </Card.Header>
                <Card.Content className="p-0">
                  <SchemaTableTree
                    tables={schema!.tables}
                    onEditColumn={handleEditColumn}
                    onEditTable={handleEditTable}
                    onEditEnum={handleEditEnum}
                    onEditRelationship={handleEditRelationship}
                  />
                </Card.Content>
              </Card>
            </m.div>

            {/* Terminology section */}
            <m.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, delay: 0.3 }}
            >
              <Card className="w-full overflow-hidden">
                <Card.Header>
                  <HStack className="justify-between items-center w-full">
                    <HStack className="gap-2 items-center">
                      <Icon name="folder-file" label="Terminology" className="w-4 h-4 text-content-layout-3" />
                      <Text level="label-medium" className="text-content-layout-1">
                        Business Terminology
                      </Text>
                      <Tag
                        size="small"
                        variant="informative"
                        modifier="ghost"
                        label={String(schema!.terminology.length)}
                      />
                    </HStack>
                    <Button
                      modifier="ghost"
                      size="small"
                      icon="add"
                      iconPosition="left"
                      label="Add Term"
                      onClick={handleAddTerm}
                      disabled={passwordLock.isLocked}
                    />
                  </HStack>
                </Card.Header>
                <Card.Content className="p-0">
                  <SchemaTerminologyList terminology={schema!.terminology} onEdit={handleEditTerm} />
                </Card.Content>
              </Card>
            </m.div>

            {/* Metrics section */}
            <m.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, delay: 0.4 }}
            >
              <Card className="w-full overflow-hidden">
                <Card.Header>
                  <HStack className="justify-between items-center w-full">
                    <HStack className="gap-2 items-center">
                      <Icon name="speedometer" label="Metrics" className="w-4 h-4 text-content-layout-3" />
                      <Text level="label-medium" className="text-content-layout-1">
                        Metrics
                      </Text>
                      <Tag
                        size="small"
                        variant="informative"
                        modifier="ghost"
                        label={String(schema!.metrics.length)}
                      />
                    </HStack>
                    <Button
                      modifier="ghost"
                      size="small"
                      icon="add"
                      iconPosition="left"
                      label="Add Metric"
                      onClick={handleAddMetric}
                      disabled={passwordLock.isLocked}
                    />
                  </HStack>
                </Card.Header>
                <Card.Content className="p-0">
                  <SchemaMetricsList metrics={schema!.metrics} onEdit={handleEditMetric} />
                </Card.Content>
              </Card>
            </m.div>

            {/* Danger zone */}
            <m.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, delay: 0.5 }}
            >
              <Card className="w-full border-border-negative-soft/30">
                <Card.Header className="border-b-border-negative-soft/30">
                  <HStack className="gap-2 items-center">
                    <Icon name="alert" label="Danger" className="w-4 h-4 text-content-negative-soft" />
                    <Text level="label-medium" className="text-content-negative-soft">
                      Danger Zone
                    </Text>
                  </HStack>
                </Card.Header>
                <Card.Content>
                  <VStack className="gap-4 items-start">
                    <Text level="body-small" className="text-content-layout-2">
                      Deleting the semantic layer will remove all table descriptions, column annotations,
                      and business terminology for this target.
                    </Text>
                    <Show when={deleteConfirm}>
                      <HStack className="gap-3 items-center">
                        <Text level="body-small" className="text-content-negative-soft">
                          Are you sure?
                        </Text>
                        <Button
                          variant="negative"
                          label="Yes, Delete"
                          onClick={handleDelete}
                          loading={loading}
                          disabled={passwordLock.isLocked}
                        />
                        <Button
                          modifier="ghost"
                          label="Cancel"
                          onClick={() => setDeleteConfirm(false)}
                          disabled={passwordLock.isLocked}
                        />
                      </HStack>
                    </Show>
                    <Show when={!deleteConfirm}>
                      <Button
                        variant="negative"
                        modifier="outline"
                        label="Delete Semantic Layer"
                        onClick={() => setDeleteConfirm(true)}
                        disabled={passwordLock.isLocked}
                      />
                    </Show>
                  </VStack>
                </Card.Content>
              </Card>
            </m.div>
          </div>
        )}

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

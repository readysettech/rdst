import { useState } from "react";
import { Text } from "@rs/ui-new/text";
import { Button } from "@rs/ui-new/button";
import { Icon } from "@rs/ui-new/icon";
import { HStack, VStack } from "@rs/ui-new/stack";
import { Card } from "@rs/ui-new/card";
import { Alert } from "@rs/ui-new/alert";
import { m, AnimatePresence } from "@rs/ui-new/motion";
import { ConfigureForm } from "../configure/ConfigureForm";
import { ConfigureTargetList } from "../configure/ConfigureTargetList";
import { ConfigureConnectionTest } from "../configure/ConfigureConnectionTest";
import type {
  ConfigureTarget,
  ConfigureTargetDetail,
  ConfigureFormData,
  ConfigureConnectionStatus,
} from "../../types/configure";
import type { ConfigureState } from "../../types/configure";

interface TargetsStepProps {
  targets: ConfigureTarget[];
  defaultTarget: string | null;
  onGetTarget: (name: string) => Promise<ConfigureTargetDetail | null>;
  onAddTarget: (data: ConfigureFormData) => Promise<void>;
  onUpdateTarget: (name: string, data: ConfigureFormData) => Promise<void>;
  onRemoveTarget: (name: string) => Promise<void>;
  onSetDefault: (name: string) => Promise<void>;
  onTestConnection: (name: string) => void;
  connectionTestResult: ConfigureConnectionStatus | null;
  state: ConfigureState;
  onNext: () => void;
  onBack: () => void;
  isLoading?: boolean;
}

export function TargetsStep({
  targets,
  defaultTarget,
  onGetTarget,
  onAddTarget,
  onUpdateTarget,
  onRemoveTarget,
  onSetDefault,
  onTestConnection,
  connectionTestResult,
  state,
  onNext,
  onBack,
  isLoading,
}: TargetsStepProps) {
  const [showForm, setShowForm] = useState(false);
  const [editingTarget, setEditingTarget] = useState<ConfigureTargetDetail | null>(null);

  const handleAddClick = () => {
    setEditingTarget(null);
    setShowForm(true);
  };

  const handleEditClick = async (target: ConfigureTarget) => {
    const targetDetail = await onGetTarget(target.name);
    if (!targetDetail) {
      return;
    }
    setEditingTarget(targetDetail);
    setShowForm(true);
  };

  const handleFormSubmit = async (data: ConfigureFormData) => {
    try {
      if (editingTarget) {
        await onUpdateTarget(editingTarget.name, data);
      } else {
        await onAddTarget(data);
      }
    } catch {
      return;
    }
    setShowForm(false);
    setEditingTarget(null);
  };

  const handleFormCancel = () => {
    setShowForm(false);
    setEditingTarget(null);
  };

  const handleDelete = async (targetName: string) => {
    if (confirm(`Are you sure you want to delete target "${targetName}"?`)) {
      await onRemoveTarget(targetName);
    }
  };

  const hasTargets = targets.length > 0;

  return (
    <div className="space-y-6">
      {/* Main Card */}
      <Card>
        {/* Header */}
        <div className="px-6 py-5 border-b border-border-layout-1">
          <HStack className="gap-4 items-center justify-between">
            <HStack className="gap-4 items-center">
              <div className="w-12 h-12 rounded-2xl bg-surface-info-soft flex items-center justify-center">
                <Icon name="database" label="Targets" className="w-6 h-6 text-content-info-soft" />
              </div>
              <VStack className="gap-0.5 items-start">
                <Text level="headline-3" className="text-content-layout-1">
                  Configure Database Targets
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  Add at least one database to start analyzing queries
                </Text>
              </VStack>
            </HStack>
            {/* Only show Add button when we have targets (not in empty state) */}
            <AnimatePresence>
              {!showForm && hasTargets && (
                <m.div
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.9 }}
                >
                  <Button
                    variant="rising"
                    icon="add"
                    iconPosition="left"
                    label="Add Target"
                    onClick={handleAddClick}
                  />
                </m.div>
              )}
            </AnimatePresence>
          </HStack>
        </div>

        {/* Content */}
        <div className="p-6">
          {/* Empty state */}
          <AnimatePresence mode="wait">
            {!hasTargets && !showForm && (
              <m.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                className="py-8 flex flex-col items-center text-center"
              >
                <div className="w-16 h-16 rounded-2xl bg-surface-layout-2 flex items-center justify-center mb-4">
                  <Icon name="database" label="No targets" className="w-8 h-8 text-content-layout-3" />
                </div>
                <Text level="subtitle-2" className="text-content-layout-2">
                  No targets configured yet
                </Text>
                <Text level="body-small" className="text-content-layout-3 mt-1 max-w-sm">
                  Add a PostgreSQL or MySQL database connection to get started with query analysis.
                </Text>
                <Button
                  variant="rising"
                  icon="add"
                  iconPosition="left"
                  label="Add Your First Target"
                  onClick={handleAddClick}
                  className="mt-6"
                />
              </m.div>
            )}
          </AnimatePresence>

          {/* Show form inline */}
          <AnimatePresence>
            {showForm && (
              <m.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
              >
                <ConfigureForm
                  initialData={
                    editingTarget
                      ? {
                          name: editingTarget.name,
                          engine: editingTarget.engine,
                          host: editingTarget.host,
                          port: editingTarget.port,
                          database: editingTarget.database,
                          user: editingTarget.user,
                          password_env: editingTarget.password_env,
                          tls: editingTarget.tls,
                          read_only: editingTarget.read_only,
                        }
                      : undefined
                  }
                  onSubmit={handleFormSubmit}
                  onCancel={handleFormCancel}
                  isLoading={isLoading}
                />
              </m.div>
            )}
          </AnimatePresence>
        </div>

        {/* Footer with navigation */}
        <div className="px-6 py-4 border-t border-border-layout-1 bg-surface-layout-2/30">
          <HStack className="justify-between w-full items-center">
            <Button
              variant="primary"
              modifier="ghost"
              icon="chevron-left"
              iconPosition="left"
              label="Back"
              onClick={onBack}
              disabled={isLoading}
            />
            <HStack className="gap-3 items-center">
              {hasTargets && (
                <m.div
                  initial={{ opacity: 0, x: 10 }}
                  animate={{ opacity: 1, x: 0 }}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-surface-positive-soft/10 border border-border-positive-soft/30"
                >
                  <Icon name="tick" label="Ready" className="w-4 h-4 text-content-positive-soft" />
                  <Text level="label-small" className="text-content-positive-soft">
                    {targets.length} target{targets.length !== 1 ? "s" : ""} configured
                  </Text>
                </m.div>
              )}
              <Button
                variant="primary"
                icon="arrow-right"
                iconPosition="right"
                label="Continue"
                onClick={onNext}
                disabled={!hasTargets || isLoading}
              />
            </HStack>
          </HStack>
        </div>
      </Card>

      {/* Connection Test Result - outside card */}
      <AnimatePresence>
        {connectionTestResult && (
          <m.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
          >
            <ConfigureConnectionTest result={connectionTestResult} isLoading={state === "loading"} />
          </m.div>
        )}
      </AnimatePresence>

      {/* Target List - outside card when we have targets */}
      <AnimatePresence>
        {!showForm && hasTargets && (
          <m.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
          >
                        <ConfigureTargetList
                          targets={targets}
                          onEdit={(target) => void handleEditClick(target)}
                          onTest={onTestConnection}
                          onDelete={handleDelete}
                          onSetDefault={onSetDefault}
              isLoading={isLoading}
            />
          </m.div>
        )}
      </AnimatePresence>

      {/* Warning for no default */}
      <AnimatePresence>
        {!defaultTarget && hasTargets && (
          <m.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <Alert
              variant="warning"
              modifier="outline"
              label="No default target selected. Choose one to streamline query analysis."
            />
          </m.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/**
 * Configure page - Manage database connection targets
 */

import { createFileRoute } from "@tanstack/react-router";
import { useState, useEffect } from "react";
import { Text } from "@rs/ui-new/text";
import { Button } from "@rs/ui-new/button";
import { Icon } from "@rs/ui-new/icon";
import { HStack, VStack } from "@rs/ui-new/stack";
import { Show } from "@rs/ui-new/show";
import { Alert } from "@rs/ui-new/alert";
import { m } from "@rs/ui-new/motion";
import { useConfigure } from "../lib/useConfigure";
import {
  ConfigureForm,
  ConfigureTargetList,
  ConfigureConnectionTest,
} from "../components/configure";
import type { ConfigureTarget, ConfigureFormData } from "../types/configure";

export const Route = createFileRoute("/configure")({
  component: ConfigurePage,
});

function ConfigurePage() {
  const [showForm, setShowForm] = useState(false);
  const [editingTarget, setEditingTarget] = useState<ConfigureTarget | null>(null);

  const {
    listTargets,
    addTarget,
    updateTarget,
    removeTarget,
    setDefaultTarget,
    testConnection,
    state,
    targets,
    connectionTestResult,
    error,
    loading,
  } = useConfigure();

  // Load targets on mount
  useEffect(() => {
    listTargets();
  }, [listTargets]);

  const handleAddClick = () => {
    setEditingTarget(null);
    setShowForm(true);
  };

  const handleEditClick = (target: ConfigureTarget) => {
    setEditingTarget(target);
    setShowForm(true);
  };

  const handleFormSubmit = async (data: ConfigureFormData) => {
    if (editingTarget) {
      await updateTarget(editingTarget.name, data);
    } else {
      await addTarget(data);
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
      await removeTarget(targetName);
    }
  };

  const handleSetDefault = async (targetName: string) => {
    await setDefaultTarget(targetName);
  };

  const handleTest = (targetName: string) => {
    testConnection(targetName);
  };

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
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-positive-soft flex items-center justify-center">
              <Icon name="settings" label="Configure" className="w-6 h-6 text-content-primary-soft" />
            </div>
            <VStack className="gap-1 items-start">
              <Text as="h1" level="headline-3" className="text-content-layout-1">
                Database Targets
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Manage database connection profiles for RDST
              </Text>
            </VStack>
          </HStack>

          <Show when={!showForm}>
            <Button
              variant="rising"
              modifier="solid"
              icon="add"
              iconPosition="left"
              label="Add Target"
              onClick={handleAddClick}
            />
          </Show>
        </HStack>
      </m.div>

      {/* Error Display */}
      <Show when={!!error}>
        <m.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <Alert
            variant="negative"
            modifier="outline"
            label={`Error: ${error}`}
          />
        </m.div>
      </Show>

      {/* Form */}
      <Show when={showForm}>
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <ConfigureForm
            initialData={
              editingTarget
                ? {
                    name: editingTarget.name,
                    engine: editingTarget.engine,
                  }
                : undefined
            }
            onSubmit={handleFormSubmit}
            onCancel={handleFormCancel}
            isLoading={loading}
          />
        </m.div>
      </Show>

      {/* Connection Test Result */}
      <Show when={!!connectionTestResult}>
        <m.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}
        >
          <ConfigureConnectionTest result={connectionTestResult} isLoading={state === "loading"} />
        </m.div>
      </Show>

      {/* Target List */}
      <Show when={!showForm}>
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.1 }}
        >
          <ConfigureTargetList
            targets={targets}
            onEdit={handleEditClick}
            onTest={handleTest}
            onDelete={handleDelete}
            onSetDefault={handleSetDefault}
            isLoading={loading}
          />
        </m.div>
      </Show>
    </div>
  );
}

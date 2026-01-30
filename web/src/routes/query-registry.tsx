import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { BaseInputText } from "@rs/ui-new/base-input-text";
import { Button } from "@rs/ui-new/button";
import { Card } from "@rs/ui-new/card";
import { Icon } from "@rs/ui-new/icon";
import { Show } from "@rs/ui-new/show";
import { Tag } from "@rs/ui-new/tag";
import { Text } from "@rs/ui-new/text";
import { HStack, VStack } from "@rs/ui-new/stack";
import { m, AnimatePresence } from "@rs/ui-new/motion";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@rs/ui-new/tooltip";
import { useQueryRegistry } from "../lib/useQueryRegistry";
import { addQueryToRegistry } from "../lib/api";
import { SQLInput } from "../components/SQLInput";
import { SQLDisplay } from "../components/SQLDisplay";
import { useTarget } from "../hooks/useTarget";

export const Route = createFileRoute("/query-registry")({
  component: QueryRegistryPage,
});

function formatTimestamp(isoString: string): string {
  if (!isoString) return "";
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

function formatSource(source: string): string {
  switch (source) {
    case "analyze":
      return "Analyze";
    case "prompt":
      return "Ask";
    case "web":
      return "Web";
    case "top":
      return "Top";
    case "file":
      return "File";
    case "manual":
      return "Manual";
    default:
      return source || "Manual";
  }
}

function getSourceVariant(source: string): "positive" | "warning" | "informative" | "primary" {
  switch (source) {
    case "top":
      return "warning";
    case "analyze":
      return "primary";
    case "prompt":
      return "positive";
    default:
      return "informative";
  }
}

function QueryRegistryPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { queries, isLoading, removeQuery, updateTag } = useQueryRegistry();
  const [searchTerm, setSearchTerm] = useState("");
  const [editingHash, setEditingHash] = useState<string | null>(null);
  const [tagDraft, setTagDraft] = useState("");
  const [confirmingHash, setConfirmingHash] = useState<string | null>(null);
  const [newSql, setNewSql] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showAddForm, setShowAddForm] = useState(false);

  const { target } = useTarget();

  const filteredQueries = useMemo(() => {
    if (!searchTerm.trim()) return queries;
    const lowerTerm = searchTerm.toLowerCase();
    return queries.filter(
      (entry) =>
        entry.tag?.toLowerCase().includes(lowerTerm) ||
        entry.sql.toLowerCase().includes(lowerTerm)
    );
  }, [queries, searchTerm]);

  const handleCreate = async () => {
    if (!newSql.trim()) return;
    setIsSubmitting(true);
    try {
      await addQueryToRegistry(newSql, target || undefined);
      setNewSql("");
      setShowAddForm(false);
      queryClient.invalidateQueries({ queryKey: ["queryRegistry"] });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleAnalyze = (sql: string, target?: string) => {
    navigate({
      to: "/results",
      search: {
        query: sql.trim(),
        target: target || undefined,
      },
    });
  };

  return (
    <div className="space-y-6 w-full">
      {/* Hero Header */}
      <m.div
        className="space-y-4"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="justify-between items-start">
          <HStack className="gap-4 items-center">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
              <Icon name="folder-file" label="Query Registry" className="w-6 h-6 text-content-primary-soft" />
            </div>
            <VStack className="gap-1 items-start">
              <Text as="h1" level="headline-3" className="text-content-layout-1">
                Query Registry
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Saved queries from analysis sessions. Use the Analyze action to run a query.
              </Text>
            </VStack>
          </HStack>

          <Show when={!showAddForm}>
            <Button
              variant="primary"
              modifier="solid"
              label="Add Query"
              icon="add"
              iconPosition="left"
              onClick={() => setShowAddForm(true)}
            />
          </Show>
        </HStack>
      </m.div>

      {/* Add Query Form */}
      <AnimatePresence>
        {showAddForm && (
          <m.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3 }}
          >
            <Card className="w-full overflow-hidden">
              <Card.Content className="p-0">
                <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
                  <HStack className="gap-2 items-center">
                    <Icon name="add" label="Add" className="w-4 h-4 text-content-layout-3" />
                    <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                      Add New Query
                    </Text>
                  </HStack>
                </div>
                <div className="p-5">
                  <SQLInput
                    value={newSql}
                    onChange={setNewSql}
                    placeholder="Enter your SQL query..."
                    minHeight="12rem"
                    target={target}
                    showPrettify
                  />
                </div>
                <div className="px-5 py-4 border-t border-border-layout-1 bg-surface-layout-1">
                  <HStack className="justify-end gap-2">
                    <Button
                      variant="primary"
                      modifier="ghost"
                      label="Cancel"
                      onClick={() => {
                        setShowAddForm(false);
                        setNewSql("");
                      }}
                    />
                    <Button
                      variant="rising"
                      modifier="solid"
                      label="Save Query"
                      icon="tick"
                      iconPosition="left"
                      onClick={handleCreate}
                      loading={isSubmitting}
                      disabled={!newSql.trim()}
                    />
                  </HStack>
                </div>
              </Card.Content>
            </Card>
          </m.div>
        )}
      </AnimatePresence>

      {/* Query List */}
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.1 }}
      >
        <Card className="w-full overflow-hidden">
          <Card.Content className="p-0">
            {/* Header with search */}
            <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
              <HStack className="justify-between items-center gap-4">
                <HStack className="gap-2 items-center">
                  <Icon name="layers" label="Queries" className="w-4 h-4 text-content-layout-3" />
                  <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                    Saved Queries
                  </Text>
                  <Tag
                    size="small"
                    variant="informative"
                    modifier="ghost"
                    label={`${queries.length} total`}
                  />
                </HStack>
                <div className="w-64">
                  <BaseInputText
                    name="search"
                    placeholder="Search queries..."
                    icon="search"
                    iconPosition="left"
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                  />
                </div>
              </HStack>
            </div>

            {/* Loading state */}
            <Show when={isLoading}>
              <div className="p-16">
                <VStack className="gap-4 items-center">
                  <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
                    <Icon name="folder-file" label="Loading" className="w-7 h-7 text-content-layout-3 animate-pulse" />
                  </div>
                  <Text level="body-small" className="text-content-layout-3">
                    Loading queries...
                  </Text>
                </VStack>
              </div>
            </Show>

            {/* Empty state */}
            <Show when={!isLoading && filteredQueries.length === 0}>
              <div className="p-16">
                <VStack className="gap-4 items-center">
                  <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
                    <Icon name="folder-file" label="Empty" className="w-7 h-7 text-content-layout-3" />
                  </div>
                  <VStack className="gap-2 items-center">
                    <Text level="headline-5" className="text-content-layout-2">
                      {searchTerm ? "No matching queries" : "No saved queries"}
                    </Text>
                    <Text level="body-small" className="text-content-layout-3 text-center max-w-sm">
                      {searchTerm
                        ? "Try adjusting your search term."
                        : "Queries you analyze will be saved here for quick access."}
                    </Text>
                  </VStack>
                  <Show when={!searchTerm && !showAddForm}>
                    <Button
                      variant="primary"
                      modifier="outline"
                      label="Add your first query"
                      icon="add"
                      iconPosition="left"
                      onClick={() => setShowAddForm(true)}
                    />
                  </Show>
                </VStack>
              </div>
            </Show>

            {/* Query table */}
            <Show when={!isLoading && filteredQueries.length > 0}>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="bg-surface-layout-2/30">
                      <th className="px-4 py-3 text-left text-xs text-content-layout-3 uppercase tracking-wider font-medium">
                        Name / SQL
                      </th>
                      <th className="px-4 py-3 text-left text-xs text-content-layout-3 uppercase tracking-wider font-medium w-20">
                        Source
                      </th>
                      <th className="px-4 py-3 text-left text-xs text-content-layout-3 uppercase tracking-wider font-medium w-32">
                        Target
                      </th>
                      <th className="px-4 py-3 text-left text-xs text-content-layout-3 uppercase tracking-wider font-medium w-28">
                        Analyzed
                      </th>
                      <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-44">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-layout-1">
                    <AnimatePresence mode="popLayout">
                      {filteredQueries.map((entry, index) => (
                        <m.tr
                          key={entry.hash}
                          initial={{ opacity: 0, y: -10 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0, x: -20 }}
                          transition={{ duration: 0.2, delay: index * 0.02 }}
                          className="group hover:bg-surface-layout-2/50 transition-colors"
                        >
                          {confirmingHash === entry.hash ? (
                            <td colSpan={5} className="px-4 py-4">
                              <div className="flex items-center justify-between gap-4 bg-surface-negative-soft/20 rounded-lg p-4 border border-border-negative/30">
                                <HStack className="gap-3 items-center flex-1 min-w-0">
                                  <Icon name="alert" label="Warning" className="w-5 h-5 text-content-negative-soft shrink-0" />
                                  <VStack className="gap-1 items-start min-w-0">
                                    <Text level="label-small" className="text-content-layout-1">
                                      Delete this query?
                                    </Text>
                                    <div className="bg-surface-layout-2 px-2 py-1 rounded max-w-md overflow-hidden">
                                      <SQLDisplay
                                        sql={entry.sql.length > 60 ? `${entry.sql.slice(0, 60)}...` : entry.sql}
                                        wrap={false}
                                      />
                                    </div>
                                  </VStack>
                                </HStack>
                                <HStack className="gap-2 shrink-0">
                                  <Button
                                    variant="primary"
                                    modifier="ghost"
                                    size="small"
                                    label="Cancel"
                                    onClick={() => setConfirmingHash(null)}
                                  />
                                  <Button
                                    variant="negative"
                                    modifier="solid"
                                    size="small"
                                    label="Delete"
                                    icon="trash"
                                    iconPosition="left"
                                    onClick={() => {
                                      removeQuery(entry.hash);
                                      setConfirmingHash(null);
                                    }}
                                  />
                                </HStack>
                              </div>
                            </td>
                          ) : (
                            <>
                              <td className="px-4 py-3">
                                <VStack className="gap-2 items-start min-w-0">
                                  {editingHash === entry.hash ? (
                                    <HStack className="gap-2 items-center w-full">
                                      <div className="flex-1">
                                        <BaseInputText
                                          name={`edit-tag-${entry.hash}`}
                                          placeholder="Enter name"
                                          value={tagDraft}
                                          onChange={(e) => setTagDraft(e.target.value)}
                                        />
                                      </div>
                                      <Button
                                        variant="primary"
                                        modifier="ghost"
                                        size="small"
                                        icon="tick"
                                        iconPosition="icon"
                                        label="Save"
                                        onClick={() => {
                                          updateTag(entry.hash, tagDraft.trim());
                                          setEditingHash(null);
                                          setTagDraft("");
                                        }}
                                      />
                                      <Button
                                        variant="primary"
                                        modifier="ghost"
                                        size="small"
                                        icon="close"
                                        iconPosition="icon"
                                        label="Cancel"
                                        onClick={() => {
                                          setEditingHash(null);
                                          setTagDraft("");
                                        }}
                                      />
                                    </HStack>
                                  ) : (
                                    <HStack className="gap-2 items-center">
                                      <Text level="label-small" className="text-content-layout-1 font-medium">
                                        {entry.tag || "(unnamed)"}
                                      </Text>
                                      <Text level="mono-small" className="text-content-layout-3">
                                        {entry.hash.slice(0, 8)}
                                      </Text>
                                    </HStack>
                                  )}
                                  <div
                                    className="text-left bg-surface-layout-2 px-3 py-2 rounded-lg max-w-md"
                                    title={`${entry.sql}\n\nUse the Analyze action to run this query.`}
                                  >
                                    <SQLDisplay
                                      sql={entry.sql.length > 60 ? `${entry.sql.slice(0, 60)}...` : entry.sql}
                                      wrap={false}
                                    />
                                  </div>
                                </VStack>
                              </td>
                              <td className="px-4 py-3 align-top">
                                <Tag
                                  size="small"
                                  variant={getSourceVariant(entry.source)}
                                  modifier="ghost"
                                  label={formatSource(entry.source)}
                                />
                              </td>
                              <td className="px-4 py-3 align-top">
                                <Show when={entry.target}>
                                  <HStack className="gap-1.5 items-center">
                                    <Icon name="database" label="Target" className="w-3 h-3 text-content-layout-3" />
                                    <Text level="mono-small" className="text-content-layout-2">
                                      {entry.target}
                                    </Text>
                                  </HStack>
                                </Show>
                                <Show when={!entry.target}>
                                  <Text level="mono-small" className="text-content-layout-3">
                                    -
                                  </Text>
                                </Show>
                              </td>
                              <td className="px-4 py-3 align-top">
                                <HStack className="gap-1.5 items-center">
                                  <Icon name="observe" label="Time" className="w-3 h-3 text-content-layout-3" />
                                  <Text level="caption" className="text-content-layout-3 whitespace-nowrap">
                                    {formatTimestamp(entry.last_analyzed)}
                                  </Text>
                                </HStack>
                              </td>
                              <td className="px-4 py-3 align-top">
                                <HStack className="gap-1 justify-end">
                                  <TooltipProvider delayDuration={150}>
                                    <Tooltip>
                                      <TooltipTrigger asChild>
                                        <div>
                                          <Button
                                            variant="primary"
                                            modifier="ghost"
                                            icon="speedometer"
                                            iconPosition="left"
                                            label="Analyze"
                                            onClick={() => handleAnalyze(entry.sql, entry.target)}
                                          />
                                        </div>
                                      </TooltipTrigger>
                                      <TooltipContent label="Analyze this query. If parameters are required, you'll be prompted first." />
                                    </Tooltip>
                                  </TooltipProvider>
                                  <Button
                                    variant="primary"
                                    modifier="ghost"
                                    size="small"
                                    icon="edit"
                                    iconPosition="icon"
                                    label="Rename"
                                    onClick={() => {
                                      setEditingHash(entry.hash);
                                      setTagDraft(entry.tag || "");
                                    }}
                                  />
                                  <Button
                                    variant="negative"
                                    modifier="ghost"
                                    size="small"
                                    icon="trash"
                                    iconPosition="icon"
                                    label="Delete"
                                    onClick={() => setConfirmingHash(entry.hash)}
                                  />
                                </HStack>
                              </td>
                            </>
                          )}
                        </m.tr>
                      ))}
                    </AnimatePresence>
                  </tbody>
                </table>
              </div>
            </Show>
          </Card.Content>
        </Card>
      </m.div>
    </div>
  );
}

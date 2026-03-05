import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo, useEffect } from "react";
import { BaseInputText } from "@rs/ui-new/base-input-text";
import { Button } from "@rs/ui-new/button";
import { Card } from "@rs/ui-new/card";
import { Icon } from "@rs/ui-new/icon";
import { Show } from "@rs/ui-new/show";
import { Text } from "@rs/ui-new/text";
import { Tag } from "@rs/ui-new/tag";
import { HStack, VStack } from "@rs/ui-new/stack";
import { m, AnimatePresence } from "@rs/ui-new/motion";

import { TargetLockNotice } from "../components";
import { useQueryRegistry } from "../lib/useQueryRegistry";
import { useTarget } from "../hooks/useTarget";
import { useTargetPasswordLock } from "../lib/useTargetPasswordLock";
import { useBenchmark } from "../lib/sse";
import { hasParameters } from "../components/BenchmarkParameterDialog";
import { SQLDisplay } from "../components/SQLDisplay";
import type { BenchmarkMode, QueryBenchmarkStats, BenchmarkQueryInput } from "../lib/api";

export const Route = createFileRoute("/benchmark")({
  component: BenchmarkPage,
});

type WizardStep = "configure" | "running";

function formatDuration(ms: number): string {
  if (ms < 1) return "<1ms";
  if (ms < 1000) return `${ms.toFixed(1)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function formatNumber(n: number): string {
  return n.toLocaleString();
}

interface Parameter {
  placeholder: string;
  index: number;
  type: "positional" | "named";
}

function detectParameters(sql: string): Parameter[] {
  const params: Parameter[] = [];
  const seen = new Set<string>();

  const pgMatches = sql.matchAll(/\$(\d+)/g);
  for (const match of pgMatches) {
    const placeholder = match[0];
    if (!seen.has(placeholder)) {
      seen.add(placeholder);
      params.push({ placeholder, index: Number.parseInt(match[1], 10), type: "positional" });
    }
  }

  let questionIndex = 1;
  const mysqlMatches = sql.matchAll(/\?/g);
  for (const _ of mysqlMatches) {
    params.push({ placeholder: "?", index: questionIndex, type: "positional" });
    questionIndex++;
  }

  const namedMatches = sql.matchAll(/(?<!:)[:@]([a-zA-Z_][a-zA-Z0-9_]*)/g);
  for (const match of namedMatches) {
    const placeholder = match[0];
    if (!seen.has(placeholder)) {
      seen.add(placeholder);
      params.push({ placeholder, index: params.length + 1, type: "named" });
    }
  }

  return params.sort((a, b) => a.index - b.index);
}

function formatValue(value: string): string {
  const trimmed = value.trim();
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return trimmed;
  if (["NULL", "TRUE", "FALSE"].includes(trimmed.toUpperCase())) return trimmed.toUpperCase();
  if ((trimmed.startsWith("'") && trimmed.endsWith("'")) || (trimmed.startsWith('"') && trimmed.endsWith('"'))) return trimmed;
  return `'${trimmed.replace(/'/g, "''")}'`;
}

function substituteParameters(sql: string, params: Parameter[], values: Record<string, string>): string {
  let result = sql;
  const questionParams = params.filter((p) => p.placeholder === "?");
  if (questionParams.length > 0) {
    const parts: string[] = [];
    let lastIndex = 0;
    let qIndex = 0;
    for (let i = 0; i < result.length; i++) {
      if (result[i] === "?") {
        parts.push(result.slice(lastIndex, i));
        parts.push(formatValue(values[`?${qIndex + 1}`] || ""));
        lastIndex = i + 1;
        qIndex++;
      }
    }
    parts.push(result.slice(lastIndex));
    result = parts.join("");
  }
  for (const param of params) {
    if (param.placeholder !== "?") {
      const value = values[param.placeholder] || "";
      const escaped = param.placeholder.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      result = result.replace(new RegExp(escaped, "g"), formatValue(value));
    }
  }
  return result;
}

/**
 * Resolve an initial value from backend's most_recent_params for a given parameter.
 * Backend stores params as {p1: value, p2: value, ...} (SQLGlot-normalized keys).
 */
function resolveInitialValue(
  param: Parameter,
  storedParams: Record<string, string | number> | undefined
): string {
  if (!storedParams) return "";
  let backendKey: string;
  if (param.type === "named") {
    backendKey = param.placeholder.replace(/^[:@]/, "");
  } else if (param.placeholder.startsWith("$")) {
    backendKey = param.placeholder.replace("$", "p");
  } else {
    backendKey = `p${param.index}`;
  }
  const value = storedParams[backendKey];
  return value != null ? String(value) : "";
}

// Step indicator component
function StepIndicator({ currentStep }: { currentStep: WizardStep }) {
  const steps = [
    { id: "configure", label: "Configure", icon: "settings" as const },
    { id: "running", label: "Run", icon: "play" as const },
  ];

  return (
    <HStack className="gap-3 items-center">
      {steps.map((step, index) => {
        const isCurrent = step.id === currentStep;
        const isPast = currentStep === "running" && step.id === "configure";

        return (
          <div key={step.id} className="contents">
            {index > 0 && (
              <div className={`w-12 h-0.5 rounded-full ${isPast || isCurrent ? 'bg-surface-positive-solid' : 'bg-border-layout-2'}`} />
            )}
            <div className={`flex items-center gap-2 px-4 py-2 rounded-full transition-colors ${
              isCurrent ? 'bg-surface-layout-2 border border-content-layout-2' : isPast ? 'bg-surface-positive-soft/50' : 'bg-surface-layout-2'
            }`}>
              <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-medium ${
                isPast
                  ? 'bg-surface-positive-solid text-white'
                  : isCurrent
                    ? 'bg-content-layout-1 text-surface-layout-1'
                    : 'bg-surface-layout-3 text-content-layout-3'
              }`}>
                {isPast ? (
                  <Icon name="tick" label="Complete" className="w-3.5 h-3.5" />
                ) : (
                  index + 1
                )}
              </div>
              <Text level="label-small" className={
                isCurrent ? 'text-content-layout-1' : isPast ? 'text-content-positive-soft' : 'text-content-layout-3'
              }>
                {step.label}
              </Text>
            </div>
          </div>
        );
      })}
    </HStack>
  );
}

// Metric card component
function MetricCard({ label, value, variant = "default" }: { label: string; value: string; variant?: "default" | "positive" | "negative" }) {
  return (
    <Card className="flex-1">
      <Card.Content className="p-4">
        <VStack className="gap-1 items-start">
          <Text level="caption" className="text-content-layout-3">
            {label}
          </Text>
          <Text level="headline-4" className={
            variant === "positive" ? "text-content-positive-soft" :
            variant === "negative" ? "text-content-negative-soft" :
            "text-content-layout-1"
          }>
            {value}
          </Text>
        </VStack>
      </Card.Content>
    </Card>
  );
}

export function BenchmarkPage() {
  const { queries, isLoading: registryLoading } = useQueryRegistry();
  const { target } = useTarget();
  const passwordLock = useTargetPasswordLock(target);
  const { start, stop, state, progress, error, reset } = useBenchmark();

  const [step, setStep] = useState<WizardStep>("configure");
  const [selectedQueries, setSelectedQueries] = useState<string[]>([]);
  const [searchTerm, setSearchTerm] = useState("");
  const [mode, setMode] = useState<BenchmarkMode>("interval");
  const [intervalMs, setIntervalMs] = useState(100);
  const [concurrency, setConcurrency] = useState(1);
  const [durationSeconds, setDurationSeconds] = useState(30);
  const [paramValues, setParamValues] = useState<Record<string, string>>({});

  const filteredQueries = queries.filter(
    (q) =>
      !searchTerm.trim() ||
      q.tag?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      q.sql.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const selectedQueryObjects = useMemo(() => {
    return selectedQueries
      .map((id) => {
        const q = queries.find((query) => (query.tag || query.hash) === id);
        if (!q) return null;
        return {
          ...q,
          identifier: q.tag || q.hash,
          name: q.tag || q.hash.slice(0, 8),
          parameters: detectParameters(q.sql),
        };
      })
      .filter((q): q is NonNullable<typeof q> => q !== null);
  }, [selectedQueries, queries]);

  const hasQueriesWithParams = useMemo(() => {
    return selectedQueryObjects.some((q) => q.parameters.length > 0);
  }, [selectedQueryObjects]);

  const allParamsFilled = useMemo(() => {
    for (const q of selectedQueryObjects) {
      for (const p of q.parameters) {
        const key = p.placeholder === "?" ? `${q.identifier}:?${p.index}` : `${q.identifier}:${p.placeholder}`;
        if (!paramValues[key]?.trim()) return false;
      }
    }
    return true;
  }, [selectedQueryObjects, paramValues]);

  // Pre-populate param values from most_recent_params when queries are selected
  useEffect(() => {
    setParamValues((prev) => {
      const next = { ...prev };
      for (const q of selectedQueryObjects) {
        if (!q.most_recent_params || q.parameters.length === 0) continue;
        for (const p of q.parameters) {
          const key = p.placeholder === "?" ? `${q.identifier}:?${p.index}` : `${q.identifier}:${p.placeholder}`;
          // Only set if not already filled by the user
          if (!next[key]) {
            next[key] = resolveInitialValue(p, q.most_recent_params);
          }
        }
      }
      return next;
    });
  }, [selectedQueryObjects]);

  const canStart =
    !passwordLock.isLocked &&
    selectedQueries.length > 0 &&
    (!hasQueriesWithParams || allParamsFilled);

  useEffect(() => {
    if (step === "running" && (state === "complete" || state === "error")) {
      // Stay on running step to show results
    }
  }, [state, step]);

  const toggleQuery = (identifier: string) => {
    setSelectedQueries((prev) =>
      prev.includes(identifier) ? prev.filter((q) => q !== identifier) : [...prev, identifier]
    );
  };

  const handleParamChange = (queryId: string, placeholder: string, value: string) => {
    const key = `${queryId}:${placeholder}`;
    setParamValues((prev) => ({ ...prev, [key]: value }));
  };

  const handleStart = () => {
    if (passwordLock.isLocked) return;
    if (!canStart) return;

    const queryInputs: BenchmarkQueryInput[] = selectedQueryObjects.map((q) => {
      if (q.parameters.length > 0) {
        const values: Record<string, string> = {};
        for (const p of q.parameters) {
          const key = p.placeholder === "?" ? `${q.identifier}:?${p.index}` : `${q.identifier}:${p.placeholder}`;
          const paramKey = p.placeholder === "?" ? `?${p.index}` : p.placeholder;
          values[paramKey] = paramValues[key] || "";
        }
        return { identifier: q.identifier, sql: substituteParameters(q.sql, q.parameters, values) };
      }
      return { identifier: q.identifier };
    });

    setStep("running");
    start({
      queries: queryInputs,
      target: target || undefined,
      mode,
      interval_ms: mode === "interval" ? intervalMs : undefined,
      concurrency: mode === "concurrency" ? concurrency : undefined,
      duration_seconds: durationSeconds,
    });
  };

  const handleStop = () => stop();
  const handleBack = () => { reset(); setStep("configure"); };
  const handleRunAgain = () => {
    if (passwordLock.isLocked) return;
    handleStart();
  };

  // ========== CONFIGURE STEP ==========
  if (step === "configure") {
    return (
      <div className="space-y-6 w-full">
        {/* Hero Header */}
        <m.div
          className="space-y-4"
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <HStack className="gap-4 items-center">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-rising-soft flex items-center justify-center">
              <Icon name="play" label="Benchmark" className="w-6 h-6 text-content-primary-soft" />
            </div>
            <VStack className="gap-1 items-start">
              <Text as="h1" level="headline-3" className="text-content-layout-1">
                Benchmark
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Run load tests on saved queries to measure performance under stress
              </Text>
            </VStack>
          </HStack>
        </m.div>

        {/* Step indicator */}
        <m.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3, delay: 0.1 }}
        >
          <StepIndicator currentStep={step} />
        </m.div>

        <AnimatePresence>
          {passwordLock.isLocked && (
            <m.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
            >
              <TargetLockNotice
                message={passwordLock.message}
                requirements={passwordLock.missingTargetRequirements}
                keyringAvailable={passwordLock.keyringAvailable}
              />
            </m.div>
          )}
        </AnimatePresence>

        {/* Settings Card */}
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.15 }}
        >
          <Card className="w-full overflow-hidden">
            <Card.Content className="p-0">
              <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
                <HStack className="gap-2 items-center">
                  <Icon name="settings" label="Settings" className="w-4 h-4 text-content-layout-3" />
                  <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                    Configuration
                  </Text>
                </HStack>
              </div>
              <div className="p-5">
                <div className="grid grid-cols-2 gap-6">
                  {/* Execution Mode */}
                  <VStack className="gap-2 items-start">
                    <Text level="label-small" className="text-content-layout-2">
                      Execution Mode
                    </Text>
                    <div className="flex gap-2 p-1 bg-surface-layout-2 rounded-lg">
                      <button
                        type="button"
                        onClick={() => setMode("interval")}
                        className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
                          mode === "interval"
                            ? 'bg-surface-layout-1 text-content-layout-1 shadow-sm'
                            : 'text-content-layout-3 hover:text-content-layout-2'
                        } cursor-pointer`}
                      >
                        Fixed Interval
                      </button>
                      <button
                        type="button"
                        onClick={() => setMode("concurrency")}
                        className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
                          mode === "concurrency"
                            ? 'bg-surface-layout-1 text-content-layout-1 shadow-sm'
                            : 'text-content-layout-3 hover:text-content-layout-2'
                        } cursor-pointer`}
                      >
                        Concurrent
                      </button>
                    </div>
                  </VStack>

                  {/* Mode-specific setting */}
                  <VStack className="gap-2 items-start">
                    <Show when={mode === "interval"}>
                      <Text level="label-small" className="text-content-layout-2">
                        Interval (ms)
                      </Text>
                      <BaseInputText
                        name="interval"
                        type="number"
                        value={String(intervalMs)}
                        onChange={(e) => setIntervalMs(Number(e.target.value) || 0)}
                      />
                      <Text level="caption" className="text-content-layout-3">
                        {intervalMs === 0 ? "Tight loop (no delay)" : `Execute every ${intervalMs}ms`}
                      </Text>
                    </Show>
                    <Show when={mode === "concurrency"}>
                      <Text level="label-small" className="text-content-layout-2">
                        Concurrent Workers
                      </Text>
                      <BaseInputText
                        name="concurrency"
                        type="number"
                        value={String(concurrency)}
                        onChange={(e) => setConcurrency(Math.max(1, Number(e.target.value) || 1))}
                      />
                    </Show>
                  </VStack>

                  {/* Duration */}
                  <VStack className="gap-2 items-start">
                    <Text level="label-small" className="text-content-layout-2">
                      Duration (seconds)
                    </Text>
                    <BaseInputText
                      name="duration"
                      type="number"
                      value={String(durationSeconds)}
                      onChange={(e) => setDurationSeconds(Math.max(1, Number(e.target.value) || 30))}
                    />
                  </VStack>

                  {/* Target */}
                  <VStack className="gap-2 items-start">
                    <Text level="label-small" className="text-content-layout-2">
                      Target Database
                    </Text>
                    <HStack className="gap-2 items-center bg-surface-layout-2 px-3 py-2 rounded-lg w-full">
                      <Icon name="database" label="Target" className="w-4 h-4 text-content-layout-3" />
                      <Text level="mono-small" className="text-content-layout-2">
                        {target || "(default)"}
                      </Text>
                    </HStack>
                  </VStack>
                </div>
              </div>
            </Card.Content>
          </Card>
        </m.div>

        {/* Query Selection Card */}
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.2 }}
        >
          <Card className="w-full overflow-hidden">
            <Card.Content className="p-0">
              <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
                <HStack className="justify-between items-center gap-4">
                  <HStack className="gap-2 items-center">
                    <Icon name="layers" label="Queries" className="w-4 h-4 text-content-layout-3" />
                    <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                      Select Queries
                    </Text>
                    <Tag
                      size="small"
                      variant={selectedQueries.length > 0 ? "primary" : "informative"}
                      modifier="ghost"
                      label={`${selectedQueries.length} selected`}
                    />
                  </HStack>
                  <div className="w-64">
                    <BaseInputText
                      name="search"
                      placeholder="Filter queries..."
                      icon="search"
                      iconPosition="left"
                      value={searchTerm}
                      onChange={(e) => setSearchTerm(e.target.value)}
                    />
                  </div>
                </HStack>
              </div>

              <Show when={registryLoading}>
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

              <Show when={!registryLoading && filteredQueries.length === 0}>
                <div className="p-16">
                  <VStack className="gap-4 items-center">
                    <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
                      <Icon name="folder-file" label="Empty" className="w-7 h-7 text-content-layout-3" />
                    </div>
                    <VStack className="gap-2 items-center">
                      <Text level="headline-5" className="text-content-layout-2">
                        No queries available
                      </Text>
                      <Text level="body-small" className="text-content-layout-3 text-center max-w-sm">
                        Add queries from the Query Registry page to benchmark them.
                      </Text>
                    </VStack>
                  </VStack>
                </div>
              </Show>

              <Show when={!registryLoading && filteredQueries.length > 0}>
                <div className="max-h-80 overflow-y-auto p-4 space-y-2">
                  {filteredQueries.map((query, index) => {
                    const identifier = query.tag || query.hash;
                    const isSelected = selectedQueries.includes(identifier);
                    const queryHasParams = hasParameters(query.sql);
                    return (
                      <m.button
                        key={query.hash}
                        type="button"
                        initial={{ opacity: 0, x: -10 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ duration: 0.2, delay: index * 0.02 }}
                        onClick={() => toggleQuery(identifier)}
                        className={`w-full text-left p-4 rounded-xl border transition-all ${
                          isSelected
                            ? "bg-surface-primary-soft/50 border-border-primary-soft"
                            : "bg-surface-layout-2/50 border-border-layout-1 hover:bg-surface-layout-2"
                        } cursor-pointer`}
                      >
                        <HStack className="gap-3 items-start">
                          <div
                            className={`w-5 h-5 rounded-md border-2 flex items-center justify-center flex-shrink-0 mt-0.5 transition-colors ${
                              isSelected ? "bg-surface-primary-solid border-surface-primary-solid" : "border-border-layout-2"
                            }`}
                          >
                            {isSelected && (
                              <Icon name="tick" label="Selected" className="w-3 h-3 text-white" />
                            )}
                          </div>
                          <VStack className="gap-1 items-start flex-1 min-w-0">
                            <HStack className="gap-2 items-center flex-wrap">
                              <Text level="label-small" className="text-content-layout-1">
                                {query.tag || "(unnamed)"}
                              </Text>
                              <Text level="mono-small" className="text-content-layout-3">
                                {query.hash.slice(0, 8)}
                              </Text>
                              {queryHasParams && (
                                <Tag size="small" variant="warning" modifier="ghost" label="Has params" />
                              )}
                            </HStack>
                            <div className="bg-surface-layout-2 px-2 py-1 rounded-md max-w-full overflow-hidden">
                              <SQLDisplay
                                sql={query.sql.length > 80 ? `${query.sql.slice(0, 80)}...` : query.sql}
                                wrap={false}
                              />
                            </div>
                          </VStack>
                        </HStack>
                      </m.button>
                    );
                  })}
                </div>
              </Show>
            </Card.Content>
          </Card>
        </m.div>

        {/* Parameters Section */}
        <AnimatePresence>
          {hasQueriesWithParams && (
            <m.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.3 }}
            >
              <Card className="w-full overflow-hidden">
                <Card.Content className="p-0">
                  <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
                    <VStack className="gap-1 items-start">
                      <HStack className="gap-2 items-center">
                        <Icon name="key" label="Parameters" className="w-4 h-4 text-content-layout-3" />
                        <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                          Parameter Values
                        </Text>
                      </HStack>
                      <Text level="caption" className="text-content-layout-3">
                        Enter values for query parameters. Strings are automatically quoted.
                      </Text>
                    </VStack>
                  </div>

                  <div className="p-5 space-y-6">
                    {selectedQueryObjects
                      .filter((q) => q.parameters.length > 0)
                      .map((q) => (
                        <div key={q.identifier} className="space-y-3">
                          <HStack className="gap-2 items-center">
                            <Text level="label-small" className="text-content-layout-1">
                              {q.name}
                            </Text>
                            <Text level="mono-small" className="text-content-layout-3">
                              {q.identifier.slice(0, 8)}
                            </Text>
                          </HStack>

                          <div className="bg-surface-layout-2 rounded-lg p-3 max-h-32 overflow-auto">
                            <SQLDisplay sql={q.sql} wrap />
                          </div>

                          <div className="space-y-2 pl-4 border-l-2 border-border-primary-soft">
                            {q.parameters.map((param) => {
                              const paramKey = param.placeholder === "?" ? `?${param.index}` : param.placeholder;
                              const valueKey = `${q.identifier}:${paramKey}`;
                              return (
                                <HStack key={paramKey} className="gap-3 items-center">
                                  <div className="w-16 flex-shrink-0 text-right">
                                    <span className="inline-block px-2 py-1 rounded-md bg-surface-layout-2 text-content-layout-2 font-mono text-sm">
                                      {paramKey}
                                    </span>
                                  </div>
                                  <div className="flex-1">
                                    <BaseInputText
                                      name={`param-${valueKey}`}
                                      value={paramValues[valueKey] || ""}
                                      onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                                        handleParamChange(q.identifier, paramKey, e.target.value)
                                      }
                                      placeholder="Enter value"
                                    />
                                  </div>
                                </HStack>
                              );
                            })}
                          </div>
                        </div>
                      ))}
                  </div>
                </Card.Content>
              </Card>
            </m.div>
          )}
        </AnimatePresence>

        {/* Start Button */}
        <m.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3, delay: 0.3 }}
        >
          <HStack className="justify-end">
            <Button
              variant="rising"
              modifier="solid"
              label="Start Benchmark"
              icon="play"
              iconPosition="left"
              onClick={handleStart}
              disabled={!canStart}
            />
          </HStack>
        </m.div>
      </div>
    );
  }

  // ========== RUNNING STEP ==========
  const errorRate = progress?.total_executions
    ? ((progress.total_failures / progress.total_executions) * 100).toFixed(1)
    : "0.0";

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
            <div className={`w-12 h-12 rounded-2xl flex items-center justify-center ${
              state === "running"
                ? "bg-gradient-to-br from-surface-primary-soft to-surface-info-soft"
                : state === "error"
                  ? "bg-gradient-to-br from-surface-negative-soft to-surface-warning-soft"
                  : "bg-gradient-to-br from-surface-positive-soft to-surface-primary-soft"
            }`}>
              <Icon
                name={state === "running" ? "play" : state === "error" ? "alert" : "tick-double"}
                label="Status"
                className={`w-6 h-6 ${
                  state === "running" ? "text-content-primary-soft animate-pulse" :
                  state === "error" ? "text-content-negative-soft" :
                  "text-content-positive-soft"
                }`}
              />
            </div>
            <VStack className="gap-1 items-start">
              <HStack className="gap-3 items-center">
                <Text as="h1" level="headline-3" className="text-content-layout-1">
                  Benchmark
                </Text>
                <Tag
                  variant={state === "running" ? "informative" : state === "error" ? "negative" : "positive"}
                  modifier="solid"
                  label={state === "running" ? "Running..." : state === "error" ? "Error" : "Complete"}
                />
              </HStack>
              <Text level="body-small" className="text-content-layout-3">
                {selectedQueries.length} queries • {mode === "interval" ? `${intervalMs}ms interval` : `${concurrency} workers`} • {durationSeconds}s duration
              </Text>
            </VStack>
          </HStack>
          <Show when={state === "running"}>
            <Button variant="negative" modifier="solid" label="Stop" icon="close" iconPosition="left" onClick={handleStop} />
          </Show>
        </HStack>
      </m.div>

      <AnimatePresence>
        {passwordLock.isLocked && (
          <m.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
          >
            <TargetLockNotice
              message={passwordLock.message}
              requirements={passwordLock.missingTargetRequirements}
              keyringAvailable={passwordLock.keyringAvailable}
            />
          </m.div>
        )}
      </AnimatePresence>

      {/* Step indicator */}
      <StepIndicator currentStep={step} />

      {/* Summary Metrics */}
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.1 }}
      >
        <HStack className="gap-4">
          <MetricCard label="Duration" value={`${progress?.elapsed_seconds.toFixed(1) || "0.0"}s`} />
          <MetricCard label="Total Executions" value={formatNumber(progress?.total_executions || 0)} />
          <MetricCard label="QPS" value={(progress?.qps || 0).toFixed(2)} />
          <MetricCard
            label="Error Rate"
            value={`${errorRate}%`}
            variant={(progress?.total_failures || 0) > 0 ? "negative" : "positive"}
          />
        </HStack>
      </m.div>

      {/* Per-query stats table */}
      <Show when={(progress?.queries?.length || 0) > 0}>
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.2 }}
        >
          <Card className="w-full overflow-hidden">
            <Card.Content className="p-0">
              <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
                <HStack className="gap-2 items-center">
                  <Icon name="layers" label="Results" className="w-4 h-4 text-content-layout-3" />
                  <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                    Query Performance
                  </Text>
                </HStack>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="bg-surface-layout-2/30">
                      <th className="px-4 py-3 text-left text-xs text-content-layout-3 uppercase tracking-wider font-medium">
                        Query
                      </th>
                      <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-24">
                        Executions
                      </th>
                      <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-20">
                        Errors
                      </th>
                      <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-20">
                        Min
                      </th>
                      <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-20">
                        Avg
                      </th>
                      <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-20">
                        P95
                      </th>
                      <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-20">
                        Max
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-layout-1">
                    <AnimatePresence mode="popLayout">
                      {progress?.queries.map((q: QueryBenchmarkStats, index: number) => (
                        <m.tr
                          key={q.query_hash}
                          initial={{ opacity: 0, y: -10 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ duration: 0.2, delay: index * 0.03 }}
                          className="hover:bg-surface-layout-2/50 transition-colors"
                        >
                          <td className="px-4 py-3">
                            <VStack className="gap-1 items-start">
                              <HStack className="gap-2 items-center">
                                <Text level="label-small" className="text-content-layout-1">
                                  {q.query_name}
                                </Text>
                                <Text level="mono-small" className="text-content-layout-3">
                                  {q.query_hash.slice(0, 8)}
                                </Text>
                              </HStack>
                              {q.last_error && (
                                <Text level="caption" className="text-content-negative-soft">
                                  {q.last_error}
                                </Text>
                              )}
                            </VStack>
                          </td>
                          <td className="px-4 py-3 text-right align-top">
                            <Text level="mono-small" className="text-content-layout-1">
                              {formatNumber(q.executions)}
                            </Text>
                          </td>
                          <td className="px-4 py-3 text-right align-top">
                            <Text level="mono-small" className={q.failures > 0 ? "text-content-negative-soft" : "text-content-layout-3"}>
                              {formatNumber(q.failures)}
                            </Text>
                          </td>
                          <td className="px-4 py-3 text-right align-top">
                            <Text level="mono-small" className="text-content-layout-2">
                              {formatDuration(q.min_ms)}
                            </Text>
                          </td>
                          <td className="px-4 py-3 text-right align-top">
                            <Text level="mono-small" className="text-content-layout-2">
                              {formatDuration(q.avg_ms)}
                            </Text>
                          </td>
                          <td className="px-4 py-3 text-right align-top">
                            <Text level="mono-small" className="text-content-layout-2">
                              {formatDuration(q.p95_ms)}
                            </Text>
                          </td>
                          <td className="px-4 py-3 text-right align-top">
                            <Text level="mono-small" className="text-content-layout-2">
                              {formatDuration(q.max_ms)}
                            </Text>
                          </td>
                        </m.tr>
                      ))}
                    </AnimatePresence>
                  </tbody>
                </table>
              </div>
            </Card.Content>
          </Card>
        </m.div>
      </Show>

      {/* Error display */}
      <Show when={error !== undefined}>
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
          className="rounded-xl bg-surface-negative-soft/20 border border-border-negative/30 p-4"
        >
          <HStack className="gap-3 items-start">
            <Icon name="alert" label="Error" className="w-5 h-5 text-content-negative-soft shrink-0" />
            <Text level="body-small" className="text-content-negative-soft">
              {error}
            </Text>
          </HStack>
        </m.div>
      </Show>

      {/* Actions */}
      <Show when={state !== "running"}>
        <m.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3, delay: 0.3 }}
        >
          <HStack className="gap-3">
            <Button variant="primary" modifier="ghost" label="Back to Configure" icon="arrow-left" iconPosition="left" onClick={handleBack} />
            <Button
              variant="rising"
              modifier="solid"
              label="Run Again"
              icon="play"
              iconPosition="left"
              onClick={handleRunAgain}
              disabled={passwordLock.isLocked}
            />
          </HStack>
        </m.div>
      </Show>
    </div>
  );
}

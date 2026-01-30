import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@rs/ui-new/button";
import { Spinner } from "@rs/ui-new/spinner";
import { Tag } from "@rs/ui-new/tag";
import { Text } from "@rs/ui-new/text";
import { SQLInput } from "./SQLInput";
import { SQLDisplay } from "./SQLDisplay";
import { fetchReadysetStatus, ReadysetContainerStatus } from "../lib/api";
import { useReadyset } from "../lib/useReadyset";

interface ReadysetPanelProps {
  target?: string | null;
  disabled?: boolean;
}

function ContainerStatusCard({
  status,
  isLoading,
}: {
  status?: ReadysetContainerStatus;
  isLoading: boolean;
}) {
  if (isLoading) {
    return (
      <div className="bg-surface-layout-1 rounded-xl p-6 border border-border-layout-1">
        <div className="flex items-center gap-3">
          <Spinner size="base" />
          <Text as="span" level="body-medium" className="text-content-layout-2">Checking container status...</Text>
        </div>
      </div>
    );
  }

  if (!status) {
    return (
      <div className="bg-surface-warning-soft rounded-xl p-6 border border-border-warning-soft">
        <Text level="body-medium" className="text-content-warning-soft">Unable to check container status</Text>
      </div>
    );
  }

  const allRunning = status.test_db_running && status.readyset_running;

   return (
     <div className="bg-surface-layout-1 rounded-xl p-6 border border-border-layout-1">
       <div className="flex justify-between items-start mb-4">
         <Text as="h3" level="subtitle-1" className="text-content-layout-1">Docker Containers</Text>
         <Tag
           variant={allRunning ? "positive" : "warning"}
           label={allRunning ? "Running" : "Not Running"}
         />
       </div>
      <div className="grid grid-cols-2 gap-4">
         <div className="bg-surface-layout-2 rounded-lg p-4">
           <div className="flex items-center gap-2 mb-2">
             <div
               className={`w-2 h-2 rounded-full ${status.test_db_running ? "bg-surface-positive-solid" : "bg-surface-layout-3"}`}
             />
             <Text as="span" level="label-small" className="text-content-layout-2">Test Database</Text>
           </div>
           {status.test_db_running && status.test_db_port && (
             <Text level="mono-small" className="text-content-layout-3">Port: {status.test_db_port}</Text>
           )}
         </div>
         <div className="bg-surface-layout-2 rounded-lg p-4">
           <div className="flex items-center gap-2 mb-2">
             <div
               className={`w-2 h-2 rounded-full ${status.readyset_running ? "bg-surface-positive-solid" : "bg-surface-layout-3"}`}
             />
             <Text as="span" level="label-small" className="text-content-layout-2">Readyset</Text>
           </div>
           {status.readyset_running && status.readyset_port && (
             <Text level="mono-small" className="text-content-layout-3">Port: {status.readyset_port}</Text>
           )}
         </div>
      </div>
    </div>
  );
}

function ExplainResultCard({
  result,
}: {
  result: { cacheable: boolean; confidence: string; explanation: string; issues: string[] };
}) {
  const isCacheable = result.cacheable;

  return (
    <div
      className={`rounded-xl p-6 border ${isCacheable ? "bg-surface-positive-soft border-border-positive-soft" : "bg-surface-negative-soft border-border-negative-soft"}`}
    >
      <div className="flex justify-between items-center mb-4">
        <div className="flex gap-2 items-center">
          <span
            className={`text-2xl ${isCacheable ? "text-content-positive-soft" : "text-content-negative-soft"}`}
          >
            {isCacheable ? "✓" : "✗"}
          </span>
          <span
            className={`font-bold text-lg ${isCacheable ? "text-content-positive-soft" : "text-content-negative-soft"}`}
          >
            {isCacheable ? "CACHEABLE" : "NOT CACHEABLE"}
          </span>
        </div>
        <Tag
          variant={isCacheable ? "positive" : "negative"}
          label={`${result.confidence.toUpperCase()} confidence`}
        />
      </div>
       {result.explanation && <Text level="body-small" className="text-content-layout-2">{result.explanation}</Text>}
       {result.issues && result.issues.length > 0 && (
         <div className="mt-4 space-y-1">
           <Text level="label-small" className="text-content-layout-3">Issues:</Text>
           {result.issues.map((issue, i) => (
             <Text key={i} level="body-small" className="text-content-layout-2">
               • {issue}
             </Text>
           ))}
         </div>
       )}
    </div>
  );
}

function CacheResultCard({
  result,
}: {
  result: { cached: boolean; cache_id: string | null; message: string; error?: string };
}) {
  const success = result.cached;

  return (
    <div
      className={`rounded-xl p-6 border ${success ? "bg-surface-positive-soft border-border-positive-soft" : "bg-surface-negative-soft border-border-negative-soft"}`}
    >
      <div className="flex gap-2 items-center mb-3">
        <span
          className={`text-2xl ${success ? "text-content-positive-soft" : "text-content-negative-soft"}`}
        >
          {success ? "✓" : "✗"}
        </span>
        <span
          className={`font-bold text-lg ${success ? "text-content-positive-soft" : "text-content-negative-soft"}`}
        >
          {success ? "Cache Created" : "Cache Failed"}
        </span>
      </div>
       {result.cache_id && (
         <Text level="body-small" className="text-content-layout-2 mb-2">
           Cache ID: <Text as="span" level="mono-small">{result.cache_id}</Text>
         </Text>
       )}
       <Text level="body-small" className="text-content-layout-2">{result.message || result.error}</Text>
    </div>
  );
}

export function ReadysetPanel({ target, disabled = false }: ReadysetPanelProps) {
  const [query, setQuery] = useState("");
  const [lastTestedQuery, setLastTestedQuery] = useState<string | null>(null);

  const {
    data: containerStatus,
    isLoading: isLoadingStatus,
    refetch: refetchStatus,
  } = useQuery({
    queryKey: ["readyset-status", target],
    queryFn: () => fetchReadysetStatus(target || undefined),
    staleTime: 30 * 1000,
    refetchInterval: 60 * 1000,
    enabled: !disabled,
  });

  const {
    setupContainers,
    explainQuery,
    createCache,
    cancel,
    state,
    progress,
    setupResult,
    explainResult,
    cacheResult,
    error,
    reset,
  } = useReadyset();

  useEffect(() => {
    if (setupResult?.success) {
      refetchStatus();
    }
  }, [setupResult, refetchStatus]);

  const handleSetup = async () => {
    if (disabled) return;
    reset();
    await setupContainers(target || undefined);
  };

  const handleExplain = async () => {
    if (disabled) return;
    if (!query.trim()) return;
    reset();
    setLastTestedQuery(query.trim());
    await explainQuery(query.trim(), target || undefined);
  };

  const handleCreateCache = async () => {
    if (disabled) return;
    const q = lastTestedQuery || query.trim();
    if (!q) return;
    reset();
    await createCache(q, target || undefined);
  };

  const isRunning = state === "running";
  const containersReady = containerStatus?.test_db_running && containerStatus?.readyset_running;

  return (
    <div className="space-y-6">
      <ContainerStatusCard status={containerStatus} isLoading={isLoadingStatus && !disabled} />

       {!containersReady && (
         <div className="bg-surface-layout-1 rounded-xl p-6 border border-border-layout-1">
           <Text level="body-medium" className="text-content-layout-2 mb-4">
             Start Docker containers to test query cacheability with Readyset.
           </Text>
          <Button
            onClick={handleSetup}
            disabled={disabled || isRunning}
            loading={isRunning && !explainResult && !cacheResult}
            variant="primary"
            modifier="solid"
            label="Start Containers"
          />
           {isRunning && progress && (
             <div className="mt-4 flex items-center gap-3">
               <Spinner size="base" />
               <Text as="span" level="body-small" className="text-content-layout-2">{progress.message}</Text>
             </div>
           )}
        </div>
      )}

       {containersReady && (
         <div className="bg-surface-layout-1 rounded-xl p-6 border border-border-layout-1">
           <Text as="h3" level="subtitle-1" className="text-content-layout-1 mb-4">
             Test Query Cacheability
           </Text>
          <div className="space-y-4">
            <SQLInput
              value={query}
              onChange={setQuery}
              onSubmit={handleExplain}
              target={target}
              disabled={disabled}
              placeholder="Enter a SQL query to test if it's cacheable..."
              minHeight="8rem"
            />
            <div className="flex gap-3">
              <Button
                onClick={handleExplain}
                disabled={disabled || isRunning || !query.trim()}
                loading={isRunning && !explainResult}
                variant="primary"
                modifier="solid"
                label="Check Cacheability"
              />
              {explainResult?.cacheable && (
                <Button
                  onClick={handleCreateCache}
                  disabled={disabled || isRunning}
                  loading={isRunning && !!explainResult}
                  variant="primary"
                  modifier="ghost"
                  label="Create Cache"
                />
              )}
              {isRunning && (
                <Button
                  onClick={cancel}
                  variant="negative"
                  modifier="ghost"
                  label="Cancel"
                  disabled={disabled}
                />
              )}
            </div>
          </div>
        </div>
      )}

       {isRunning && progress && (
         <div className="bg-surface-layout-1 rounded-xl p-4 border border-border-layout-1">
           <div className="flex items-center gap-3">
             <Spinner size="base" />
             <Text as="span" level="body-medium" className="text-content-layout-2">{progress.message}</Text>
           </div>
          {progress.percent > 0 && (
            <div className="mt-3">
              <div className="w-full h-1 bg-surface-layout-2 rounded-full overflow-hidden">
                <div
                  className="h-full bg-surface-info-solid transition-all duration-300"
                  style={{ width: `${progress.percent}%` }}
                />
              </div>
            </div>
          )}
        </div>
      )}

       {error && (
         <div className="bg-surface-negative-soft border border-border-negative-soft rounded-xl p-6">
           <Text level="label-small" className="text-content-negative-soft mb-2">Error</Text>
           <Text level="body-medium" className="text-content-layout-1">{error}</Text>
         </div>
       )}

       {explainResult && lastTestedQuery && (
         <div className="space-y-4">
           <div className="bg-surface-layout-2 rounded-lg p-3">
             <Text level="caption" className="text-content-layout-3 mb-1">Tested Query</Text>
             <SQLDisplay sql={lastTestedQuery} />
           </div>
           <ExplainResultCard result={explainResult} />
         </div>
       )}

      {cacheResult && <CacheResultCard result={cacheResult} />}

       {setupResult && !explainResult && !cacheResult && (
         <div className="bg-surface-positive-soft border border-border-positive-soft rounded-xl p-6">
           <Text level="label-small" className="text-content-positive-soft mb-2">
             {setupResult.already_running ? "Containers Already Running" : "Containers Started"}
           </Text>
           <Text level="body-small" className="text-content-layout-2">
             Readyset is ready on port {setupResult.readyset_port}. Enter a query above to test
             cacheability.
           </Text>
         </div>
       )}
    </div>
  );
}

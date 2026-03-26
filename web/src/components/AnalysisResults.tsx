import { Spinner } from "@rs/ui-new/spinner";
import { Text } from "@rs/ui-new/text";
import { Icon } from "@rs/ui-new/icon";
import { HStack, VStack } from "@rs/ui-new/stack";
import { m } from "@rs/ui-new/motion";
import type {
  AnalysisState,
  ProgressEvent,
  CompleteEvent,
  RewriteTesting,
  ReadysetCacheability,
} from "../lib/api";
import {
  type ValidIconName,
  resolveRewriteTesting,
  PerformanceSummarySection,
  TestedOptimizationsSection,
  IndexRecommendationsSection,
  AdditionalRecommendationsSection,
  ReadysetCacheabilitySection,
} from "./analysis/AnalysisSections";

interface AnalysisResultsProps {
  state: AnalysisState;
  progress?: ProgressEvent;
  results?: CompleteEvent;
  rewriteTesting?: RewriteTesting;
  readysetCacheability?: ReadysetCacheability;
  error?: string;
  target?: string;
  cacheDeployed?: boolean;
  onCacheQuery?: () => void;
  onDeployNavigate?: () => void;
  isCaching?: boolean;
}

// ---------------------------------------------------------------------------
// AnalysisHeader — metadata bar (target, engine, analysis ID, LLM info)
// Specific to the /results page, not shared.
// ---------------------------------------------------------------------------

function AnalysisHeader({
  results,
  target: targetProp,
}: {
  results: CompleteEvent;
  target?: string;
}) {
  const formatted = results.formatted;
  const metadata = formatted?.metadata;
  const tokenUsage = results.llm_analysis?.token_usage;
  const llmInfo = metadata?.llm_info;

  const target = metadata?.target || targetProp;
  const databaseEngine =
    metadata?.database_engine || results.explain_results?.database_engine;
  const analysisId = metadata?.analysis_id || results.analysis_id;

  if (!target && !databaseEngine && !analysisId) return null;

  const model = llmInfo?.model || "claude";
  const tokens = llmInfo?.tokens || tokenUsage?.total || 0;
  const cost = llmInfo?.cost || tokenUsage?.estimated_cost_usd || 0;

  return (
    <m.div
      className="bg-gradient-to-r from-surface-layout-1 to-surface-layout-2 rounded-xl p-4 border border-border-layout-1"
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <HStack className="justify-between items-center flex-wrap gap-3">
        <HStack className="gap-6 flex-wrap">
          {target && (
            <HStack className="gap-2 items-center">
              <Icon
                name="database"
                label="Target"
                className="w-4 h-4 text-content-layout-3"
              />
              <Text
                as="span"
                level="mono-small"
                className="text-content-layout-1"
              >
                {target}
              </Text>
            </HStack>
          )}
          {databaseEngine && (
            <HStack className="gap-2 items-center">
              <Icon
                name="database-settings"
                label="Engine"
                className="w-4 h-4 text-content-layout-3"
              />
              <Text
                as="span"
                level="mono-small"
                className="text-content-layout-1 uppercase"
              >
                {databaseEngine}
              </Text>
            </HStack>
          )}
          {analysisId && (
            <HStack className="gap-2 items-center">
              <Icon
                name="key"
                label="Analysis ID"
                className="w-4 h-4 text-content-layout-3"
              />
              <Text
                as="span"
                level="mono-small"
                className="text-content-layout-2"
              >
                {analysisId.slice(0, 12)}
              </Text>
            </HStack>
          )}
        </HStack>
        {(llmInfo || tokenUsage) && (
          <HStack className="gap-2 items-center px-3 py-1.5 bg-surface-layout-1/50 rounded-lg">
            <Icon
              name="sparkles"
              label="AI Analysis"
              className="w-3.5 h-3.5 text-content-primary-soft"
            />
            <Text
              as="span"
              level="caption"
              className="text-content-layout-3"
            >
              {model} · {tokens.toLocaleString()} tokens · ${cost.toFixed(3)}
            </Text>
          </HStack>
        )}
      </HStack>
    </m.div>
  );
}

// ---------------------------------------------------------------------------
// AnalysisResults — state machine component for /results page
// ---------------------------------------------------------------------------

export function AnalysisResults({
  state,
  progress,
  results,
  rewriteTesting,
  readysetCacheability,
  error,
  target,
  cacheDeployed,
  onCacheQuery,
  onDeployNavigate,
  isCaching,
}: AnalysisResultsProps) {
  if (state === "idle") {
    return (
      <m.div
        className="py-20 flex flex-col items-center justify-center"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.4 }}
      >
        <div className="w-16 h-16 rounded-2xl bg-surface-layout-1 border border-border-layout-1 flex items-center justify-center mb-6">
          <Icon
            name="querypilot"
            label="Ready to Analyze"
            className="w-8 h-8 text-content-layout-3"
          />
        </div>
        <Text level="headline-4" className="text-content-layout-2 mb-2">
          Ready to Analyze
        </Text>
        <Text
          level="body-small"
          className="text-content-layout-3 text-center max-w-md"
        >
          Enter a SQL query above and click Analyze to get performance insights
          and optimization recommendations
        </Text>
      </m.div>
    );
  }

  if (state === "analyzing") {
    const stages: {
      id: string;
      label: string;
      description: string;
      icon: ValidIconName;
    }[] = [
      {
        id: "normalizing",
        label: "Preparing",
        description: "Parsing and normalizing SQL",
        icon: "querypilot",
      },
      {
        id: "executing_explain",
        label: "Executing",
        description: "Running EXPLAIN ANALYZE",
        icon: "play",
      },
      {
        id: "analyzing_llm",
        label: "Analyzing",
        description: "AI-powered optimization",
        icon: "sparkles",
      },
    ];

    // Map all backend stage IDs to frontend stage indices
    const stageMapping: Record<string, number> = {
      loading_config: 0,
      validating: 0,
      normalizing: 0,
      executing_explain: 1,
      collecting_metrics: 1,
      collecting_schema: 1,
      analyzing_llm: 2,
      testing_rewrites: 2,
      checking_readyset: 2,
      storing_results: 2,
      complete: 2,
    };

    const currentStageId = progress?.stage || "normalizing";
    const currentStageIndex = stageMapping[currentStageId] ?? 0;
    const currentStage = stages[currentStageIndex];
    const percent = progress?.percent || 5;

    return (
      <m.div
        className="py-16"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.3 }}
      >
        <div className="max-w-lg mx-auto">
          {/* Stage indicators */}
          <div className="flex items-center justify-center mb-10">
            {stages.map((stage, index) => {
              const isComplete = index < currentStageIndex;
              const isCurrent = index === currentStageIndex;
              const isPending = index > currentStageIndex;

              return (
                <div key={stage.id} className="flex items-center">
                  <m.div
                    className={`
                      relative w-12 h-12 rounded-xl flex items-center justify-center transition-all
                      ${isComplete ? "bg-surface-positive-soft" : ""}
                      ${isCurrent ? "bg-surface-primary-soft ring-2 ring-border-primary-soft" : ""}
                      ${isPending ? "bg-surface-layout-1 border border-border-layout-1" : ""}
                    `}
                    initial={false}
                    animate={
                      isCurrent ? { scale: [1, 1.05, 1] } : { scale: 1 }
                    }
                    transition={{
                      duration: 1.5,
                      repeat: isCurrent
                        ? Number.POSITIVE_INFINITY
                        : 0,
                      ease: "easeInOut",
                    }}
                  >
                    {isComplete ? (
                      <Icon
                        name="tick-double"
                        label="Complete"
                        className="w-5 h-5 text-content-positive-soft"
                      />
                    ) : isCurrent ? (
                      <Spinner size="base" />
                    ) : (
                      <Icon
                        name={stage.icon}
                        label={stage.label}
                        className="w-5 h-5 text-content-layout-3"
                      />
                    )}
                  </m.div>
                  {index < stages.length - 1 && (
                    <div className="w-12 mx-1.5 h-0.5 rounded-full overflow-hidden bg-surface-layout-1">
                      <m.div
                        className="h-full bg-content-positive-soft"
                        initial={{ width: "0%" }}
                        animate={{
                          width:
                            index < currentStageIndex ? "100%" : "0%",
                        }}
                        transition={{ duration: 0.5 }}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Current stage info */}
          <div className="text-center mb-8">
            <m.div
              key={currentStage.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3 }}
            >
              <Text
                level="headline-3"
                className="text-content-layout-1 mb-2"
              >
                {currentStage.label}
              </Text>
              <Text
                level="body-small"
                className="text-content-layout-3"
              >
                {currentStage.description}
              </Text>
            </m.div>
          </div>

          {/* Progress bar */}
          <div className="bg-surface-layout-1 rounded-xl p-5 border border-border-layout-1">
            <HStack className="justify-between items-center mb-3">
              <Text
                level="overline"
                className="text-content-layout-3 uppercase tracking-wider"
              >
                Progress
              </Text>
              <Text
                level="mono-small"
                className="text-content-primary-soft font-semibold"
              >
                {percent}%
              </Text>
            </HStack>
            <div className="w-full h-2 bg-surface-layout-2 rounded-full overflow-hidden">
              <m.div
                className="h-full bg-gradient-to-r from-content-primary-soft to-content-rising-plain rounded-full"
                initial={{ width: "0%" }}
                animate={{ width: `${percent}%` }}
                transition={{ duration: 0.5, ease: "easeOut" }}
              />
            </div>
          </div>
        </div>
      </m.div>
    );
  }

  if (state === "error") {
    return (
      <m.div
        className="bg-surface-negative-soft/50 border border-border-negative-soft rounded-xl p-6"
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="gap-4 items-start">
          <div className="w-12 h-12 rounded-xl bg-surface-negative-soft flex items-center justify-center shrink-0">
            <Icon
              name="alert"
              label="Error"
              className="w-6 h-6 text-content-negative-soft"
            />
          </div>
          <VStack className="gap-2 items-start flex-1">
            <Text level="headline-4" className="text-content-negative-soft">
              Analysis Failed
            </Text>
            <Text
              level="body-small"
              className="text-content-layout-2 leading-relaxed"
            >
              {error ||
                "An unknown error occurred while analyzing the query. Please try again."}
            </Text>
          </VStack>
        </HStack>
      </m.div>
    );
  }

  if (state === "complete" && results) {
    const { llm_analysis, explain_results, formatted } = results;
    const perf =
      llm_analysis?.performance_assessment || formatted?.analysis_summary;
    const testing = resolveRewriteTesting(
      rewriteTesting,
      results.rewrite_testing,
      formatted?.rewrite_testing
    );
    const cacheability =
      readysetCacheability ||
      results.readyset_cacheability ||
      formatted?.readyset_cacheability;
    const hasLLMAnalysis =
      llm_analysis?.success !== false &&
      (perf ||
        llm_analysis?.rewrite_suggestions?.length ||
        llm_analysis?.index_recommendations?.length);

    return (
      <m.div
        className="space-y-8 w-full"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.4 }}
      >
        <AnalysisHeader results={results} target={target} />

        {/* Performance Summary - Hero Section */}
        {perf && (
          <PerformanceSummarySection
            perf={perf}
            explainResults={explain_results}
          />
        )}

        {testing && <TestedOptimizationsSection testing={testing} />}

        {llm_analysis?.index_recommendations &&
          llm_analysis.index_recommendations.length > 0 && (
            <IndexRecommendationsSection
              recommendations={llm_analysis.index_recommendations}
            />
          )}

        {llm_analysis?.optimization_opportunities &&
          llm_analysis.optimization_opportunities.length > 0 && (
            <AdditionalRecommendationsSection
              opportunities={llm_analysis.optimization_opportunities}
            />
          )}

        {cacheability && (
          <ReadysetCacheabilitySection
            cacheability={cacheability}
            cacheDeployed={cacheDeployed}
            onCacheQuery={onCacheQuery}
            onDeployNavigate={onDeployNavigate}
            isCaching={isCaching}
          />
        )}

        {!hasLLMAnalysis && (
          <m.div
            className="bg-surface-warning-soft/50 border border-border-warning-soft rounded-xl p-5"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.2 }}
          >
            <HStack className="gap-3 items-start">
              <div className="w-8 h-8 rounded-lg bg-surface-warning-soft flex items-center justify-center shrink-0">
                <Icon
                  name="info"
                  label="Limited"
                  className="w-4 h-4 text-content-warning-soft"
                />
              </div>
              <VStack className="gap-1 items-start">
                <Text
                  level="label-medium"
                  className="text-content-warning-soft"
                >
                  Limited Analysis
                </Text>
                <Text
                  level="body-small"
                  className="text-content-layout-2"
                >
                  AI analysis was not available. Showing execution plan data
                  only.
                </Text>
              </VStack>
            </HStack>
          </m.div>
        )}
      </m.div>
    );
  }

  return null;
}

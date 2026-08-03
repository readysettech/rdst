import { useId } from "react";
import { Spinner } from "@rs/ui-new/spinner";
import { Text } from "@rs/ui-new/text";
import { Icon } from "@rs/ui-new/icon";
import { HStack, VStack } from "@rs/ui-new/stack";
import { ErrorState } from "@rs/ui-new/error-state";
import { useDisclosure } from "@rs/ui-new/use-disclosure";
import { m } from "@rs/ui-new/motion";
import type {
  AnalysisState,
  ProgressEvent,
  CompleteEvent,
  RewriteTesting,
  ReadysetCacheability,
} from "../lib/api";
import {
  type ApiErrorEnvelope,
  classifyError,
  isConnectionFailure,
  isTrialExhaustedError,
  recoveryFor,
  retryHelps,
  TRIAL_EXHAUSTED_MESSAGE,
} from "../lib/errorContract";
import { RoutableNotice } from "./RoutableNotice";
import { ConnectionFailureActions } from "./ConnectionFailureActions";
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
  errorEnvelope?: ApiErrorEnvelope;
  target?: string;
  cacheDeployed?: boolean;
  onCacheQuery?: () => void;
  onDeployNavigate?: () => void;
  /** Hand the query to the gated cache flow ("Set up caching…"). Preferred over
   *  the inline onCacheQuery/onDeployNavigate when present. */
  onSetUpCaching?: () => void;
  /** Navigate to a recovery destination (e.g. back to `/analyze`). */
  onRecover?: (to: string) => void;
  /** Re-run the analysis; only surfaced when a retry can plausibly help. */
  onRetry?: () => void;
  onStartTrial?: () => void;
  isCaching?: boolean;
  /** Open the interactive chat drawer — surfaced as a quiet footer link. */
  onAskFollowUp?: () => void;
  /** Whether a prior conversation exists (changes the footer link label). */
  hasExistingChat?: boolean;
}

// ---------------------------------------------------------------------------
// AnalysisFooter — quiet tertiary footer: cost/model metadata, the raw EXPLAIN
// plan (behind a disclosure), and the "Ask a follow-up" chat entry point.
// Demoted out of the top so nothing competes with the verdict. [VIS-011, USE-065]
// Specific to the /results page, not shared.
// ---------------------------------------------------------------------------

function AnalysisFooter({
  results,
  target: targetProp,
  onAskFollowUp,
  hasExistingChat,
}: {
  results: CompleteEvent;
  target?: string;
  onAskFollowUp?: () => void;
  hasExistingChat?: boolean;
}) {
  const [planOpen, setPlanOpen] = useDisclosure({});
  const planId = useId();

  const formatted = results.formatted;
  const metadata = formatted?.metadata;
  const tokenUsage = results.llm_analysis?.token_usage;
  const llmInfo = metadata?.llm_info;

  const target = metadata?.target || targetProp;
  const databaseEngine =
    metadata?.database_engine || results.explain_results?.database_engine;

  const model = llmInfo?.model || "claude";
  const tokens = llmInfo?.tokens || tokenUsage?.total || 0;
  const cost = llmInfo?.cost || tokenUsage?.estimated_cost_usd || 0;
  const hasCost = Boolean(llmInfo || tokenUsage);

  // Preserve the model/token/cost transparency the old header carried, plus
  // target/engine — just demoted to one quiet caption line. [USE-065]
  const metaSegments = [
    target,
    databaseEngine ? databaseEngine.toUpperCase() : null,
    hasCost ? model : null,
    hasCost ? `${tokens.toLocaleString()} tokens` : null,
    hasCost ? `$${cost.toFixed(3)}` : null,
  ].filter(Boolean) as string[];

  const explainPlan = results.explain_results?.explain_plan;
  const hasPlan =
    explainPlan != null && Object.keys(explainPlan).length > 0;
  const planText = hasPlan ? JSON.stringify(explainPlan, null, 2) : "";

  const canAsk = Boolean(results.query_hash && onAskFollowUp);

  if (metaSegments.length === 0 && !hasPlan && !canAsk) return null;

  return (
    <m.div
      className="pt-5 border-t border-border-layout-1"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3, delay: 0.2 }}
    >
      <HStack className="justify-between items-center flex-wrap gap-3">
        {metaSegments.length > 0 && (
          <Text as="span" level="caption" className="text-content-layout-3">
            {metaSegments.join(" · ")}
          </Text>
        )}
        <HStack className="gap-4 items-center flex-wrap">
          {hasPlan && (
            <button
              type="button"
              aria-expanded={planOpen}
              aria-controls={planId}
              onClick={() => setPlanOpen(!planOpen)}
              className="flex items-center gap-1.5 text-content-layout-3 hover:text-content-layout-2 transition-colors text-label-small"
            >
              <Icon
                name="querypilot"
                label=""
                className="w-3.5 h-3.5"
              />
              View EXPLAIN plan
              <Icon
                name="chevron-down"
                label=""
                className={`w-3.5 h-3.5 transition-transform ${planOpen ? "rotate-180" : ""}`}
              />
            </button>
          )}
          {canAsk && (
            <button
              type="button"
              onClick={onAskFollowUp}
              className="flex items-center gap-1.5 text-content-primary-soft hover:text-content-primary-solid transition-colors text-label-small"
            >
              <Icon
                name={hasExistingChat ? "message-multiple" : "sparkles"}
                label=""
                className="w-3.5 h-3.5"
              />
              {hasExistingChat ? "Continue conversation" : "Ask a follow-up"}
            </button>
          )}
        </HStack>
      </HStack>
      {hasPlan && planOpen && (
        <pre
          id={planId}
          className="mt-3 whitespace-pre-wrap break-words rounded-lg border border-border-layout-1 bg-surface-layout-2/60 p-3 text-content-layout-3 text-mono-small max-h-96 overflow-auto"
        >
          {planText}
        </pre>
      )}
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
  errorEnvelope,
  target,
  cacheDeployed,
  onCacheQuery,
  onDeployNavigate,
  onSetUpCaching,
  onRecover,
  onRetry,
  onStartTrial,
  isCaching,
  onAskFollowUp,
  hasExistingChat,
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
          {/* Stage indicators — every stage carries a caption (Preparing →
              Executing → Analyzing) with the active one emphasized, so users
              can preview the steps and see how many remain. [USE-008, USE-022] */}
          <div className="flex items-start justify-center mb-10">
            {stages.map((stage, index) => {
              const isComplete = index < currentStageIndex;
              const isCurrent = index === currentStageIndex;
              const isPending = index > currentStageIndex;

              return (
                <div key={stage.id} className="flex items-start">
                  <VStack className="gap-2.5 items-center">
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
                    <Text
                      level="caption"
                      className={
                        isCurrent
                          ? "text-content-layout-1 font-medium"
                          : isComplete
                            ? "text-content-positive-soft"
                            : "text-content-layout-3"
                      }
                    >
                      {stage.label}
                    </Text>
                  </VStack>
                  {index < stages.length - 1 && (
                    <div className="w-12 mx-1.5 mt-6 h-0.5 rounded-full overflow-hidden bg-surface-layout-1">
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
    // Route the failure through the shared error contract: friendly cause, a
    // recovery action to the right screen, retry only when it can help, and the
    // raw driver text tucked behind an expander (retires the P41 leak).
    const envelope: ApiErrorEnvelope = errorEnvelope ?? {
      code: "error",
      message:
        error ||
        "An unknown error occurred while analyzing the query. Please try again.",
    };
    const errorClass = classifyError(envelope);
    const trialExhausted = isTrialExhaustedError(envelope)
    const isInvalidSql = envelope.code === "invalid_sql";
    const title = isInvalidSql ? "Analysis Failed" : "Analysis could not complete";

    let action: { label: string; onClick: () => void } | undefined;
    if (isInvalidSql && onRecover) {
      action = { label: "Edit query", onClick: () => onRecover("/analyze") };
    } else {
      const rec = recoveryFor(errorClass);
      if (rec && onRecover) {
        action = { label: rec.label, onClick: () => onRecover(rec.to) };
      }
    }

    if (trialExhausted) {
      return (
        <RoutableNotice
          kind="trial-exhausted"
          message={TRIAL_EXHAUSTED_MESSAGE}
          primaryActionLabel="Set key"
          onRetry={onStartTrial}
          retryLabel={onStartTrial ? "Start trial" : undefined}
        />
      )
    }

    if (target && isConnectionFailure(envelope)) {
      return (
        <m.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.3 }}
          className="rounded-xl border border-border-negative-soft bg-surface-negative-soft/20 p-5"
        >
          <ConnectionFailureActions
            failure={{
              target: envelope.target || target,
              message: envelope.message,
              category: envelope.category,
              code: envelope.code,
            }}
            onRetry={
              onRetry
                ? async () => {
                    onRetry();
                    return true;
                  }
                : undefined
            }
            featureRecovery
          />
        </m.div>
      );
    }

    return (
      <m.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.3 }}
      >
        <ErrorState
          errorClass={errorClass}
          title={title}
          message={envelope.message}
          action={action}
          onRetry={retryHelps(errorClass) && onRetry ? onRetry : undefined}
          detail={envelope.detail}
        />
      </m.div>
    );
  }

  if (state === "complete" && results) {
    const { llm_analysis, explain_results, formatted } = results;
    const perf =
      llm_analysis?.performance_assessment || formatted?.analysis_summary;
    // Suppress the Performance hero when there is no real assessment (score 0
    // AND rating unknown/absent) — a zero-score hero reads as a false verdict
    // (B3/T3). A genuine low-but-rated score still renders.
    const perfScore =
      typeof perf?.efficiency_score === "number" ? perf.efficiency_score : undefined;
    const perfRating = (perf as { overall_rating?: string } | undefined)
      ?.overall_rating;
    const perfIsUnknown =
      (!perfRating || perfRating.toLowerCase() === "unknown") &&
      (perfScore === undefined || perfScore <= 0);
    const showPerformanceHero = Boolean(perf) && !perfIsUnknown;
    const testing = resolveRewriteTesting(
      rewriteTesting,
      results.rewrite_testing ?? undefined,
      formatted?.rewrite_testing ?? undefined
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
        {/* Performance Summary - Hero Section */}
        {showPerformanceHero && perf && (
          <PerformanceSummarySection
            perf={perf}
            explainResults={explain_results ?? undefined}
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
              collapsible
            />
          )}

        {cacheability && (
          <ReadysetCacheabilitySection
            cacheability={cacheability}
            cacheDeployed={cacheDeployed}
            onCacheQuery={onCacheQuery}
            onDeployNavigate={onDeployNavigate}
            onSetUpCaching={onSetUpCaching}
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

        <AnalysisFooter
          results={results}
          target={target}
          onAskFollowUp={onAskFollowUp}
          hasExistingChat={hasExistingChat}
        />
      </m.div>
    );
  }

  return null;
}

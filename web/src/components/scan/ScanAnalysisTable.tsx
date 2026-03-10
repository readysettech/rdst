/**
 * Analysis results table — shown when --analyze is used
 * SQL pill + modal detail pattern (matches ScanResultsTable UX)
 *
 * Mirrors CLI `rdst scan --analyze` output:
 *   - Summary panel: mode, counts, worst score, CI status + thresholds
 *   - Per-query detail: rating, score, exec time, issues, recommendations,
 *     rewrite benchmarks, hash
 *   - Failed queries section
 */

import { useState, useMemo, useCallback } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { Button } from '@rs/ui-new/button';
import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { Tag } from '@rs/ui-new/tag';
import { HStack } from '@rs/ui-new/stack';
import { Card } from '@rs/ui-new/card';
import { Show } from '@rs/ui-new/show';
import { m, AnimatePresence } from '@rs/ui-new/motion';
import { Modal, ModalContent, ModalContentContainer } from '@rs/ui-new/modal';
import { SQLDisplay } from '../SQLDisplay';
import { useFormatSql } from '../../lib/useFormatSql';
import { collapseWhitespace } from '../../lib/collapseWhitespace';
import type { ScanAnalysisSummary, ScanAnalyzedQuery, ScanRawAnalysis } from '../../types/scan';
import {
  resolveRewriteTesting,
  getRatingVariant,
  getScoreVariant,
  variantStyles,
  PerformanceSummarySection,
  TestedOptimizationsSection,
  IndexRecommendationsSection,
  AdditionalRecommendationsSection,
  ReadysetCacheabilitySection,
} from '../analysis/AnalysisSections';

interface ScanAnalysisTableProps {
  analysis: ScanAnalysisSummary;
  scanTarget: string | null;
}

function getScoreColor(score: number | null): string {
  if (score === null) return 'text-content-layout-3';
  return variantStyles[getScoreVariant(score)].text;
}

function getCiStatusVariant(
  status: string
): 'positive' | 'warning' | 'negative' {
  if (status === 'pass') return 'positive';
  if (status === 'warn') return 'warning';
  return 'negative';
}

function getRichAnalysis(raw: ScanRawAnalysis | undefined) {
  const llmAnalysis = raw?.llm_analysis;
  const nestedAnalysis = llmAnalysis?.analysis_results;
  const explainResults = raw?.explain_results;
  const perf =
    llmAnalysis?.performance_assessment ??
    nestedAnalysis?.performance_assessment ??
    raw?.formatted?.analysis_summary;
  const rewriteTesting = resolveRewriteTesting(
    raw?.rewrite_test_results,
    raw?.rewrite_testing,
    nestedAnalysis?.rewrite_testing,
    raw?.formatted?.rewrite_testing
  );
  const cacheability =
    raw?.readyset_cacheability ??
    nestedAnalysis?.readyset_cacheability ??
    raw?.formatted?.readyset_cacheability;
  const indexRecommendations =
    llmAnalysis?.index_recommendations ?? nestedAnalysis?.index_recommendations ?? [];
  const optimizationOpportunities =
    llmAnalysis?.optimization_opportunities ??
    nestedAnalysis?.optimization_opportunities ??
    [];
  const hasRenderableRichSections = Boolean(
    perf ||
    rewriteTesting ||
    indexRecommendations.length > 0 ||
    optimizationOpportunities.length > 0 ||
    cacheability?.checked
  );

  return {
    explainResults,
    perf,
    rewriteTesting,
    cacheability,
    indexRecommendations,
    optimizationOpportunities,
    hasRenderableRichSections,
  };
}

// ---------------------------------------------------------------------------
// Modal — detail view for a single analyzed query
// ---------------------------------------------------------------------------

interface AnalysisDetailModalProps {
  query: ScanAnalyzedQuery | null;
  onClose: () => void;
  onAnalyze: () => void;
}

function AnalysisDetailModal({ query, onClose, onAnalyze }: AnalysisDetailModalProps) {
  const formattedSql = useFormatSql(query?.sql ?? null);
  const displaySql = formattedSql ?? query?.sql ?? '';

  const {
    explainResults,
    perf,
    rewriteTesting,
    cacheability,
    indexRecommendations,
    optimizationOpportunities,
    hasRenderableRichSections,
  } = useMemo(() => getRichAnalysis(query?.raw_analysis), [query?.raw_analysis]);
  const showFallbackPerformance = query ? !perf && (query.execution_time_ms !== undefined || query.risk_score !== null) : false;
  const showFallbackIssues = query ? query.issues.length > 0 || !hasRenderableRichSections : false;
  const showFallbackRecommendations = query ? query.recommendations.length > 0 : false;
  const showFallbackBenchmarks = query ? !rewriteTesting && Boolean(query.rewrite_benchmarks?.length) : false;

  return (
    <Modal open={!!query} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={!!query}>
        <ModalContent size="large" className="p-0 gap-0">
          {query && (
            <>
              {/* Header */}
              <div className="flex items-center justify-between px-5 py-4 border-b border-border-layout-1 bg-surface-layout-1">
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  <div className="p-2 rounded-lg bg-surface-layout-2 shrink-0">
                    <Icon
                      name="speedometer"
                      label="Analysis"
                      size="base"
                      className="text-content-layout-2"
                    />
                  </div>
                  <div className="min-w-0">
                    <Text as="h2" level="headline-5" className="text-content-layout-1 truncate">
                      {query.function}()
                    </Text>
                    <HStack className="gap-2 items-center flex-wrap">
                      <Text level="caption" className="text-content-layout-3">
                        {query.file}:{query.line}
                      </Text>
                      {query.rating && (
                        <Tag
                          size="small"
                          variant={getRatingVariant(query.rating)}
                          modifier="ghost"
                          label={query.rating}
                        />
                      )}
                      {query.risk_score !== null && (
                        <Text level="caption" className={getScoreColor(query.risk_score)}>
                          Score: {query.risk_score}
                        </Text>
                      )}
                      <Text level="caption" className="text-content-layout-3">
                        {query.hash.slice(0, 8)}
                      </Text>
                    </HStack>
                  </div>
                </div>
              </div>

              {/* Content */}
              <div className="p-5 space-y-5 max-h-[60vh] overflow-auto">
                {/* SQL */}
                <div>
                  <Text
                    as="label"
                    level="label-small"
                    className="text-content-layout-3 uppercase tracking-wider block mb-2"
                  >
                    SQL
                  </Text>
                  <div className="bg-surface-layout-2 rounded-lg p-3 overflow-auto">
                    <SQLDisplay sql={displaySql} wrap showCopy />
                  </div>
                </div>

                {hasRenderableRichSections && (
                  <div className="space-y-6">
                    {perf && (
                      <PerformanceSummarySection
                        perf={perf}
                        explainResults={explainResults}
                      />
                    )}

                    {rewriteTesting && (
                      <TestedOptimizationsSection testing={rewriteTesting} />
                    )}

                    {indexRecommendations.length > 0 && (
                      <IndexRecommendationsSection
                        recommendations={indexRecommendations}
                      />
                    )}

                    {optimizationOpportunities.length > 0 && (
                      <AdditionalRecommendationsSection
                        opportunities={optimizationOpportunities}
                      />
                    )}

                    {cacheability && (
                      <ReadysetCacheabilitySection cacheability={cacheability} />
                    )}
                  </div>
                )}

                {(showFallbackPerformance ||
                  showFallbackIssues ||
                  showFallbackRecommendations ||
                  showFallbackBenchmarks) && (
                  <div className="space-y-6">
                    {showFallbackPerformance && (
                      <div>
                        <Text
                          as="label"
                          level="label-small"
                          className="text-content-layout-3 uppercase tracking-wider block mb-2"
                        >
                          Performance
                        </Text>
                        <HStack className="gap-4 items-center">
                          {query.risk_score !== null && (
                            <HStack className="gap-1.5 items-center">
                              <Text level="body-small" className="text-content-layout-2">
                                Risk Score:
                              </Text>
                              <Text level="headline-5" className={getScoreColor(query.risk_score)}>
                                {query.risk_score}
                              </Text>
                            </HStack>
                          )}
                          {query.execution_time_ms !== undefined && (
                            <HStack className="gap-1.5 items-center">
                              <Text level="body-small" className="text-content-layout-2">
                                Execution:
                              </Text>
                              <Text level="mono-small" className="text-content-layout-1">
                                {query.execution_time_ms.toFixed(1)}ms
                              </Text>
                            </HStack>
                          )}
                        </HStack>
                      </div>
                    )}

                    {query.issues.length > 0 ? (
                      <div>
                        <Text
                          as="label"
                          level="label-small"
                          className="text-content-layout-3 uppercase tracking-wider block mb-2"
                        >
                          Issues
                        </Text>
                        <div className="space-y-1.5">
                          {query.issues.map((issue, i) => (
                            <HStack key={i} className="gap-2 items-start">
                              <Icon
                                name="alert"
                                label="Issue"
                                className="w-3.5 h-3.5 text-content-warning-soft shrink-0 mt-0.5"
                              />
                              <Text level="body-small" className="text-content-layout-2">
                                {issue}
                              </Text>
                            </HStack>
                          ))}
                        </div>
                      </div>
                    ) : (
                      !hasRenderableRichSections && (
                        <Text level="body-small" className="text-content-positive-soft">
                          No issues found
                        </Text>
                      )
                    )}

                    {showFallbackRecommendations && (
                      <div>
                        <Text
                          as="label"
                          level="label-small"
                          className="text-content-layout-3 uppercase tracking-wider block mb-2"
                        >
                          Recommendations
                        </Text>
                        <div className="space-y-1.5">
                          {query.recommendations.map((rec, i) => (
                            <HStack key={i} className="gap-2 items-start">
                              <Icon
                                name="tick"
                                label="Recommendation"
                                className="w-3.5 h-3.5 text-content-positive-soft shrink-0 mt-0.5"
                              />
                              <Text level="body-small" className="text-content-layout-2">
                                {rec}
                              </Text>
                            </HStack>
                          ))}
                        </div>
                      </div>
                    )}

                    {showFallbackBenchmarks && (
                      <div>
                        <Text
                          as="label"
                          level="label-small"
                          className="text-content-layout-3 uppercase tracking-wider block mb-2"
                        >
                          Rewrite Benchmarks
                        </Text>
                        <div className="space-y-1.5">
                          {query.rewrite_benchmarks?.map((bench, i) => (
                            <HStack key={i} className="gap-2 items-start">
                              <Icon
                                name="speedometer"
                                label="Benchmark"
                                className="w-3.5 h-3.5 text-content-layout-3 shrink-0 mt-0.5"
                              />
                              <Text level="body-small" className="text-content-layout-2">
                                {bench}
                              </Text>
                            </HStack>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

              </div>

              {/* Footer */}
              <div className="flex justify-end gap-3 px-5 py-4 border-t border-border-layout-1 bg-surface-layout-1">
                <Button
                  variant="primary"
                  modifier="ghost"
                  label="Close"
                  onClick={onClose}
                />
                <Button
                  variant="primary"
                  modifier="solid"
                  label="Analyze Query"
                  icon="speedometer"
                  iconPosition="left"
                  onClick={onAnalyze}
                />
              </div>
            </>
          )}
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Row — compact analysis row with SQL pill
// ---------------------------------------------------------------------------

interface AnalysisRowProps {
  query: ScanAnalyzedQuery;
  idx: number;
  onViewDetail: () => void;
}

function AnalysisRow({ query, idx, onViewDetail }: AnalysisRowProps) {
  const collapsedSql = collapseWhitespace(query.sql);
  const sqlPreview =
    collapsedSql.length > 120 ? `${collapsedSql.slice(0, 120)}...` : collapsedSql;

  return (
    <m.div
      key={query.hash}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.15, delay: idx * 0.03 }}
      className="group px-5 py-3.5 hover:bg-surface-layout-2/30 transition-colors"
    >
      {/* Line 1: Identity + metadata + score */}
      <HStack className="justify-between items-center gap-3">
        <HStack className="gap-2.5 items-center min-w-0 flex-1 flex-wrap">
          <Text as="span" level="mono-small" className="text-content-layout-2 shrink-0">
            {query.file}:{query.line}
          </Text>
          <Text as="span" level="mono-small" className="text-content-layout-1 truncate">
            {query.function}()
          </Text>
          {query.rating && (
            <Tag
              size="small"
              variant={getRatingVariant(query.rating)}
              modifier="ghost"
              label={query.rating}
            />
          )}
          {query.issues.length > 0 && (
            <Tag
              size="small"
              variant="warning"
              modifier="ghost"
              label={`${query.issues.length} issue${query.issues.length === 1 ? '' : 's'}`}
            />
          )}
          <Text as="span" level="caption" className="text-content-layout-3">
            {query.hash.slice(0, 8)}
          </Text>
        </HStack>
        <HStack className="gap-2 items-center shrink-0">
          {query.execution_time_ms !== undefined && (
            <Text level="caption" className="text-content-layout-3">
              {query.execution_time_ms.toFixed(1)}ms
            </Text>
          )}
          <Text level="headline-4" className={getScoreColor(query.risk_score)}>
            {query.risk_score ?? '—'}
          </Text>
        </HStack>
      </HStack>

      {/* Line 2: SQL preview pill */}
      <div className="mt-1.5">
        <button
          type="button"
          onClick={onViewDetail}
          className="text-left bg-surface-layout-2 px-2.5 py-1.5 rounded-lg hover:bg-surface-primary-soft transition-colors cursor-pointer max-w-full overflow-hidden flex items-center gap-2"
          title="View analysis detail"
        >
          <Icon
            name="eye"
            label="View detail"
            className="w-3 h-3 text-content-layout-3 shrink-0"
          />
          <Text level="mono-small" className="text-content-layout-2 truncate">
            {sqlPreview}
          </Text>
        </button>
      </div>
    </m.div>
  );
}

// ---------------------------------------------------------------------------
// Failed query row — analysis error
// ---------------------------------------------------------------------------

interface FailedQueryRowProps {
  query: { hash: string; function: string; sql: string; error: string };
  idx: number;
}

function FailedQueryRow({ query, idx }: FailedQueryRowProps) {
  const sqlPreview = query.sql.length > 120
    ? `${query.sql.slice(0, 120)}...`
    : query.sql;

  return (
    <m.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.15, delay: idx * 0.03 }}
      className="px-5 py-3.5"
    >
      <HStack className="gap-2.5 items-center">
        <Icon name="alert" label="Error" className="w-3.5 h-3.5 text-content-negative-soft shrink-0" />
        <Text as="span" level="mono-small" className="text-content-layout-1">
          {query.function ? `${query.function}()` : '?'}
        </Text>
        <Tag size="small" variant="negative" modifier="ghost" label="error" />
      </HStack>
      <Text level="mono-small" className="text-content-layout-3 mt-1 block truncate">
        {sqlPreview}
      </Text>
      <Text level="caption" className="text-content-negative-soft mt-1 block">
        {query.error}
      </Text>
    </m.div>
  );
}

// ---------------------------------------------------------------------------
// Summary bar — mirrors CLI Analysis Summary panel
// ---------------------------------------------------------------------------

function AnalysisSummaryBar({ analysis }: { analysis: ScanAnalysisSummary }) {
  const totalAll = analysis.by_query.length + analysis.failed_queries.length;
  const belowFail = analysis.by_query.filter(
    (q) => q.risk_score !== null && q.risk_score < analysis.fail_threshold
  );
  const belowWarn = analysis.by_query.filter(
    (q) =>
      q.risk_score !== null &&
      q.risk_score >= analysis.fail_threshold &&
      q.risk_score < analysis.warn_threshold
  );

  return (
    <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50 space-y-2">
      {/* Top line: title + CI status */}
      <HStack className="justify-between items-center">
        <HStack className="gap-2 items-center">
          <Icon
            name="speedometer"
            label="Analysis"
            className="w-4 h-4 text-content-layout-3"
          />
          <Text
            level="overline"
            className="text-content-layout-3 uppercase tracking-wider"
          >
            Analysis Results
          </Text>
        </HStack>
        <HStack className="gap-2 items-center">
          <Tag
            size="small"
            variant={getCiStatusVariant(analysis.ci_status)}
            modifier="ghost"
            label={analysis.ci_status.toUpperCase()}
          />
        </HStack>
      </HStack>

      {/* Stats row */}
      <HStack className="gap-4 items-center flex-wrap">
        <Text level="caption" className="text-content-layout-2">
          <Text as="span" level="label-small" className="text-content-layout-1">
            {analysis.mode === 'shallow' ? 'Shallow' : 'Deep'}
          </Text>
          {' '}mode
        </Text>
        <Text level="caption" className="text-content-layout-2">
          <Text as="span" level="label-small" className="text-content-layout-1">{totalAll}</Text>
          {' '}total
        </Text>
        <Text level="caption" className="text-content-layout-2">
          <Text as="span" level="label-small" className="text-content-layout-1">{analysis.successful}</Text>
          {' '}analyzed
        </Text>
        {analysis.failed > 0 && (
          <Text level="caption" className="text-content-negative-soft">
            <Text as="span" level="label-small">{analysis.failed}</Text>
            {' '}errors
          </Text>
        )}
        <Text level="caption" className={getScoreColor(typeof analysis.worst_score === 'number' ? analysis.worst_score : null)}>
          Worst: <Text as="span" level="label-small">{analysis.worst_score}</Text>
        </Text>
        {belowFail.length > 0 && (
          <Text level="caption" className="text-content-negative-soft">
            <Text as="span" level="label-small">{belowFail.length}</Text>
            {' '}below fail ({analysis.fail_threshold})
          </Text>
        )}
        {belowWarn.length > 0 && (
          <Text level="caption" className="text-content-warning-soft">
            <Text as="span" level="label-small">{belowWarn.length}</Text>
            {' '}below warn ({analysis.warn_threshold})
          </Text>
        )}
      </HStack>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Table — analysis results container
// ---------------------------------------------------------------------------

export function ScanAnalysisTable({ analysis, scanTarget }: ScanAnalysisTableProps) {
  const navigate = useNavigate();
  const [detailQuery, setDetailQuery] = useState<ScanAnalyzedQuery | null>(null);

  const handleAnalyze = useCallback(() => {
    if (!detailQuery?.sql) return;
    navigate({
      to: '/results',
      search: { query: detailQuery.sql, target: scanTarget || undefined },
    });
  }, [detailQuery, navigate, scanTarget]);
  // Sort by score descending (best first)
  const sortedQueries = useMemo(
    () => [...analysis.by_query].sort((a, b) => (b.risk_score ?? -1) - (a.risk_score ?? -1)),
    [analysis.by_query]
  );

  if (sortedQueries.length === 0 && analysis.failed_queries.length === 0) {
    return null;
  }

  return (
    <>
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.3 }}
      >
        <Card className="w-full overflow-hidden">
          <Card.Content className="p-0">
            {/* Summary header */}
            <AnalysisSummaryBar analysis={analysis} />

            {/* Analyzed queries */}
            <Show when={sortedQueries.length > 0}>
              <div className="divide-y divide-border-layout-1">
                <AnimatePresence>
                  {sortedQueries.map((query, idx) => (
                    <AnalysisRow
                      key={query.hash}
                      query={query}
                      idx={idx}
                      onViewDetail={() => setDetailQuery(query)}
                    />
                  ))}
                </AnimatePresence>
              </div>
            </Show>

            {/* Failed queries */}
            <Show when={analysis.failed_queries.length > 0}>
              <div className="border-t border-border-layout-1">
                <div className="px-5 py-2 bg-surface-layout-2/30">
                  <Text level="caption" className="text-content-negative-soft uppercase tracking-wider">
                    Failed ({analysis.failed_queries.length})
                  </Text>
                </div>
                <div className="divide-y divide-border-layout-1">
                  {analysis.failed_queries.map((fq, idx) => (
                    <FailedQueryRow key={fq.hash} query={fq} idx={idx} />
                  ))}
                </div>
              </div>
            </Show>
          </Card.Content>
        </Card>
      </m.div>

      <AnalysisDetailModal
        query={detailQuery}
        onClose={() => setDetailQuery(null)}
        onAnalyze={handleAnalyze}
      />
    </>
  );
}

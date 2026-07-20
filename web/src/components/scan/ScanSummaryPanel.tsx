/**
 * Verdict banner for scan results — the primary "what did the scan find?"
 * answer (PASS/WARN/FAIL + worst score), with the extraction funnel as a
 * subordinate line and run metadata deferred to a tertiary caption.
 * Mirrors the CLI's _print_report funnel.
 */

import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Card } from '@rs/ui-new/card';
import { Tag } from '@rs/ui-new/tag';
import { Show } from '@rs/ui-new/show';
import { m } from '@rs/ui-new/motion';
import type { ScanSummary } from '../../types/scan';

interface ScanSummaryPanelProps {
  summary: ScanSummary;
}

export function ScanSummaryPanel({ summary }: ScanSummaryPanelProps) {
  const analysis = summary.analysis;
  const ciStatus = analysis?.ci_status;
  const ciVariant =
    ciStatus === 'pass' ? 'positive' : ciStatus === 'warn' ? 'warning' : 'negative';

  // VIS-097: a colored accent bar keys the whole banner to the verdict.
  const accentClass =
    ciStatus === 'pass'
      ? 'bg-surface-positive-solid'
      : ciStatus === 'warn'
        ? 'bg-surface-warning-solid'
        : ciStatus === 'fail'
          ? 'bg-surface-negative-solid'
          : 'bg-surface-layout-2';

  const verdictTextClass =
    ciStatus === 'pass'
      ? 'text-content-positive-soft'
      : ciStatus === 'warn'
        ? 'text-content-warning-soft'
        : 'text-content-negative-soft';

  return (
    <m.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      {/* Raised (elevation-1) surface with a left accent bar. */}
      <Card className="w-full overflow-hidden bg-surface-raised shadow-elevation-1">
        <Card.Content className="p-0">
          <div className="flex items-stretch">
            {/* Accent bar — VIS-097 */}
            <div className={`w-1 shrink-0 ${accentClass}`} aria-hidden="true" />

            <div className="flex-1 min-w-0">
              {/* Header */}
              <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
                <HStack className="justify-between items-center">
                  <HStack className="gap-2 items-center">
                    <Icon name="document-validation" label="Summary" className="w-4 h-4 text-content-layout-3" />
                    <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                      Scan Summary
                    </Text>
                  </HStack>
                  {analysis && ciStatus && (
                    <Tag
                      size="small"
                      variant={ciVariant as 'positive' | 'warning' | 'negative'}
                      modifier="solid"
                      label={ciStatus.toUpperCase()}
                    />
                  )}
                </HStack>
              </div>

              <div className="p-5 space-y-4">
                {/* Verdict — primary, first-hit line */}
                <Show when={!!analysis && !!ciStatus}>
                  <HStack className="gap-3 items-baseline flex-wrap">
                    <Text level="headline-3" className={verdictTextClass}>
                      {ciStatus?.toUpperCase()}
                    </Text>
                    <Text level="body-small" className={verdictTextClass}>
                      Worst score: {analysis?.worst_score ?? 'N/A'}
                    </Text>
                    <Text level="caption" className="text-content-layout-3">
                      {analysis?.mode === 'shallow' ? 'Shallow (schema-only)' : 'Deep (EXPLAIN ANALYZE)'}
                    </Text>
                  </HStack>
                </Show>

                {/* Extraction funnel — subordinate line */}
                <div className="flex flex-wrap gap-x-10 gap-y-4">
                  <VStack className="gap-1 items-start">
                    <Text level="caption" className="text-content-layout-3">
                      Files with ORM code
                    </Text>
                    <Text level="headline-4" className="text-content-layout-1">
                      {summary.files_count}
                    </Text>
                  </VStack>
                  <VStack className="gap-1 items-start">
                    <Text level="caption" className="text-content-layout-3">
                      ORM snippets extracted
                    </Text>
                    <Text level="headline-4" className="text-content-layout-1">
                      {summary.queries_total}
                    </Text>
                  </VStack>
                  <VStack className="gap-1 items-start">
                    <Text level="caption" className="text-content-layout-3">
                      Converted to SQL
                    </Text>
                    <Text level="headline-4" className="text-content-positive-soft">
                      {summary.queries_sql}
                    </Text>
                  </VStack>
                  <VStack className="gap-1 items-start">
                    <Text level="caption" className="text-content-layout-3">
                      Skipped
                    </Text>
                    <Text level="headline-4" className="text-content-layout-2">
                      {summary.queries_skipped}
                    </Text>
                  </VStack>
                </div>

                {/* Run metadata — tertiary caption, deferred below the verdict */}
                <div className="flex flex-wrap gap-x-6 gap-y-1 pt-2.5 border-t border-border-layout-1">
                  <Text level="caption" className="text-content-layout-3">
                    Extraction: AST (deterministic)
                  </Text>
                  <Text level="caption" className="text-content-layout-3">
                    Cache: {summary.cache_hits} hits, {summary.cache_misses} misses
                  </Text>
                  <Show when={!summary.registry_skipped}>
                    <Text level="caption" className="text-content-layout-3">
                      Registry: {summary.registry_new} new, {summary.registry_updated} updated, {summary.registry_total} total
                    </Text>
                  </Show>
                  <Show when={summary.registry_skipped}>
                    <Text level="caption" className="text-content-layout-3">
                      Registry: skipped
                    </Text>
                  </Show>
                  <Show when={!!analysis}>
                    <Text level="caption" className="text-content-layout-3">
                      Analysis: {analysis?.successful ?? 0} analyzed
                      {analysis && analysis.failed > 0 ? `, ${analysis.failed} errors` : ''}
                    </Text>
                  </Show>
                </div>
              </div>
            </div>
          </div>
        </Card.Content>
      </Card>
    </m.div>
  );
}

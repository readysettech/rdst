import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { Link } from '@tanstack/react-router'
import {
  formatReportNumber,
  healthScoreColor,
  VERDICT_LABELS,
} from '../../../lib/auditReportFormat'
import type { AuditReport } from '../../../types/audit'
import { InstanceClassValue } from './ReportPrimitives'

/**
 * PRIMARY of the report view: the verdict, raised via the elevation-token scale
 * (VIS-075/076/080/105) — depth by a lightness step + a dark-tuned shadow, not
 * a border or a raw shadow-xl. The AI health-score badge is the single accent
 * of the report state (VIS-013/016/097). Degrades gracefully with no key: the
 * sizing verdict leads and a muted note points at Configure (H-4).
 */
function isAuthFailure(error: string | undefined): boolean {
  return (
    !!error &&
    /(?:api key|auth(?:entication|orization)?|unauthorized|\b401\b|invalid key)/i.test(
      error
    )
  )
}

export function VerdictCard({
  report,
  aiCredentialInvalid = false,
}: {
  report: AuditReport
  aiCredentialInvalid?: boolean
}) {
  const sizing = report.sizing || {}
  const health = report.health_analysis
  const healthOk =
    !!health && !health.error && health.health_score !== undefined
  const verdict =
    VERDICT_LABELS[sizing.verdict || 'unknown'] || VERDICT_LABELS.unknown
  const summary =
    health?.health_score_rationale ||
    health?.executive_summary ||
    sizing.explanation

  return (
    <div className="rounded-[1.25rem] bg-surface-raised shadow-elevation-1 p-6">
      <VStack className="gap-4 items-stretch">
        <Text
          level="overline"
          className="text-content-layout-2 uppercase tracking-wider"
        >
          Verdict
        </Text>
        <HStack className="gap-3 items-center flex-wrap">
          {healthOk && (
            <HStack className="gap-2 items-baseline">
              <Text
                level="headline-1"
                className={`tabular-nums ${healthScoreColor(health!.health_score!)}`}
              >
                {formatReportNumber(health!.health_score, 0)}
              </Text>
              <Text level="body-small" className="text-content-layout-2">
                / 100
              </Text>
              <Tag
                variant={
                  health!.health_score! >= 75
                    ? 'positive'
                    : health!.health_score! >= 60
                      ? 'warning'
                      : 'negative'
                }
                modifier="ghost"
                label={health!.health_label || 'SCORE'}
              />
            </HStack>
          )}
          <Tag
            variant={verdict.variant}
            modifier="ghost"
            label={verdict.label}
          />
          <InstanceClassValue report={report} />
        </HStack>
        {summary && (
          <Text level="body-small" className="text-content-layout-2">
            {summary}
          </Text>
        )}
        {!healthOk && (
          <HStack className="gap-1.5 items-center flex-wrap">
            <Icon
              name="sparkles"
              label=""
              aria-hidden="true"
              className="w-3.5 h-3.5 text-content-layout-2 shrink-0"
            />
            <Text level="caption" className="text-content-layout-2">
              {health?.error && !isAuthFailure(health.error)
                ? `AI analysis failed: ${health.error}`
                : 'AI health score unavailable'}
            </Text>
            {(isAuthFailure(health?.error) ||
              (!health?.error && aiCredentialInvalid)) && (
              <Link to="/configure" className="hover:underline">
                <Text level="caption" className="text-content-primary-soft">
                  Configure
                </Text>
              </Link>
            )}
          </HStack>
        )}
      </VStack>
    </div>
  )
}

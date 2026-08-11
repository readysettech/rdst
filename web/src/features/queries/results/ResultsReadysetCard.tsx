import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { Icon, type IconStrokeName } from '@rs/ui-new/icon'
import { IconTile } from '@rs/ui-new/icon-tile'
import { m, useReducedMotion } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { getTransition } from '@rs/ui-new/transition'
import type { ComponentProps } from 'react'
import { ResultSectionIndex } from './ResultSectionIndex'
import { resultToneStyles } from './resultStyles'
import type { ReadysetVerdict } from './resultsSelectors'

function CachePathNode({
  icon,
  label,
  state = 'neutral',
  showCheck,
  checkTransition,
  shouldReduceMotion,
}: {
  icon: IconStrokeName
  label: string
  state?: 'neutral' | 'active' | 'muted'
  showCheck?: boolean
  checkTransition?: ComponentProps<typeof m.div>['transition']
  shouldReduceMotion: boolean | null
}) {
  return (
    <VStack
      className={`min-w-14 items-center gap-2 ${
        state === 'muted' ? 'opacity-50' : ''
      }`}
    >
      <div className="relative">
        <IconTile
          icon={icon}
          size="base"
          accent="primary"
          className={
            state === 'active'
              ? 'border border-border-positive-soft shadow-glow-positive'
              : undefined
          }
        />
        {showCheck ? (
          <m.div
            className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-surface-positive-solid"
            initial={
              shouldReduceMotion
                ? false
                : {
                    opacity: 0,
                    scale: 0.75,
                  }
            }
            animate={{ opacity: 1, scale: 1 }}
            transition={checkTransition}
          >
            <Icon
              name="tick"
              label=""
              aria-hidden="true"
              className="h-2.5 w-2.5 text-content-positive-solid"
            />
          </m.div>
        ) : null}
      </div>
      <Text
        level="caption"
        className={
          state === 'active'
            ? 'text-content-positive-soft'
            : 'text-content-layout-3'
        }
      >
        {label}
      </Text>
    </VStack>
  )
}

function CachedQueryPath() {
  const shouldReduceMotion = useReducedMotion()
  const lineTransition = shouldReduceMotion
    ? { duration: 0 }
    : {
        ...getTransition('cubicSlow'),
        duration: 0.9,
        delay: 0.18,
      }
  const statusTransition = shouldReduceMotion
    ? { duration: 0 }
    : {
        ...getTransition('cubicSlow'),
        duration: 0.55,
        delay: 0.82,
      }

  return (
    <div
      role="img"
      aria-label="Query is served by an active Readyset cache while the origin database is bypassed"
      className="mt-auto flex h-36 flex-col overflow-hidden rounded-xl border border-border-layout-1 bg-surface-layout-2/40"
    >
      <div className="flex items-center justify-between gap-4 border-b border-border-layout-1 px-4 py-3">
        <Text level="label-extra-small" className="text-content-layout-3">
          Active request path
        </Text>
        <m.div
          className="flex shrink-0 items-center gap-2"
          initial={
            shouldReduceMotion
              ? false
              : {
                  opacity: 0,
                  x: 4,
                }
          }
          animate={{ opacity: 1, x: 0 }}
          transition={statusTransition}
        >
          <Text
            level="label-extra-small"
            className="text-content-positive-soft"
          >
            Cache hit
          </Text>
          <span
            aria-hidden="true"
            className="h-1 w-1 rounded-full bg-border-layout-1"
          />
          <Text level="caption" className="text-content-layout-3">
            Origin bypassed
          </Text>
        </m.div>
      </div>

      <div className="min-h-0 flex-1 px-5 py-3">
        <div className="grid grid-cols-[auto_1fr_auto_1fr_auto] items-start gap-2">
          <CachePathNode
            icon="querypilot"
            label="Query"
            shouldReduceMotion={shouldReduceMotion}
          />

          <div className="relative mt-5 flex items-center">
            <div className="h-px w-full bg-border-layout-1" />
            <m.div
              className="absolute inset-x-0 h-px origin-left bg-border-positive-soft"
              initial={shouldReduceMotion ? false : { scaleX: 0 }}
              animate={{ scaleX: 1 }}
              transition={lineTransition}
            />
            <m.div
              className="absolute -right-1"
              initial={shouldReduceMotion ? false : { opacity: 0, x: -4 }}
              animate={{ opacity: 1, x: 0 }}
              transition={statusTransition}
            >
              <Icon
                name="chevron-right"
                label=""
                aria-hidden="true"
                className="h-3 w-3 text-content-positive-soft"
              />
            </m.div>
          </div>

          <CachePathNode
            icon="database-settings"
            label="Readyset"
            state="active"
            showCheck
            checkTransition={statusTransition}
            shouldReduceMotion={shouldReduceMotion}
          />

          <div className="mt-5 border-t border-dashed border-border-layout-1" />

          <CachePathNode
            icon="database"
            label="Origin"
            state="muted"
            shouldReduceMotion={shouldReduceMotion}
          />
        </div>
      </div>
    </div>
  )
}

function ReadysetStatePanel({ verdict }: { verdict: ReadysetVerdict }) {
  const style = resultToneStyles[verdict.tone]
  const accent =
    verdict.tone === 'positive'
      ? ('positive' as const)
      : verdict.tone === 'warning'
        ? ('warning' as const)
        : verdict.tone === 'negative'
          ? ('negative' as const)
          : ('info' as const)
  const icon = verdict.verified
    ? verdict.cacheable
      ? ('tick-double' as const)
      : ('close' as const)
    : verdict.estimated
      ? ('info' as const)
      : ('alert' as const)
  const detailParts = [
    ...verdict.issues,
    ...verdict.warnings,
    verdict.technicalDetail,
  ].filter((detail): detail is string => Boolean(detail))
  const detail = [...new Set(detailParts)].join(' · ')

  const panel = verdict.estimated
    ? {
        eyebrow: 'Static SQL screen',
        status:
          verdict.issues.length > 0
            ? `${verdict.issues.length} potential blocker${verdict.issues.length === 1 ? '' : 's'}`
            : 'No obvious blockers',
        title: 'Readyset verification required',
        description:
          detail ||
          'Run a Readyset verification before using this result as a compatibility verdict.',
      }
    : verdict.cacheable
      ? {
          eyebrow: 'Cache readiness',
          status:
            verdict.warnings.length > 0
              ? `${verdict.warnings.length} warning${verdict.warnings.length === 1 ? '' : 's'}`
              : 'Verified',
          title: 'Ready for cache setup',
          description:
            detail ||
            'Readyset found no compatibility blockers for this query.',
        }
      : verdict.verified
        ? {
            eyebrow: 'Compatibility result',
            status:
              verdict.issues.length > 0
                ? `${verdict.issues.length} blocker${verdict.issues.length === 1 ? '' : 's'}`
                : 'Not cacheable',
            title: 'Changes required',
            description:
              detail ||
              'The query needs a compatibility change before Readyset can cache it.',
          }
        : {
            eyebrow: 'Verification status',
            status: 'Incomplete',
            title: 'Verification needs attention',
            description:
              detail ||
              'Retry the compatibility check when Readyset is available.',
          }

  return (
    <div className="mt-auto flex h-36 flex-col overflow-hidden rounded-xl border border-border-layout-1 bg-surface-layout-2/40">
      <div className="flex items-center justify-between gap-4 border-b border-border-layout-1 px-4 py-3">
        <Text level="label-extra-small" className="text-content-layout-3">
          {panel.eyebrow}
        </Text>
        <Text level="label-extra-small" className={style.text}>
          {panel.status}
        </Text>
      </div>

      <HStack className="min-h-0 flex-1 items-center gap-3 px-4 py-3">
        <IconTile icon={icon} size="base" accent={accent} />
        <VStack className="min-w-0 items-start gap-1">
          <Text level="label-small" className="text-content-layout-1">
            {panel.title}
          </Text>
          <Text level="caption" className="line-clamp-2 text-content-layout-3">
            {panel.description}
          </Text>
        </VStack>
      </HStack>
    </div>
  )
}

export function ResultsReadysetCard({
  verdict,
  actionIsPrimary,
  onSetUpCaching,
  onCacheQuery,
  onDeployNavigate,
  cacheDeployed,
  isCaching,
}: {
  verdict: ReadysetVerdict
  actionIsPrimary: boolean
  onSetUpCaching?: () => void
  onCacheQuery?: () => void
  onDeployNavigate?: () => void
  cacheDeployed?: boolean
  isCaching?: boolean
}) {
  const style = resultToneStyles[verdict.tone]

  const action = verdict.cacheable
    ? onSetUpCaching
      ? {
          label: verdict.alreadyCached ? 'View saved query' : 'Set up caching…',
          icon: verdict.alreadyCached
            ? ('arrow-right' as const)
            : ('database-settings' as const),
          onClick: onSetUpCaching,
        }
      : cacheDeployed && onCacheQuery
        ? {
            label: 'Cache this query',
            icon: 'add' as const,
            onClick: onCacheQuery,
          }
        : onDeployNavigate
          ? {
              label: 'Deploy cache first',
              icon: 'database-settings' as const,
              onClick: onDeployNavigate,
            }
          : undefined
    : undefined
  const footerStatus = verdict.cacheable
    ? verdict.alreadyCached
      ? 'Cache is active'
      : 'Ready to set up'
    : verdict.estimated
      ? 'Static check only'
      : verdict.verified
        ? 'No caching action'
        : 'Verification incomplete'

  return (
    <Card className="h-full">
      <Card.Header className="items-stretch gap-3 tablet:flex-row tablet:items-center tablet:justify-between">
        <HStack className="items-center gap-3">
          <ResultSectionIndex value={3} />
          <VStack className="items-start gap-1">
            <Card.Title>Readyset compatibility</Card.Title>
            <Card.Description>
              Readyset status and next action.
            </Card.Description>
          </VStack>
        </HStack>
        <div className="self-end tablet:self-auto">
          <Tag
            variant={style.tag}
            modifier="ghost"
            label={verdict.tag}
            icon={style.icon}
            iconPosition="left"
          />
        </div>
      </Card.Header>

      <Card.Content className="flex flex-1 flex-col gap-8 p-6">
        <VStack className="min-w-0 items-start gap-2">
          <Text level="subtitle-1" className="text-content-layout-1">
            {verdict.title}
          </Text>
          {verdict.body ? (
            <Text
              level="body-small"
              className="max-w-3xl leading-relaxed text-content-layout-2"
            >
              {verdict.body}
            </Text>
          ) : null}
        </VStack>

        {verdict.alreadyCached ? (
          <CachedQueryPath />
        ) : (
          <ReadysetStatePanel verdict={verdict} />
        )}
      </Card.Content>

      <Card.Footer
        className={
          action && !actionIsPrimary ? 'min-h-18' : 'min-h-18 justify-start'
        }
      >
        {action && !actionIsPrimary ? (
          <Button
            variant="rising"
            modifier="outline"
            label={action.label}
            icon={action.icon}
            iconPosition="left"
            onClick={action.onClick}
            loading={isCaching}
          />
        ) : (
          <HStack className="items-center gap-1.5">
            <Icon
              name={
                verdict.cacheable
                  ? 'tick-double'
                  : verdict.verified
                    ? 'close'
                    : 'alert'
              }
              label=""
              aria-hidden="true"
              className={`h-3.5 w-3.5 shrink-0 ${style.text}`}
            />
            <Text level="caption" className={style.text}>
              {footerStatus}
            </Text>
          </HStack>
        )}
      </Card.Footer>
    </Card>
  )
}

import { cn, tv, type VariantProps } from '@rs/tailwind-base'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { memo, useId, useState } from 'react'
import { Button } from '../element/button'
import { HStack, VStack } from '../element/stack'
import { Text } from '../element/text'
import { Icon } from '../svg/icon'

/**
 * The six failure classes of the shared error contract (B7/T24). Each maps to
 * an accent + glyph and, at the call site, to one recovery destination. The
 * component itself is presentation-only: routing lives with the composer so
 * `@rs/ui-new` stays framework-agnostic.
 */
export type ErrorClass =
  | 'user-config'
  | 'database'
  | 'local-dependency'
  | 'rdst-service'
  | 'provider'
  | 'valid-negative'

type Accent = 'negative' | 'warning' | 'info'

const CLASS_META: Record<ErrorClass, { accent: Accent; icon: IconStrokeName }> =
  {
    'user-config': { accent: 'warning', icon: 'settings' },
    database: { accent: 'negative', icon: 'database' },
    'local-dependency': { accent: 'warning', icon: 'layers' },
    'rdst-service': { accent: 'warning', icon: 'user-shield' },
    provider: { accent: 'warning', icon: 'sparkles' },
    'valid-negative': { accent: 'info', icon: 'info' },
  }

/** One primary recovery action, routed to the right screen by the caller. */
export interface ErrorAction {
  label: string
  onClick: () => void
  icon?: IconStrokeName
}

interface ErrorFields {
  /** What failed (short headline). */
  title: string
  /** The most specific safe cause — never a raw driver dump. */
  message: string
  /** Which results remain trustworthy (shown when only part of a flow failed). */
  trustworthy?: string
  /** The recovery action; omit when there is nowhere useful to route. */
  action?: ErrorAction
  /** Retry — offer only when a retry can plausibly help. */
  onRetry?: () => void
  retryLabel?: string
  /** Technical / correlation detail, kept behind an expander. */
  detail?: string
}

// Accent recipes reuse the app's established stateful-card vocabulary: soft
// surface + soft border + the same variant glow the analysis verdict cards use
// (AnalysisSections.variantStyles) — deliberate depth, not a new elevation set.
// NEEDS TOKEN (C-07/T18): the glow shadows below are arbitrary values repeated
// from AnalysisSections.variantStyles — fold into @rs/tailwind-base as
// shadow-glow-{positive|info|warning|negative} (or the planned elevation-* set)
// and consume the token here and there.
const surfaceRecipe = tv({
  base: ['rounded-xl', 'border'],
  variants: {
    accent: {
      negative: [
        'bg-surface-negative-soft/50',
        'border-border-negative-soft',
        'shadow-[0_0_20px_rgba(239,68,68,0.15)]',
      ],
      warning: [
        'bg-surface-warning-soft/50',
        'border-border-warning-soft',
        'shadow-[0_0_20px_rgba(234,179,8,0.15)]',
      ],
      info: [
        'bg-surface-info-soft/50',
        'border-border-info-soft',
        'shadow-[0_0_20px_rgba(59,130,246,0.15)]',
      ],
    },
  },
  defaultVariants: { accent: 'negative' },
})

const badgeRecipe = tv({
  variants: {
    accent: {
      negative: ['bg-surface-negative-soft'],
      warning: ['bg-surface-warning-soft'],
      info: ['bg-surface-info-soft'],
    },
  },
  defaultVariants: { accent: 'negative' },
})

// The page-level badge floats on the page background and is the focal point,
// so it carries the glow itself (shadow conveys elevation — VIS-075).
const pageBadgeRecipe = tv({
  base: ['rounded-2xl', 'flex', 'items-center', 'justify-center'],
  variants: {
    accent: {
      negative: [
        'bg-surface-negative-soft',
        'shadow-[0_0_20px_rgba(239,68,68,0.15)]',
      ],
      warning: [
        'bg-surface-warning-soft',
        'shadow-[0_0_20px_rgba(234,179,8,0.15)]',
      ],
      info: ['bg-surface-info-soft', 'shadow-[0_0_20px_rgba(59,130,246,0.15)]'],
    },
  },
  defaultVariants: { accent: 'negative' },
})

const accentTextRecipe = tv({
  variants: {
    accent: {
      negative: ['text-content-negative-soft'],
      warning: ['text-content-warning-soft'],
      info: ['text-content-info-soft'],
    },
  },
  defaultVariants: { accent: 'negative' },
})

type AccentVariant = VariantProps<typeof surfaceRecipe>

/**
 * "Technical details" disclosure — the contract's expander for raw technical
 * text (driver output, correlation ids). Exported so section-level surfaces
 * (e.g. the analyze cacheability card) can keep raw text out of primary copy.
 */
export function DetailExpander({
  detail,
  id,
  align = 'start',
}: {
  detail: string
  id: string
  align?: 'start' | 'center'
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className={cn('mt-1', align === 'center' && 'w-full')}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
        className="text-content-layout-3 hover:text-content-layout-2 transition-colors text-label-extra-small underline underline-offset-2"
      >
        {open ? 'Hide technical details' : 'Technical details'}
      </button>
      {open && (
        <pre
          id={id}
          className="mt-2 whitespace-pre-wrap break-words rounded-lg border border-border-layout-1 bg-surface-layout-2/60 p-3 text-content-layout-3 text-mono-small text-left"
        >
          {detail}
        </pre>
      )}
    </div>
  )
}

export interface ErrorStateProps extends ErrorFields {
  /** Drives the glyph + accent and hints the recovery destination. */
  errorClass?: ErrorClass
  /** Override the class-derived glyph. */
  icon?: IconStrokeName
  /**
   * `card` (default): a section-level horizontal card for inline slots.
   * `page`: a centered, spacious full-page state for boundaries (404, root
   * error) — designed as a first-class empty state, not a fallback banner.
   */
  layout?: 'card' | 'page'
  /** Small overline above the title on the page layout (e.g. "404"). */
  eyebrow?: string
  className?: string
}

/**
 * Page- / card-level error surface. Renders the six required fields of the
 * shared contract: what failed, the safe cause, which results remain
 * trustworthy, one routed recovery action, an optional retry, and technical
 * detail behind an expander.
 */
function ErrorStateImpl({
  errorClass = 'database',
  icon,
  layout = 'card',
  eyebrow,
  title,
  message,
  trustworthy,
  action,
  onRetry,
  retryLabel = 'Try again',
  detail,
  className,
}: ErrorStateProps) {
  const meta = CLASS_META[errorClass]
  const accent: Accent = meta.accent
  const glyph = icon ?? meta.icon
  const detailId = useId()

  if (layout === 'page') {
    // Full-page state: centered vertical composition with generous whitespace;
    // no card border — the badge glow and spacing do the separating. The title
    // stays in the primary text color (the icon carries the accent) and the
    // single solid primary button is the one obvious next step.
    return (
      <div
        role="alert"
        className={cn(
          'mx-auto flex w-full max-w-md flex-col items-center px-6 py-24 text-center',
          className
        )}
      >
        <div
          className={cn(
            pageBadgeRecipe({ accent } as AccentVariant),
            'w-16 h-16'
          )}
        >
          <Icon
            name={glyph}
            label={title}
            className={cn(
              'w-8 h-8',
              accentTextRecipe({ accent } as AccentVariant)
            )}
          />
        </div>

        {eyebrow && (
          <Text
            as="span"
            level="overline"
            className="mt-8 text-content-layout-3 tracking-wider"
          >
            {eyebrow}
          </Text>
        )}

        <Text
          as="h1"
          level="headline-3"
          className={cn('text-content-layout-1', eyebrow ? 'mt-2' : 'mt-8')}
        >
          {title}
        </Text>

        <Text
          level="body-medium"
          className="mt-3 max-w-sm text-content-layout-3 leading-relaxed"
        >
          {message}
        </Text>

        {trustworthy && (
          <Text level="caption" className="mt-3 text-content-layout-3">
            {trustworthy}
          </Text>
        )}

        {(action || onRetry) && (
          <HStack className="mt-8 flex-wrap items-center justify-center gap-3">
            {action && (
              <Button
                variant="primary"
                modifier="solid"
                label={action.label}
                icon={action.icon}
                iconPosition={action.icon ? 'left' : 'none'}
                onClick={action.onClick}
              />
            )}
            {onRetry && (
              <Button
                variant="primary"
                modifier="ghost"
                label={retryLabel}
                onClick={onRetry}
              />
            )}
          </HStack>
        )}

        {detail && (
          <div className="mt-8 w-full">
            <DetailExpander detail={detail} id={detailId} align="center" />
          </div>
        )}
      </div>
    )
  }

  return (
    <div
      role="alert"
      className={cn(
        surfaceRecipe({ accent } as AccentVariant),
        'p-6',
        className
      )}
    >
      <HStack className="gap-4 items-start">
        <div
          className={cn(
            badgeRecipe({ accent } as AccentVariant),
            'w-12 h-12 rounded-xl flex items-center justify-center shrink-0'
          )}
        >
          <Icon
            name={glyph}
            label={title}
            className={cn(
              'w-6 h-6',
              accentTextRecipe({ accent } as AccentVariant)
            )}
          />
        </div>
        <VStack className="gap-2 items-start flex-1 min-w-0">
          <Text
            level="headline-4"
            className={accentTextRecipe({ accent } as AccentVariant)}
          >
            {title}
          </Text>
          <Text
            level="body-small"
            className="text-content-layout-2 leading-relaxed"
          >
            {message}
          </Text>

          {trustworthy && (
            <Text level="caption" className="text-content-layout-3">
              {trustworthy}
            </Text>
          )}

          {(action || onRetry) && (
            <HStack className="gap-2 items-center mt-2 flex-wrap">
              {action && (
                <Button
                  variant="primary"
                  modifier="outline"
                  size="small"
                  label={action.label}
                  icon={action.icon}
                  iconPosition={action.icon ? 'left' : 'none'}
                  onClick={action.onClick}
                />
              )}
              {onRetry && (
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  label={retryLabel}
                  onClick={onRetry}
                />
              )}
            </HStack>
          )}

          {detail && <DetailExpander detail={detail} id={detailId} />}
        </VStack>
      </HStack>
    </div>
  )
}

export const ErrorState = memo(ErrorStateImpl)

export interface InlineNoticeProps extends ErrorFields {
  errorClass?: ErrorClass
  /** Override the class-derived accent (e.g. a warning for a soft notice). */
  accent?: Accent
  icon?: IconStrokeName
  className?: string
}

/**
 * Compact, section-level variant of {@link ErrorState}. Same contract, smaller
 * footprint — for inline slots (a table row, a panel, a routable credential
 * prompt) where a full card would be too heavy. Glow-free: an inline notice
 * sits inside other content and should not claim elevation.
 */
function InlineNoticeImpl({
  errorClass = 'database',
  accent: accentOverride,
  icon,
  title,
  message,
  trustworthy,
  action,
  onRetry,
  retryLabel = 'Try again',
  detail,
  className,
}: InlineNoticeProps) {
  const meta = CLASS_META[errorClass]
  const accent: Accent = accentOverride ?? meta.accent
  const glyph = icon ?? meta.icon
  const detailId = useId()

  return (
    <div
      role="alert"
      className={cn(
        surfaceRecipe({ accent } as AccentVariant),
        'shadow-none p-4',
        className
      )}
    >
      <HStack className="gap-3 items-start">
        <Icon
          name={glyph}
          label={title ?? 'Notice'}
          className={cn(
            'w-4 h-4 mt-0.5 shrink-0',
            accentTextRecipe({ accent } as AccentVariant)
          )}
        />
        <VStack className="gap-1 items-start flex-1 min-w-0">
          {title && (
            <Text
              level="label-small"
              className={accentTextRecipe({ accent } as AccentVariant)}
            >
              {title}
            </Text>
          )}
          <Text
            level="body-small"
            className="text-content-layout-2 leading-relaxed"
          >
            {message}
          </Text>
          {trustworthy && (
            <Text level="caption" className="text-content-layout-3">
              {trustworthy}
            </Text>
          )}
          {(action || onRetry) && (
            <HStack className="gap-2 items-center mt-1 flex-wrap">
              {action && (
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  label={action.label}
                  icon={action.icon}
                  iconPosition={action.icon ? 'left' : 'none'}
                  onClick={action.onClick}
                />
              )}
              {onRetry && (
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  label={retryLabel}
                  onClick={onRetry}
                />
              )}
            </HStack>
          )}
          {detail && <DetailExpander detail={detail} id={detailId} />}
        </VStack>
      </HStack>
    </div>
  )
}

export const InlineNotice = memo(InlineNoticeImpl)

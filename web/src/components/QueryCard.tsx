/**
 * Canonical query card — THE single card idiom for every surface that lists a SQL
 * query (Slow Queries, Analyze "Recent", Saved Queries, Cached Queries, Benchmark
 * selection). One anatomy everywhere; per-page differences are props only.
 * [feedback-triage-2 §1.1; VIS-108/128 shared standard, USE-097 cross-screen]
 *
 * v3 anatomy (owner ref — a /top card, evaluated live and re-directed):
 *
 *   ┌──────────────────────────────────────────────────[⧉ Copy]┐
 *   │  {leading}  {title} {badges}                              │
 *   │  SELECT … syntax-highlighted, full, wrapped, no inner box │
 *   ├──────────────────────────────────────────────────────────┤
 *   │  hash … · freq … · total … · avg … · load …   [Analyze …] │
 *   └──────────────────────────────────────────────────────────┘
 *
 * Three ideas, each a prop:
 *   1. SQL — `sql`. Sits DIRECTLY in the card (no inset panel, no inner scroll,
 *      no click-to-transform): full, syntax-highlighted, wrapped, the SAME on
 *      every row via the lightweight `SqlTokens` regex highlighter (never a
 *      CodeMirror instance per row). The SQL is the query's identity, so it
 *      dominates. [VIS-011/017 SQL dominant, VIS-124 mono personality]
 *   2. Meta — `meta`. ONE quiet muted-mono stat line, bottom-LEFT, folding each
 *      page's metrics into `label value · label value …` (the old right-hand
 *      spec column is gone). May wrap; never becomes columns. [USE-002/003 fold
 *      labels into values, VIS-011 quiet secondary, VIS-104 no divider column]
 *   3. Actions — `actions`. Right-aligned on the SAME footer row as the meta
 *      line. Omit both meta and actions → no footer. [VIS-022/023 hierarchy]
 *
 * One copy affordance per card: a quiet tertiary Copy pinned TOP-RIGHT, always
 * visible (not hover-gated, never over a scrollbar). [triage §1.3]
 *
 * Per-page behaviour, still one anatomy:
 *   - `clickable` — whole surface selects the query (Analyze "Recent"). It keeps
 *     the SAME base card colour as every other card (one global card surface); it
 *     only LIFTS on hover/focus (raised tint + shadow) to read as clickable. It is
 *     separated from the page by its wrapping container panel, not by a per-page
 *     card colour. [USE-018 clickable must look clickable, USE-097 consistency,
 *     VIS-104 separate via surface step, VIS-080 lighter = raised on hover]
 *   - `selectable` / `selected` — a leading checkbox + selected accent (Benchmark
 *     multi-select). [VIS-108 selectable cards over radio stacks]
 *   - `children` escape hatch — a row's transient inline state (delete-confirm,
 *     rename, SQL edit) owns the padded surface; the surface + data attributes
 *     stay owned by QueryCard.
 *
 * Surfaces separate via a background step + shadow, not hard borders (our
 * VIS-104/080 stance). Presentation only: every handler, navigation param and
 * stateful editor lives in the calling screen and is passed through as a slot.
 *
 * Stays app-local (not `@rs/ui-new`) and is imported DIRECTLY by each consumer
 * (never through the `../components` barrel). It no longer imports `SQLDisplay`,
 * so the card carries zero CodeMirror weight of its own. [triage §1.1 lazy-split]
 */

import { CopyButton } from '@rs/ui-new/copy-button'
import { Icon } from '@rs/ui-new/icon'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react'
import { SqlTokens } from './SqlTokens'

interface QueryCardProps {
  /** Full SQL for the row — the query's identity (drives the highlighted body + copy). */
  sql: string

  /** Leading element before the SQL area — the rank number in /top. Ignored when `selectable`. */
  leading?: ReactNode
  /** Optional query name/title above the SQL (Cache name, Benchmark tag, Saved title). */
  title?: ReactNode
  /** Source / status chip(s) next to the title (or above the SQL when there is no title). */
  badges?: ReactNode

  /** The single muted-mono stat line (footer-left). Omit for a meta-less card. */
  meta?: ReactNode

  /** Footer action bar (right-aligned, same row as `meta`). Omit → no actions. */
  actions?: ReactNode

  /** Whole-surface click (Analyze "Recent" selects the query into the editor). */
  clickable?: boolean
  onClick?: () => void

  /** Multi-select mode (Benchmark): a leading checkbox + selected accent. */
  selectable?: boolean
  selected?: boolean

  /**
   * Escape hatch: replaces the standard body with a padded surface. Used for a
   * row's transient inline states (delete-confirm, rename, SQL edit).
   */
  children?: ReactNode

  /**
   * Additive slot: a full-width region rendered INSIDE the card, below the
   * footer row. Hosts a row's revealed detail that is too wide for a badge or
   * the meta line — the Queries workbench uses it for the "Cache & test"
   * before/after payoff (origin-vs-cache bars) plus the hash/params/timestamps
   * detail. Optional and unset by default, so /top, /analyze and /benchmark —
   * which never pass it — render byte-identically. [triage §1.1; VIS-108]
   */
  expansion?: ReactNode

  className?: string
  'data-testid'?: string
  'data-query-hash'?: string
  'data-cache-id'?: string
}

/** Selection checkbox for `selectable` mode — the app's hand-rolled tick box, so a
 * card can host it without nesting a real `<button>` inside a clickable surface. */
function QueryCardCheckbox({ selected }: { selected: boolean }) {
  return (
    <div
      aria-hidden
      className={`w-5 h-5 rounded-md border-2 flex items-center justify-center shrink-0 mt-0.5 transition-colors ${
        selected
          ? 'bg-surface-primary-solid border-surface-primary-solid'
          : 'border-border-layout-2'
      }`}
    >
      <Show when={selected}>
        <Icon
          name="tick"
          label="Selected"
          className="w-3 h-3 text-content-primary-solid"
        />
      </Show>
    </div>
  )
}

export function QueryCard({
  sql,
  leading,
  title,
  badges,
  meta,
  actions,
  clickable = false,
  onClick,
  selectable = false,
  selected = false,
  children,
  expansion,
  className,
  'data-testid': dataTestid,
  'data-query-hash': dataQueryHash,
  'data-cache-id': dataCacheId,
}: QueryCardProps) {
  // Surface: ONE global card colour (`surface-layout-2`); cards are separated
  // from the page by their wrapping container panel, never by a per-page card
  // tint [USE-097, VIS-104]. A clickable card keeps that base colour and only
  // LIFTS on hover/focus (raised tint + shadow) to read as clickable [USE-018,
  // VIS-080]; a static card must NOT look clickable [USE-020]. Selected carries a
  // primary accent with ≥2 cues (fill + border + shadow), never colour alone
  // [VIS-119 / USE-005].
  let surfaceTone: string
  if (selectable) {
    surfaceTone = selected
      ? 'bg-surface-primary-soft border border-border-primary-soft shadow-elevation-2'
      : 'bg-surface-layout-2 border border-border-layout-1 hover:bg-surface-raised hover:shadow-elevation-1 cursor-pointer'
  } else if (clickable) {
    surfaceTone =
      'bg-surface-layout-2 cursor-pointer hover:bg-surface-raised hover:shadow-elevation-1 focus-within:bg-surface-raised focus-within:shadow-elevation-1'
  } else {
    surfaceTone = 'bg-surface-layout-2'
  }
  const surface = `group relative rounded-xl overflow-hidden transition-all ${surfaceTone}${className ? ` ${className}` : ''}`

  // Interactive props are spread (not literal attributes) so a clickable card
  // carries a real button role, tab stop and Enter/Space handler at runtime —
  // keyboard-accessible — without asserting a `<button>` element that could not
  // legally wrap the card's own inner buttons.
  const interactive =
    clickable || (selectable && onClick)
      ? {
          role: 'button',
          tabIndex: 0,
          'aria-pressed': selectable ? selected : undefined,
          onClick,
          onKeyDown: (e: ReactKeyboardEvent) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              onClick?.()
            }
          },
        }
      : {}

  const dataAttrs = {
    'data-testid': dataTestid,
    'data-query-hash': dataQueryHash,
    'data-cache-id': dataCacheId,
  }

  // Escape hatch: transient inline state owns a padded surface.
  if (children) {
    return (
      <div {...dataAttrs} className={surface} {...interactive}>
        <div className="p-4">{children}</div>
      </div>
    )
  }

  const titleRow =
    title || badges ? (
      <HStack className="gap-2 items-center flex-wrap min-w-0">
        {title}
        {badges}
      </HStack>
    ) : null

  const leadingNode = selectable ? (
    <QueryCardCheckbox selected={selected} />
  ) : (
    leading
  )

  return (
    <div {...dataAttrs} className={surface} {...interactive}>
      {/* One copy affordance — pinned top-right, always visible. The `pr-9`
          below reserves the gutter so no SQL line runs under it. */}
      {/* Copy must never double as the card's click/select action. */}
      {/* biome-ignore lint/a11y/noStaticElementInteractions: propagation
          barrier only — the interactive element is the CopyButton inside. */}
      <div
        className="absolute top-2 right-2 z-10"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        <CopyButton text={sql} />
      </div>

      <div className="flex gap-3 items-start px-4 pt-4 pb-3">
        {leadingNode}

        <VStack className="gap-2 items-stretch min-w-0 flex-1 pr-9">
          {titleRow}
          {/* SQL sits directly in the card: full, highlighted, wrapped, uniform.
              `title={sql}` is the stable DOM hook for the card's e2e locators. */}
          <SqlTokens sql={sql} title={sql} />
        </VStack>
      </div>

      {/* Footer — the muted stat line (left) and actions (right) share ONE row.
          Present only when there is a meta line or an action to show. */}
      <Show when={!!(meta || actions)}>
        <div className="flex items-center justify-between gap-3 flex-wrap px-4 pb-3.5">
          <div className="min-w-0 flex-1 text-mono-small font-mono text-content-layout-3 break-words">
            {meta}
          </div>
          <Show when={!!actions}>
            <HStack className="gap-2 items-center shrink-0">{actions}</HStack>
          </Show>
        </div>
      </Show>

      {/* Additive detail slot — full width, inside the card, below the footer.
          Callers own its internal separator/padding. Absent for every consumer
          that omits `expansion`, so their DOM is unchanged. */}
      <Show when={!!expansion}>
        <div className="px-4 pb-4">{expansion}</div>
      </Show>
    </div>
  )
}

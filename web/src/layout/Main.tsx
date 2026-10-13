import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { Scrollable } from '@rs/ui-new/scrollable'
import { useLayoutEffect, useRef } from 'react'
import type { RouteLayout } from './routeLayout'

/** Titles for the pages that still carry their own heading under the shell's bar. */
const LEGACY_TITLES: Record<string, string> = {
  '/database': 'Database',
  '/scan': 'Code scan',
  '/cache': 'Benchmarks',
  '/benchmark': 'Benchmarks',
  '/schema': 'Schema',
  '/guards': 'Guards',
  '/onboarding': 'Get started',
}

export function legacyTitle(pathname: string): string {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
  if (LEGACY_TITLES[path]) return LEGACY_TITLES[path]
  if (path.startsWith('/database')) return 'Database'
  return 'Not found'
}

interface MainProps {
  children: React.ReactNode
  /** The page's path: a new page starts at the top; search params alone keep the place. */
  scrollKey?: string
  layout?: RouteLayout
  /** The bar title for a `legacy` route. */
  fallbackTitle?: string
}

/**
 * The canvas: the one rounded surface inset 12px from the page tone, with the
 * sidebar's right edge as its left margin. It scrolls the page; a `split`
 * route gets the bare area instead and draws its own panels.
 */
export function Main({
  children,
  scrollKey,
  layout = 'legacy',
  fallbackTitle = '',
}: MainProps) {
  const canvas = useRef<HTMLDivElement>(null)
  const shownKey = useRef(scrollKey)
  useLayoutEffect(() => {
    if (shownKey.current === scrollKey) return
    shownKey.current = scrollKey
    if (canvas.current) canvas.current.scrollTop = 0
  }, [scrollKey])

  if (layout === 'split')
    return (
      <main
        id="main-content"
        data-layout="split"
        className="mt-[max(--spacing(3),var(--titlebar-right-height))] mr-3 mb-3 flex min-h-0 min-w-0 flex-1 flex-col"
      >
        {children}
      </main>
    )

  return (
    <main
      id="main-content"
      data-layout={layout}
      className="relative mt-[max(--spacing(3),var(--titlebar-right-height))] mr-3 mb-3 flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-canvas border border-border-layout-soft bg-surface-layout-1"
    >
      <Scrollable rootClassName="min-h-0 flex-1" viewportRef={canvas}>
        {layout === 'legacy' ? (
          <>
            <PageBar title={fallbackTitle} titleAs="p" />
            <PageContainer className="pt-9 pb-24">{children}</PageContainer>
          </>
        ) : (
          children
        )}
      </Scrollable>
    </main>
  )
}

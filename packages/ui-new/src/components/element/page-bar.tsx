import { cn } from '@rs/tailwind-base'
import type { ReactNode } from 'react'
import { PageContainer } from './page-container'
import { PageNoticeSlot } from './page-notices'
import { Text } from './text'

export interface PageBarProps {
  /** The page name, or a breadcrumb on a detail page. */
  title: ReactNode
  /**
   * The page name is the route's level-1 heading. A shell rendering a bar on
   * behalf of a page that still carries its own heading passes `p`.
   */
  titleAs?: 'h1' | 'p'
  /** A mark before the title, outside the heading: a state indicator. */
  leading?: ReactNode
  /** The third-tone group beside the title: a date, a state, a count. */
  meta?: ReactNode
  /** The actions at the right of the title row. */
  actions?: ReactNode
  /** The view tabs, rendered on the content axis under the title row. */
  tabs?: ReactNode
  className?: string
}

/**
 * The bar every page opens with: a 64px title row flush with the top of the
 * canvas, its bottom hairline spanning the canvas edge to edge while the title,
 * meta, actions and tabs sit on the content axis. The shell's global notices
 * (`PageNotices`) show directly under it.
 */
export const PageBar = ({
  title,
  titleAs = 'h1',
  leading,
  meta,
  actions,
  tabs,
  className,
}: PageBarProps) => (
  <>
    <header
      data-page-bar
      className={cn(
        'border-b border-border-layout-2 [-webkit-app-region:drag]',
        className
      )}
    >
      <PageContainer className="flex min-h-16 items-center gap-3.5">
        {leading && (
          <div className="flex items-center [-webkit-app-region:no-drag]">
            {leading}
          </div>
        )}
        <Text as={titleAs} level="headline-3" className="text-content-layout-1">
          {title}
        </Text>
        {meta && (
          <div className="flex items-center gap-2.5 text-body-small text-content-layout-3">
            {meta}
          </div>
        )}
        {actions && (
          <div className="ml-auto flex items-center gap-2 [-webkit-app-region:no-drag]">
            {actions}
          </div>
        )}
      </PageContainer>
      {tabs && (
        <PageContainer className={cn('-mt-2', '[-webkit-app-region:no-drag]')}>
          {tabs}
        </PageContainer>
      )}
    </header>
    <PageNoticeSlot />
  </>
)

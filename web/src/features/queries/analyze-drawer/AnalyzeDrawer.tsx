import { Drawer, DrawerContentContainer } from '@rs/ui-new/drawer'
import { lazy, Suspense } from 'react'
import type { AnalyzeDrawerContentProps } from './AnalyzeDrawerContent'

// The analysis presentation pulls in the SQL editor stack, so it stays behind
// its own chunk: a Query Library that nobody opens an analysis on never pays
// for it. Same split as cloud's view-query-details drawer.
const AnalyzeDrawerContent = lazy(() =>
  import('./AnalyzeDrawerContent').then((module) => ({
    default: module.AnalyzeDrawerContent,
  }))
)

type AnalyzeDrawerProps = Omit<AnalyzeDrawerContentProps, 'link'> & {
  link: AnalyzeDrawerContentProps['link'] | null
}

/**
 * Thin shell for the analyze drawer. Open state is the caller's URL, so the
 * drawer's own dismissal (Esc, backdrop, close button) reports up rather than
 * closing behind the URL's back.
 */
export function AnalyzeDrawer({ link, ...props }: AnalyzeDrawerProps) {
  return (
    <Drawer
      open={Boolean(link)}
      onOpenChange={(next) => {
        if (!next) props.onClose()
      }}
      direction="right"
    >
      <DrawerContentContainer>
        {link ? (
          <Suspense>
            <AnalyzeDrawerContent link={link} {...props} />
          </Suspense>
        ) : null}
      </DrawerContentContainer>
    </Drawer>
  )
}

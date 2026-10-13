'use client'

import * as RadixPortal from '@radix-ui/react-portal'
import { cn } from '@rs/tailwind-base'
import {
  createContext,
  type ReactNode,
  useContext,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { pageContainerStyles } from './page-container'

interface NoticeHost {
  /** Created once and adopted by the slot on screen. */
  element: HTMLDivElement
  /** The layout of the slot that holds the element. */
  layout: string
  setLayout: (layout: string) => void
}

// The shell renders its global notices into one element that the page on screen adopts
// under its bar. The element never changes, so the notices keep their state (and their
// effects keep running) while the page under them changes.
const HostContext = createContext<NoticeHost | null>(null)

const defaultLayout = pageContainerStyles({ class: 'pt-9 -mb-2' })

export function PageNoticesProvider({ children }: { children: ReactNode }) {
  const [element] = useState(() => {
    if (typeof document === 'undefined') return null
    const created = document.createElement('div')
    // Boxless, like the slot, so an empty notice column adds no gap to its page.
    created.style.display = 'contents'
    return created
  })
  const [layout, setLayout] = useState(defaultLayout)
  const host = useMemo(
    () => (element ? { element, layout, setLayout } : null),
    [element, layout]
  )
  return <HostContext.Provider value={host}>{children}</HostContext.Provider>
}

/**
 * Global notices for the page on screen. They render in the page's notice slot (under
 * its PageBar), one line each; with no slot on screen they stay mounted but unseen.
 */
export function PageNotices({ children }: { children: ReactNode }) {
  const host = useContext(HostContext)
  if (!host) return <>{children}</>
  return (
    <RadixPortal.Root
      container={host.element}
      className={cn('flex flex-col gap-2 empty:hidden', host.layout)}
    >
      {children}
    </RadixPortal.Root>
  )
}

export interface PageNoticeSlotProps {
  /** Layout of the notice column; defaults to the page's content axis. */
  className?: string
}

/**
 * Where a page shows the global notices. PageBar renders one under its hairline; a page
 * without a PageBar places its own. Takes no space while there is nothing to show.
 */
export function PageNoticeSlot({ className }: PageNoticeSlotProps) {
  const host = useContext(HostContext)
  const slot = useRef<HTMLDivElement>(null)
  const element = host?.element
  const setLayout = host?.setLayout
  useLayoutEffect(() => {
    const parent = slot.current
    if (!element || !parent || !setLayout) return
    setLayout(className ?? defaultLayout)
    parent.appendChild(element)
    return () => {
      if (element.parentNode === parent) parent.removeChild(element)
    }
  }, [element, setLayout, className])
  if (!host) return null
  return <div ref={slot} data-page-notices="" className="contents" />
}

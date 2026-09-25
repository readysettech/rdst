// The daily snapshot's section bar (FEEDBACK_ROUND4 R3): sticks to the top while the day
// scrolls, marks the section being read, and jumps to a section on click. On a phone it
// scrolls sideways and keeps the current section in view. It never prints.
import { cn } from '@rs/tailwind-base'
import { Pressable } from '@rs/ui-new/pressable'
import { useCallback, useEffect, useRef, useState } from 'react'
import { activeSection, type ReportSection } from './snapshotModel'

/** How far below the bar a section's heading marks it as the one being read. */
const READING_GAP = 160

function scrollParent(element: HTMLElement | null): HTMLElement | null {
  for (let node = element?.parentElement; node; node = node.parentElement) {
    const { overflowY } = window.getComputedStyle(node)
    if (overflowY === 'auto' || overflowY === 'scroll') return node
  }
  return null
}

export function ReportSectionNav({ sections }: { sections: ReportSection[] }) {
  const navRef = useRef<HTMLElement>(null)
  const stripRef = useRef<HTMLDivElement>(null)
  const [active, setActive] = useState<string | null>(sections[0]?.id ?? null)
  // After a click the bar holds the picked section until the smooth scroll settles.
  const held = useRef<{ id: string; timer: number } | null>(null)
  const ids = sections.map((section) => section.id).join(' ')

  const measure = useCallback(() => {
    const nav = navRef.current
    if (!nav) return
    const scroller = scrollParent(nav)
    const view = scroller ? scroller.clientHeight : window.innerHeight
    const line =
      nav.getBoundingClientRect().bottom + Math.min(READING_GAP, view / 4)
    const tops = sections.flatMap((section) => {
      const element = document.getElementById(`${section.id}-section`)
      return element
        ? [{ id: section.id, top: element.getBoundingClientRect().top }]
        : []
    })
    // Scrolled all the way down: the last sections may never reach the reading line.
    const atBottom = scroller
      ? scroller.scrollTop > 0 &&
        scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4
      : window.scrollY > 0 &&
        window.innerHeight + window.scrollY >=
          document.documentElement.scrollHeight - 4
    const next = activeSection(tops, line, atBottom)
    if (held.current) {
      window.clearTimeout(held.current.timer)
      held.current.timer = window.setTimeout(() => {
        held.current = null
        measure()
      }, 160)
      return
    }
    setActive(next)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids])

  useEffect(() => {
    const scroller = scrollParent(navRef.current)
    const target: HTMLElement | Window = scroller ?? window
    let frame = 0
    const onScroll = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(measure)
    }
    measure()
    target.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      cancelAnimationFrame(frame)
      target.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [measure])

  // Keep the current chip visible in the sideways strip.
  useEffect(() => {
    const strip = stripRef.current
    const chip = strip?.querySelector<HTMLElement>(`[data-section="${active}"]`)
    if (!strip || !chip) return
    const left = chip.offsetLeft - 16
    const right = chip.offsetLeft + chip.offsetWidth + 16 - strip.clientWidth
    if (strip.scrollLeft > left) strip.scrollTo?.({ left, behavior: 'smooth' })
    else if (strip.scrollLeft < right)
      strip.scrollTo?.({ left: right, behavior: 'smooth' })
  }, [active])

  const jump = (id: string) => {
    if (held.current) window.clearTimeout(held.current.timer)
    held.current = {
      id,
      timer: window.setTimeout(() => {
        held.current = null
        measure()
      }, 900),
    }
    setActive(id)
    document
      .getElementById(`${id}-section`)
      ?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }

  if (sections.length < 2) return null
  return (
    <nav
      ref={navRef}
      aria-label="Report sections"
      data-testid="report-section-nav"
      className="sticky top-0 z-20 -mx-6 border-b border-border-layout-1 bg-surface-layout-2/95 backdrop-blur-md print:hidden"
    >
      <div
        ref={stripRef}
        className="flex gap-1 overflow-x-auto px-6 py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {sections.map((section) => {
          const current = section.id === active
          return (
            <Pressable
              key={section.id}
              data-section={section.id}
              aria-current={current ? 'location' : undefined}
              onClick={() => jump(section.id)}
              className={cn(
                'inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1 text-label-medium whitespace-nowrap transition-colors duration-fast',
                current
                  ? 'bg-surface-primary-soft text-content-primary-soft'
                  : 'text-content-layout-2 hover:bg-surface-layout-2 hover:text-content-layout-1'
              )}
            >
              {section.label}
              {section.count ? (
                <span
                  className={cn(
                    'text-caption tabular-nums',
                    current
                      ? 'text-content-primary-soft'
                      : 'text-content-layout-3'
                  )}
                >
                  {section.count}
                </span>
              ) : null}
            </Pressable>
          )
        })}
      </div>
    </nav>
  )
}

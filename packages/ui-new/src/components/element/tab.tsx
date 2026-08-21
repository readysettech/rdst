'use client'

import { cn, tv, type VariantProps } from '@rs/tailwind-base'
import {
  Link,
  type LinkComponentProps,
  type RegisteredRouter,
} from '@tanstack/react-router'
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
} from 'react'
import type { WithClassName } from '../../helpers/types'
import { m } from '../../motion/motion'
import { getTransition } from '../../motion/transition'
import { Icon, type IconStrokeName } from '../svg/icon'
import { Skeleton } from './skeleton'
import { HStack } from './stack'

// Page-level view/route tabs: a flat row of labels with an animated underline
// tracking the active one. This is navigation between views/routes — for an
// in-place behavior or filter switch use `SegmentedControl` instead. Link tabs
// use a shared-layout animation; button tab lists own one persistent indicator
// so each row has a local coordinate system, including inside portals.

const tabItemStyles = tv({
  base: [
    'relative flex items-center   justify-start gap-2 rounded-none px-3 py-2 cursor-pointer',
    'px-0 py-1',
    'h-12 w-auto text-label-medium text-content-layout-2',
    'transition-[background,color] duration-slower ease-base',
    'select-none outline-none',
    'hover:text-content-primary-soft',
    'focus:text-content-primary-soft',
    'disabled:pointer-events-none disabled:opacity-50',
    // Unavailable tabs stay focusable and hoverable, so the reason they give
    // can actually be read.
    'aria-disabled:cursor-not-allowed aria-disabled:opacity-50',
    'aria-disabled:hover:text-content-layout-2',
  ],
})

type RegisterActiveTab = (
  element: HTMLButtonElement,
  layoutPrefix: string
) => void

const TabListContext = createContext<RegisterActiveTab | null>(null)

export type TabBaseItemProps = {
  leftIcon?: IconStrokeName
  layoutPrefix: string
  rightIcon?: IconStrokeName
  label: string
  classMerge?: string
} & VariantProps<typeof tabItemStyles> &
  WithClassName

export type TabItemProps = TabBaseItemProps &
  LinkComponentProps<'a', RegisteredRouter, string, string>

export const TabItem = ({
  className,
  label,
  leftIcon,
  rightIcon,
  to,
  activeProps,
  layoutPrefix,
  ...restProps
}: TabItemProps) => {
  return (
    <Link
      to={to}
      className={tabItemStyles({ className })}
      activeProps={{
        className: cn(
          'text-content-primary-soft',
          '[&_p]:text-content-primary-soft',
          '[&_svg]:text-content-primary-soft'
        ),
        ...activeProps,
      }}
      {...restProps}
    >
      {({ isActive }) => {
        return (
          <>
            <HStack>
              {leftIcon && <Icon name={leftIcon} label="" aria-hidden="true" />}
              {label}
            </HStack>
            {rightIcon && <Icon name={rightIcon} label="" aria-hidden="true" />}
            {isActive && (
              <m.div
                layoutId={`${layoutPrefix}-tab-active-indicator`}
                className="absolute bottom-0 right-0 h-[2px] w-full bg-content-primary-soft rounded-full"
              />
            )}
          </>
        )
      }}
    </Link>
  )
}

export type TabButtonItemProps = {
  onClick: () => void
  active: boolean
  /**
   * The view exists but cannot be opened yet: the tab is announced as disabled
   * and ignores activation, while `hint` says what would unlock it.
   */
  disabled?: boolean
  /** Why the tab is unavailable, shown on hover. */
  hint?: string
  /** Tab id — wire it to the panel's `aria-labelledby`. */
  id?: string
  /** Id of the panel this tab controls. */
  'aria-controls'?: string
} & TabBaseItemProps

export const TabItemButton = ({
  label,
  leftIcon,
  rightIcon,
  active,
  layoutPrefix,
  onClick,
  className,
  disabled,
  hint,
  id,
  'aria-controls': ariaControls,
  ...restProps
}: TabButtonItemProps) => {
  const transition = getTransition()
  const registerActiveTab = useContext(TabListContext)
  const buttonRef = useRef<HTMLButtonElement>(null)

  useLayoutEffect(() => {
    if (active && buttonRef.current) {
      registerActiveTab?.(buttonRef.current, layoutPrefix)
    }
  }, [active, layoutPrefix, registerActiveTab])

  return (
    <button
      ref={buttonRef}
      type="button"
      role="tab"
      id={id}
      aria-selected={active}
      aria-controls={ariaControls}
      aria-disabled={disabled || undefined}
      title={hint}
      data-tab-layout-prefix={layoutPrefix}
      onClick={disabled ? undefined : onClick}
      className={tabItemStyles({ className })}
      {...restProps}
    >
      <HStack>
        {leftIcon && <Icon name={leftIcon} label="" aria-hidden="true" />}
        {label}
      </HStack>
      {rightIcon && <Icon name={rightIcon} label="" aria-hidden="true" />}
      {active && !registerActiveTab && (
        <m.div
          layoutId={`${layoutPrefix}-tab-active-indicator`}
          className="absolute bottom-0 right-0 h-[2px] w-full bg-content-primary-soft rounded-full"
          transition={transition}
        />
      )}
    </button>
  )
}

export const TabItemSkeleton = () => (
  <HStack className="items-center">
    <Skeleton className="h-8 w-24" />
  </HStack>
)

export type TabListProps = WithClassName & {
  /** When set, the row becomes a WAI-ARIA `tablist` named by this label. */
  'aria-label'?: string
  children: ReactNode
}

export const TabList = ({
  'aria-label': ariaLabel,
  className,
  children,
}: TabListProps) => {
  const transition = getTransition()
  const listRef = useRef<HTMLDivElement>(null)
  const observerRef = useRef<ResizeObserver | null>(null)
  const activeTabRef = useRef<{
    element: HTMLButtonElement
    layoutPrefix: string
  } | null>(null)
  const [indicator, setIndicator] = useState<{
    layoutPrefix: string
    x: number
    width: number
  } | null>(null)

  const registerActiveTab = useCallback<RegisterActiveTab>(
    (element, layoutPrefix) => {
      const list = listRef.current
      if (!list) return

      activeTabRef.current = { element, layoutPrefix }

      const measure = () => {
        const currentList = listRef.current
        const activeTab = activeTabRef.current
        if (!currentList || !activeTab) return

        const nextIndicator = {
          layoutPrefix: activeTab.layoutPrefix,
          // offset* values stay in the tab list's local coordinate system and
          // are unaffected by an ancestor modal's entry scale transform.
          x: activeTab.element.offsetLeft + currentList.scrollLeft,
          width: activeTab.element.offsetWidth,
        }

        setIndicator((currentIndicator) => {
          if (
            currentIndicator?.layoutPrefix === nextIndicator.layoutPrefix &&
            currentIndicator.x === nextIndicator.x &&
            currentIndicator.width === nextIndicator.width
          ) {
            return currentIndicator
          }

          return nextIndicator
        })
      }

      measure()
      observerRef.current?.disconnect()

      if (typeof ResizeObserver !== 'undefined') {
        const observer = new ResizeObserver(measure)
        observer.observe(list)
        observer.observe(element)
        observerRef.current = observer
      }
    },
    []
  )

  useLayoutEffect(
    () => () => {
      observerRef.current?.disconnect()
    },
    []
  )

  useLayoutEffect(() => {
    const activeTab = listRef.current?.querySelector<HTMLButtonElement>(
      '[role="tab"][aria-selected="true"]'
    )
    const layoutPrefix = activeTab?.dataset.tabLayoutPrefix

    if (activeTab && layoutPrefix) {
      registerActiveTab(activeTab, layoutPrefix)
    }
  }, [children, registerActiveTab])

  return (
    <TabListContext.Provider value={registerActiveTab}>
      <HStack
        ref={listRef}
        role={ariaLabel ? 'tablist' : undefined}
        aria-label={ariaLabel}
        className={cn(
          'relative w-full gap-6 border-b-(length:--border-base) border-border-layout-1',
          className
        )}
      >
        {children}
        {indicator && (
          <m.div
            key={indicator.layoutPrefix}
            aria-hidden="true"
            initial={false}
            animate={{ x: indicator.x, width: indicator.width }}
            className="pointer-events-none absolute bottom-0 left-0 h-[2px] bg-content-primary-soft rounded-full"
            transition={transition}
          />
        )}
      </HStack>
    </TabListContext.Provider>
  )
}

export const TabListSkeleton = () => (
  <HStack className="gap-6 pb-2">
    <TabItemSkeleton />
    <TabItemSkeleton />
    <TabItemSkeleton />
  </HStack>
)

import { cn } from '@rs/tailwind-base'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { IconTile } from '@rs/ui-new/icon-tile'
import { m } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { TabItemButton, TabList } from '@rs/ui-new/tab'
import { Text } from '@rs/ui-new/text'
import type { ReactNode } from 'react'

export interface WorkspaceView<Value extends string> {
  value: Value
  label: string
  icon: IconStrokeName
}

interface WorkspaceLayoutProps<Value extends string> {
  title: string
  description: string
  icon: IconStrokeName
  views?: readonly WorkspaceView<Value>[]
  activeView?: Value
  layoutPrefix?: string
  panelId: string
  tabsLabel?: string
  onViewChange?: (view: Value) => void
  titleMeta?: ReactNode
  headerMeta?: ReactNode
  headerActions?: ReactNode
  headerDivider?: boolean
  showHeader?: boolean
  fullBleedTabs?: boolean
  fillViewport?: boolean
  children: ReactNode
}

/**
 * Shared anatomy for multi-view RDST workspaces.
 *
 * The layout owns page identity, URL-backed view navigation, and the active
 * panel boundary. Feature workspaces continue to own routing and pane content.
 */
export function WorkspaceLayout<Value extends string>({
  title,
  description,
  icon,
  views,
  activeView,
  layoutPrefix,
  panelId,
  tabsLabel,
  onViewChange,
  titleMeta,
  headerMeta,
  headerActions,
  headerDivider = false,
  showHeader = true,
  fullBleedTabs = false,
  fillViewport = false,
  children,
}: WorkspaceLayoutProps<Value>) {
  const hasViews = Boolean(
    views?.length && activeView && layoutPrefix && tabsLabel && onViewChange
  )

  return (
    <div
      className={cn(
        'w-full',
        fillViewport
          ? 'flex h-[calc(100dvh-6rem)] min-h-0 flex-col gap-6'
          : 'space-y-6'
      )}
    >
      {showHeader ? (
        <m.div
          className={cn(
            fillViewport && 'shrink-0',
            headerDivider && 'border-b border-border-layout-1 pb-6'
          )}
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <HStack className="items-center justify-between gap-4 flex-wrap">
            <HStack className="items-center gap-4 min-w-0">
              <IconTile icon={icon} />
              <VStack className="min-w-0 items-start gap-1">
                <HStack className="min-w-0 items-center gap-2 flex-wrap">
                  <Text
                    as="h1"
                    level="headline-3"
                    className="text-content-layout-1"
                  >
                    {title}
                  </Text>
                  {titleMeta}
                </HStack>
                <Text level="body-small" className="text-content-layout-3">
                  {description}
                </Text>
                {headerMeta ? <div className="pt-1">{headerMeta}</div> : null}
              </VStack>
            </HStack>
            {headerActions}
          </HStack>
        </m.div>
      ) : null}

      {hasViews ? (
        <div
          className={cn(
            fillViewport && 'shrink-0',
            fullBleedTabs && '-mx-6 border-b border-border-layout-1 px-6'
          )}
        >
          <TabList
            aria-label={tabsLabel}
            className={cn(fullBleedTabs && 'border-b-0')}
          >
            {views?.map((view) => (
              <TabItemButton
                key={view.value}
                layoutPrefix={layoutPrefix ?? ''}
                label={view.label}
                leftIcon={view.icon}
                active={view.value === activeView}
                id={`${panelId}-tab-${view.value}`}
                aria-controls={panelId}
                onClick={() => onViewChange?.(view.value)}
              />
            ))}
          </TabList>
        </div>
      ) : null}

      {hasViews ? (
        <div
          role="tabpanel"
          id={panelId}
          aria-labelledby={`${panelId}-tab-${activeView}`}
          className={cn(fillViewport && 'min-h-0 flex-1')}
        >
          {children}
        </div>
      ) : (
        <section id={panelId} aria-label={title}>
          {children}
        </section>
      )}
    </div>
  )
}

import { cn } from '@rs/tailwind-base'
import { Text } from '@rs/ui-new/text'
import type { ReactNode } from 'react'

/** One group of settings: its name in a left column, its rows at the right. */
export function SettingsGroup({
  id,
  title,
  action,
  children,
  className,
}: {
  id?: string
  title: string
  /** The group's one ghost action, at the right above its rows. */
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section
      id={id}
      aria-labelledby={id ? `${id}-title` : undefined}
      className={cn(
        'grid scroll-mt-6 grid-cols-[240px_1fr] gap-12 border-t border-border-layout-2 py-6 pb-7 first:border-t-0 first:pt-0',
        className
      )}
    >
      <h2
        id={id ? `${id}-title` : undefined}
        className="mt-2.5 text-headline-5 text-content-layout-1"
      >
        {title}
      </h2>
      <div className="min-w-0">
        {action && <div className="flex justify-end pb-1">{action}</div>}
        {children}
      </div>
    </section>
  )
}

/** A setting: its name, one line under it, and its control at the right. */
export function SettingsRow({
  title,
  line,
  children,
}: {
  title: ReactNode
  line?: ReactNode
  children?: ReactNode
}) {
  return (
    <div className="flex items-center gap-3.5 border-b border-border-layout-2 py-3.5 last:border-b-0">
      <div className="min-w-0 flex-1">
        <Text as="div" level="body-medium" className="text-content-layout-1">
          {title}
        </Text>
        {line && (
          <Text
            as="div"
            level="caption"
            className="mt-0.5 text-content-layout-3"
          >
            {line}
          </Text>
        )}
      </div>
      {children}
    </div>
  )
}

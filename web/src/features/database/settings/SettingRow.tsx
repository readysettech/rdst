// One preference row: a 15px title, a 13px line under it, the control at the right,
// and a hairline to the next row.
import { cn } from '@rs/tailwind-base'
import { Text } from '@rs/ui-new/text'
import type { ReactNode } from 'react'

export function SettingRow({
  title,
  line,
  control,
  last = false,
  className,
}: {
  title: string
  line?: ReactNode
  control: ReactNode
  /** The last row of its group carries no hairline. */
  last?: boolean
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex items-center gap-3.5 py-3.5',
        !last && 'border-b border-border-layout-2',
        className
      )}
    >
      <div className="min-w-0 flex-1">
        <Text as="p" level="body-medium" className="text-content-layout-1">
          {title}
        </Text>
        {line && (
          <Text as="p" level="caption" className="mt-0.5 text-content-layout-3">
            {line}
          </Text>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">{control}</div>
    </div>
  )
}

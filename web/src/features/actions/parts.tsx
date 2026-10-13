// Small pieces the Plan and Applied views share: section headings, titled blocks, quiet
// expandable rows, and a copy that says it copied.
import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import { Reveal } from './Reveal'

/** A text link underlined by a hairline, as across the redesigned pages. */
export const hairlineLink =
  'cursor-pointer border-b border-border-layout-2 hover:border-content-layout-2'

/** Copies text and reports it for two seconds. */
export function useCopy(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    []
  )
  const copy = useCallback((text: string) => {
    void navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopied(true)
        if (timer.current) clearTimeout(timer.current)
        timer.current = setTimeout(() => setCopied(false), 2000)
      })
      .catch(() => setCopied(false))
  }, [])
  return [copied, copy]
}

/** A section's heading: the title, its count, and its quiet meta beside it. */
export function SectionTitle({
  id,
  title,
  count,
  meta,
  quiet = false,
}: {
  id: string
  title: string
  count?: number
  meta?: string
  /** A section with nothing in it reads in the second tone. */
  quiet?: boolean
}) {
  return (
    <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <h2
        id={id}
        className={cn(
          'text-headline-5',
          quiet ? 'font-normal text-content-layout-2' : 'text-content-layout-1'
        )}
      >
        {title}
      </h2>
      {count != null ? (
        <span className="-ml-1 text-headline-5 font-normal tabular-nums text-content-layout-3">
          {count}
        </span>
      ) : null}
      {meta ? (
        <Text as="span" level="caption" className="text-content-layout-3">
          {meta}
        </Text>
      ) : null}
    </div>
  )
}

/** A titled block inside an opened change: a caption over its content. */
export function DetailBlock({
  title,
  children,
  className,
}: {
  title: string
  children: ReactNode
  className?: string
}) {
  return (
    <div className={className}>
      <Text as="h3" level="caption" className="mb-2 text-content-layout-3">
        {title}
      </Text>
      {children}
    </div>
  )
}

/** A quiet row at the foot of a view: a name, a line under it, and what it opens. */
export function QuietRow({
  name,
  sub,
  children,
  testId,
}: {
  name: string
  sub?: ReactNode
  /** What the row opens; a row without it is a plain statement. */
  children?: ReactNode
  testId?: string
}) {
  const [open, setOpen] = useState(false)
  const label = (
    <span className="min-w-0">
      <Text
        as="span"
        level="body-medium"
        className="block text-content-layout-1"
      >
        {name}
      </Text>
      {sub ? (
        <Text
          as="span"
          level="caption"
          className="mt-0.5 block text-content-layout-3"
        >
          {sub}
        </Text>
      ) : null}
    </span>
  )
  return (
    <div className="border-b border-border-layout-2" data-testid={testId}>
      {children ? (
        <Pressable
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="flex min-h-15 w-full items-center justify-between gap-4 py-3 text-left"
        >
          {label}
          <Icon
            name="chevron-down"
            label=""
            aria-hidden="true"
            size="small"
            className={cn(
              'shrink-0 text-content-layout-3 transition-transform duration-fast',
              open && 'rotate-180'
            )}
          />
        </Pressable>
      ) : (
        <div className="flex min-h-15 items-center py-3">{label}</div>
      )}
      {children ? (
        <Reveal open={open}>
          <div className="pb-3">{children}</div>
        </Reveal>
      ) : null}
    </div>
  )
}

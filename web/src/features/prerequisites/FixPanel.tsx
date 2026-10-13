// How to fix one failed prerequisite: a one-line summary, copyable SQL, console
// steps, copyable commands, a restart warning and the provider's guide.
import { cn } from '@rs/tailwind-base'
import { CopyButton } from '@rs/ui-new/copy-button'
import { Icon } from '@rs/ui-new/icon'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { SqlTokens } from '@/components/SqlTokens'
import type { PrereqFix } from './api'

export function CodeBlock({
  code,
  kind,
}: {
  code: string
  kind: 'sql' | 'shell'
}) {
  return (
    <div className="group/code flex items-start gap-2 rounded-control border border-border-layout-1 bg-surface-layout-2 py-2 pl-3 pr-1.5 transition-colors duration-fast ease-base hover:border-border-layout-2">
      <Scrollable
        rootClassName="min-w-0 flex-1"
        className="max-h-64 py-0.5 font-mono text-mono-small leading-relaxed text-content-layout-1"
      >
        {kind === 'sql' ? (
          <SqlTokens
            sql={code}
            className="whitespace-pre-wrap [overflow-wrap:anywhere]"
          />
        ) : (
          <code className="whitespace-pre-wrap break-all">{code}</code>
        )}
      </Scrollable>
      <CopyButton text={code} label="Copy" />
    </div>
  )
}

export function Steps({ steps, start }: { steps: string[]; start: number }) {
  if (steps.length === 0) return null
  return (
    <ol className="space-y-1.5">
      {steps.map((step, index) => (
        <li key={step} className="flex items-start gap-2.5">
          <span
            aria-hidden="true"
            className="mt-px grid h-5 w-5 shrink-0 place-items-center rounded-pill bg-surface-layout-2 text-label-extra-small text-content-layout-2"
          >
            {start + index}
          </span>
          <Text
            level="body-small"
            className="min-w-0 text-content-layout-2 [overflow-wrap:anywhere]"
          >
            {step}
          </Text>
        </li>
      ))}
    </ol>
  )
}

export function FixPanel({
  fix,
  database,
  className,
}: {
  fix: PrereqFix
  database?: string
  className?: string
}) {
  return (
    <div className={cn('space-y-3', className)}>
      <div className="flex flex-wrap items-center gap-2">
        <Text
          as="p"
          level="body-small"
          className="min-w-0 text-content-layout-1 [overflow-wrap:anywhere]"
        >
          {fix.summary}
        </Text>
        {fix.restart && (
          <Tag
            size="small"
            variant="warning"
            modifier="outline"
            label="Needs a restart"
          />
        )}
      </div>

      <Steps steps={fix.steps} start={1} />
      {fix.sql.length > 0 && (
        <div className="space-y-1.5">
          <Text as="p" level="caption" className="text-content-layout-3">
            {database ? `Run in ${database}` : 'Run this SQL'}
          </Text>
          {fix.sql.map((statement) => (
            <CodeBlock key={statement} code={statement} kind="sql" />
          ))}
        </div>
      )}

      {fix.commands.length > 0 && (
        <div className="space-y-1.5">
          {fix.commands.map((command) => (
            <CodeBlock key={command} code={command} kind="shell" />
          ))}
        </div>
      )}

      <Steps steps={fix.after ?? []} start={fix.steps.length + 1} />

      {fix.note && (
        <Text
          as="p"
          level="caption"
          className="text-content-layout-3 [overflow-wrap:anywhere]"
        >
          {fix.note}
        </Text>
      )}

      {fix.docs_url && (
        <a
          href={fix.docs_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 rounded-control text-label-small text-content-layout-2 underline-offset-4 transition-colors duration-fast ease-base hover:text-content-layout-1 hover:underline focus-visible:shadow-focus focus-visible:outline-none"
        >
          Open the provider guide
          <Icon name="arrow-up-right" label="" size="small" />
        </a>
      )}
    </div>
  )
}

// A SQL block on the page tone: mono, keywords in the second series hue, nothing else. Long
// lines wrap under a hanging indent, with a break allowed after each dot of a qualified name.
import { cn } from '@rs/tailwind-base'
import { Fragment } from 'react'

const KEYWORDS =
  /\b(select|from|where|order by|group by|having|limit|offset|join|left join|inner join|on|and|or|not|in|as|create index concurrently|create index|create unique index|drop index concurrently|drop index|on|include|desc|asc|insert into|values|update|set|delete|count|distinct|with|using|alter system|null|is|between|like|ilike)\b/gi

/** The text split into keyword and plain runs, in order. */
export function sqlRuns(sql: string): { text: string; keyword: boolean }[] {
  const runs: { text: string; keyword: boolean }[] = []
  let last = 0
  for (const match of sql.matchAll(KEYWORDS)) {
    const at = match.index ?? 0
    if (at > last) runs.push({ text: sql.slice(last, at), keyword: false })
    runs.push({ text: match[0], keyword: true })
    last = at + match[0].length
  }
  if (last < sql.length) runs.push({ text: sql.slice(last), keyword: false })
  return runs
}

/** Plain text with a break allowed after each dot, so public.order_items can wrap. */
function Dotted({ text }: { text: string }) {
  const parts = text.split('.')
  return parts.map((part, index) => (
    <Fragment key={`${index}-${part}`}>
      {part}
      {index < parts.length - 1 ? (
        <>
          .<wbr />
        </>
      ) : null}
    </Fragment>
  ))
}

export function SqlCode({
  sql,
  className,
  testId,
}: {
  sql: string
  className?: string
  testId?: string
}) {
  const lines = sql.split('\n')
  return (
    <pre
      data-testid={testId}
      className={cn(
        'rounded-control bg-surface-layout-2 px-3.5 py-3 text-mono-medium leading-relaxed wrap-anywhere whitespace-pre-wrap text-content-layout-1',
        className
      )}
    >
      <code>
        {lines.map((line, at) => (
          <span
            key={`${at}-${line}`}
            data-sql-line
            className="block"
            style={{ paddingLeft: '2ch', textIndent: '-2ch' }}
          >
            {sqlRuns(line).map((run, index) =>
              run.keyword ? (
                <span key={`${index}-${run.text}`} className="text-series-2">
                  {run.text}
                </span>
              ) : (
                <Dotted key={`${index}-${run.text}`} text={run.text} />
              )
            )}
            {at < lines.length - 1 ? '\n' : null}
          </span>
        ))}
      </code>
    </pre>
  )
}

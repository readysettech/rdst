// The opened change, in the side pane the Database page opens a query in: what it saves and
// does, the evidence, the what-if, the SQL and its rollback, and the verbs in the footer: I did
// this, Set aside and Dismiss.
import { cn } from '@rs/tailwind-base'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Icon } from '@rs/ui-new/icon'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { SidePane } from '@rs/ui-new/side-pane'
import { Text } from '@rs/ui-new/text'
import { useToast } from '@rs/ui-new/use-toast'
import { Link } from '@tanstack/react-router'
import { useId, useState } from 'react'
import { SqlCode } from '@/features/database/queries/SqlCode'
import { type PlanCard, usePlanVerb } from '@/lib/monitor/actions'
import {
  MIGRATION_FORMATS,
  type MigrationFormat,
  migration,
  ms,
  paneMeta,
  pct,
  saves,
  speedup,
  stepList,
  whatIf,
  whatItDoes,
  writesNote,
} from './actionsModel'
import { rankMark } from './PlanRow'
import { DetailBlock, useCopy } from './parts'

export function PlanPane({
  card,
  now,
  onPrev,
  onNext,
  onClose,
}: {
  card: PlanCard
  now: number
  onPrev?: () => void
  onNext?: () => void
  onClose: () => void
}) {
  return (
    <SidePane
      aria-label={card.title}
      chip={
        <span
          data-testid="plan-pane-rank"
          className={cn(
            'inline-flex size-5.5 items-center justify-center rounded-control text-label-extra-small tabular-nums',
            rankMark(card.priority ?? 'later')
          )}
        >
          {card.rank}
        </span>
      }
      meta={paneMeta(card).map((fact) => <span key={fact}>{fact}</span>)}
      onPrev={onPrev}
      onNext={onNext}
      onClose={onClose}
      footer={<PlanVerbs card={card} now={now} />}
    >
      <PlanDetail card={card} />
    </SidePane>
  )
}

function PlanDetail({ card }: { card: PlanCard }) {
  const estimate = whatIf(card)
  const what = whatItDoes(card)
  const verb = card.kind === 'maintenance' ? 'steadies' : 'speeds up'
  const [copied, copy] = useCopy()
  const [rollbackCopied, copyRollback] = useCopy()
  const sqlNote = card.sql ? writesNote(card.sql) : null
  const rollbackNote = card.rollback_sql ? writesNote(card.rollback_sql) : null
  const steps = stepList(card.steps)
  const rewrite = card.type === 'rewrite_query'
  return (
    <div data-testid="plan-detail">
      <Text as="h2" level="headline-3" className="mb-5 text-content-layout-1">
        {card.title}
      </Text>
      <div className="grid grid-cols-2 gap-4">
        <DetailBlock title="Saves">
          <p className="tabular-nums" data-testid="plan-pane-saves">
            <span className="text-headline-2 text-content-layout-1">
              {saves(card.saves_ms_per_min)}
            </span>
            <span className="ml-1 text-caption text-content-layout-3">
              a min
            </span>
          </p>
        </DetailBlock>
        <DetailBlock title="What it does">
          <Text as="p" level="body-small" className="text-content-layout-1">
            {what.text}
          </Text>
          {what.sub ? (
            <Text
              as="p"
              level="caption"
              className="tabular-nums text-content-layout-3"
            >
              {what.sub}
            </Text>
          ) : null}
        </DetailBlock>
      </div>
      {card.why ? (
        <Text as="p" level="body-small" className="mt-6 text-content-layout-1">
          {card.why}
        </Text>
      ) : null}
      {card.evidence.length ? (
        <dl
          className="mt-6 grid grid-cols-2 gap-x-6 gap-y-3"
          data-testid="plan-evidence"
        >
          {card.evidence.slice(0, 4).map((row) => (
            <div key={String(row.label)} className="min-w-0 space-y-0.5">
              <dt className="text-caption text-content-layout-3">
                {String(row.label)}
              </dt>
              <dd className="truncate text-body-small tabular-nums text-content-layout-1">
                {String(row.value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {card.queries.length ? (
        <DetailBlock
          title={`Evidence, the ${card.queries.length === 1 ? 'query' : `${card.queries.length} queries`} it ${verb}`}
          className="mt-6"
        >
          <div className="space-y-3">
            {card.queries.map((query) => (
              <div key={query.key} className="flex items-center gap-3">
                <div className="min-w-0">
                  <Text
                    as="p"
                    level="body-small"
                    className="truncate text-content-layout-1"
                  >
                    {query.name}
                  </Text>
                  <Text
                    as="p"
                    level="caption"
                    className="tabular-nums text-content-layout-3"
                  >
                    {pct(query.share)} of time
                    {query.mean_ms != null ? `, ${ms(query.mean_ms)} mean` : ''}
                  </Text>
                </div>
                <Link
                  to="/database"
                  search={{ target: card.database, query: query.key }}
                  className="ml-auto inline-flex shrink-0 items-center gap-1 text-caption text-content-layout-2 hover:text-content-layout-1"
                >
                  Open query
                  <Icon
                    name="chevron-right"
                    label=""
                    aria-hidden="true"
                    size="small"
                  />
                </Link>
              </div>
            ))}
          </div>
        </DetailBlock>
      ) : card.summary ? (
        <Text as="p" level="body-small" className="mt-6 text-content-layout-2">
          {card.summary}
        </Text>
      ) : null}
      {estimate ? (
        <DetailBlock title={estimate.title} className="mt-6">
          <div className="grid grid-cols-2 gap-6" data-testid="plan-what-if">
            <div>
              <Text as="p" level="caption" className="text-content-layout-3">
                Today
              </Text>
              <Text
                as="p"
                level="headline-3"
                className="tabular-nums text-content-layout-1"
              >
                {ms(estimate.today)}
              </Text>
              <Text
                as="p"
                level="caption"
                className="truncate text-content-layout-3"
              >
                {estimate.query}
              </Text>
            </div>
            <div>
              <Text as="p" level="caption" className="text-content-layout-3">
                With the change
              </Text>
              <Text
                as="p"
                level="headline-3"
                className="tabular-nums text-content-layout-1"
              >
                {ms(estimate.after)}
              </Text>
              <Text
                as="p"
                level="caption"
                className="text-content-positive-soft"
              >
                {speedup(estimate.today, estimate.after)}
              </Text>
            </div>
          </div>
          <Text as="p" level="caption" className="mt-3 text-content-layout-3">
            {estimate.note}
          </Text>
        </DetailBlock>
      ) : null}
      {steps.length ? (
        <DetailBlock title="How to do it" className="mt-6">
          {steps.length === 1 ? (
            <Text as="p" level="body-small" className="text-content-layout-1">
              {steps[0]}
            </Text>
          ) : (
            <ol
              className="list-decimal space-y-1.5 pl-5 text-body-small text-content-layout-1"
              data-testid="plan-steps"
            >
              {steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          )}
        </DetailBlock>
      ) : null}
      {rewrite && card.original ? (
        <DetailBlock title="Your query" className="mt-6">
          <SqlCode sql={card.original} testId="plan-original-sql" />
        </DetailBlock>
      ) : null}
      {card.sql ? (
        <DetailBlock
          title={rewrite && card.original ? 'The rewrite' : 'SQL'}
          className="mt-6"
        >
          <SqlCode sql={card.sql} testId="plan-sql" />
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              modifier="outline"
              size="small"
              label={copied ? 'Copied' : 'Copy SQL'}
              onClick={() => copy(card.sql ?? '')}
            />
            <CopyAsMigration card={card} />
          </div>
          {sqlNote ? (
            <Text
              as="p"
              level="caption"
              className="mt-1.5 text-content-layout-3"
            >
              {sqlNote}
            </Text>
          ) : null}
        </DetailBlock>
      ) : null}
      {card.rollback_sql ? (
        <DetailBlock title="Rollback" className="mt-6">
          <SqlCode sql={card.rollback_sql} testId="plan-rollback-sql" />
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              modifier="outline"
              size="small"
              label={rollbackCopied ? 'Copied' : 'Copy rollback'}
              onClick={() => copyRollback(card.rollback_sql ?? '')}
            />
            {rollbackNote ? (
              <Text as="span" level="caption" className="text-content-layout-3">
                {rollbackNote}
              </Text>
            ) : null}
          </div>
        </DetailBlock>
      ) : null}
      {card.rows.length ? (
        <ul
          className="mt-6 space-y-1 text-body-small text-content-layout-2"
          data-testid="plan-rows"
        >
          {card.rows.map((row) => (
            <li key={`${String(row.name)}-${String(row.value)}`}>
              <span className="text-content-layout-1">{String(row.name)}</span>
              {': '}
              {String(row.value ?? '')}
              {row.detail ? ` (${String(row.detail)})` : ''}
            </li>
          ))}
        </ul>
      ) : null}
      {card.note ? (
        <Text as="p" level="caption" className="mt-6 text-content-layout-3">
          {card.note}
        </Text>
      ) : null}
    </div>
  )
}

function CopyAsMigration({ card }: { card: PlanCard }) {
  const [copied, copy] = useCopy()
  const [format, setFormat] = useState<MigrationFormat | null>(null)
  const label = MIGRATION_FORMATS.find((entry) => entry.value === format)?.label
  return (
    <Dropdown>
      <Dropdown.Trigger asChild>
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          icon="chevron-down"
          iconPosition="right"
          label={copied && label ? `Copied as ${label}` : 'Copy as migration'}
        />
      </Dropdown.Trigger>
      <Dropdown.Content
        align="start"
        className="w-48 min-w-48 rounded-panel bg-surface-primary-soft p-1.5"
      >
        {MIGRATION_FORMATS.map((entry) => (
          <Dropdown.Item
            key={entry.value}
            label={entry.label}
            className="h-8.5 rounded-md px-2.5 text-body-small"
            onClick={() => {
              setFormat(entry.value)
              copy(
                migration(entry.value, {
                  title: card.title,
                  sql: card.sql ?? '',
                  rollback_sql: card.rollback_sql,
                })
              )
            }}
          />
        ))}
      </Dropdown.Content>
    </Dropdown>
  )
}

const DAY = 86400

function isoDate(at: number): string {
  const date = new Date(at * 1000)
  const two = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())}`
}

/** A yyyy-mm-dd date as the start of that local day, in unix seconds. */
function dayStart(value: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) return null
  return (
    new Date(
      Number(match[1]),
      Number(match[2]) - 1,
      Number(match[3])
    ).getTime() / 1000
  )
}

function Field({
  label,
  type = 'text',
  value,
  onChange,
  placeholder,
}: {
  label: string
  type?: 'text' | 'date'
  value: string
  onChange: (value: string) => void
  placeholder?: string
}) {
  const id = useId()
  return (
    <div>
      <label
        htmlFor={id}
        className="mb-1 block text-caption text-content-layout-3"
      >
        {label}
      </label>
      <BaseInputText
        id={id}
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="h-8.5 bg-surface-layout-2 text-body-small"
      />
    </div>
  )
}

function PlanVerbs({ card, now }: { card: PlanCard; now: number }) {
  const verb = usePlanVerb()
  const [asideOpen, setAsideOpen] = useState(false)
  const [dismissOpen, setDismissOpen] = useState(false)
  const [until, setUntil] = useState(() => isoDate(now + 7 * DAY))
  const [reason, setReason] = useState('')
  const { toast } = useToast()
  const key = { target: card.database, id: card.id }
  // Set aside and Dismiss take the change off the list, so each offers an Undo.
  const undone = (name: 'set_aside' | 'dismiss') => () => {
    const handle = toast({
      title: name === 'dismiss' ? 'Dismissed' : 'Set aside',
      description: card.title,
      duration: 5000,
      action: (
        <Button
          label="Undo"
          size="small"
          modifier="outline"
          onClick={() => {
            verb.mutate({ ...key, verb: 'bring_back' })
            handle.dismiss()
          }}
        />
      ),
    })
  }
  const run = (
    name: 'set_aside' | 'dismiss' | 'did_it',
    extra: { until?: number; reason?: string } = {}
  ) =>
    verb.mutate(
      { ...key, verb: name, ...extra },
      { onSuccess: name === 'did_it' ? undefined : undone(name) }
    )
  return (
    <div className="contents" data-testid="plan-verbs">
      <Button
        variant="primary"
        modifier="solid"
        size="small"
        label="I did this"
        disabled={verb.isPending}
        onClick={() => run('did_it')}
      />
      <Popover open={asideOpen} onOpenChange={setAsideOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Set aside"
          />
        </PopoverTrigger>
        <PopoverContent
          side="top"
          align="start"
          aria-label="Set aside"
          className="w-72 flex-col gap-3 p-4"
        >
          <Field label="Until" type="date" value={until} onChange={setUntil} />
          <Field
            label="Reason"
            value={reason}
            onChange={setReason}
            placeholder="Optional"
          />
          <Button
            variant="primary"
            modifier="outline"
            size="small"
            label="Set aside"
            className="self-end"
            disabled={verb.isPending || dayStart(until) === null}
            onClick={() => {
              run('set_aside', {
                until: dayStart(until) ?? now + 7 * DAY,
                reason: reason.trim() || undefined,
              })
              setAsideOpen(false)
            }}
          />
        </PopoverContent>
      </Popover>
      <Popover open={dismissOpen} onOpenChange={setDismissOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Dismiss"
          />
        </PopoverTrigger>
        <PopoverContent
          side="top"
          align="start"
          aria-label="Dismiss"
          className="w-72 flex-col gap-3 p-4"
        >
          <Field
            label="Reason"
            value={reason}
            onChange={setReason}
            placeholder="Why this change isn't worth it"
          />
          <Button
            variant="primary"
            modifier="outline"
            size="small"
            label="Dismiss"
            className="self-end"
            disabled={verb.isPending}
            onClick={() => {
              run('dismiss', { reason: reason.trim() || undefined })
              setDismissOpen(false)
            }}
          />
        </PopoverContent>
      </Popover>
    </div>
  )
}

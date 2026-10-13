import { Button } from '@rs/ui-new/button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { StatusLine, statusLinkClass } from '@rs/ui-new/status-line'
import { Text } from '@rs/ui-new/text'
import { Link } from '@tanstack/react-router'
import { useOpenAddDatabase } from '../../components/configure/addDatabase'
import type { HomeStatus } from '../../lib/monitor/types'
import { formatClock, plural } from '../monitor-kit/format'
import { monitorLinkTo } from './homeModel'

/** "9:56:30": the last reading, to the second, without the day half. */
export function clockSeconds(ts: number): string {
  const date = new Date(ts * 1000)
  const hour = date.getHours() % 12 || 12
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${hour}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

/** "12 AM" on the hour, "9:30 AM" otherwise. */
function hourClock(ts: number): string {
  return formatClock(ts).replace(':00 ', ' ')
}

/**
 * The one sentence Home opens with, built from the status: Act now items first, then the
 * Do first changes, then the quiet, paused and not-monitored forms.
 */
export function HomeSentence({
  status,
  database,
  doFirstOn,
  oneDatabase = false,
}: {
  status: HomeStatus
  /** The database to name when the status has none (nothing is monitored yet). */
  database?: string
  /** The database to start the Do first changes on. */
  doFirstOn?: string | null
  /** Only one database is configured, so the sentence needs no "first". */
  oneDatabase?: boolean
}) {
  const target = status.target ?? database ?? ''
  const actNow = status.act_now ?? status.needs_you
  const doFirst = status.do_first ?? 0
  if (status.state === 'needs_you') {
    const link = status.link ? monitorLinkTo(status.link) : { to: '/inbox' }
    return (
      <>
        <Link
          to={link.to as never}
          search={link.search as never}
          className={statusLinkClass}
        >
          {plural(actNow, 'item')} {actNow === 1 ? 'needs' : 'need'} you
        </Link>{' '}
        on {target}
        {status.headline && (
          <StatusLine.Quiet>: {status.headline}.</StatusLine.Quiet>
        )}
      </>
    )
  }
  const first = doFirstOn ?? target
  if (status.state !== 'off' && doFirst > 0 && first)
    return (
      <>
        <Link
          to="/actions"
          search={{ db: first } as never}
          className={statusLinkClass}
        >
          {plural(doFirst, 'change')} to do
        </Link>
        {oneDatabase ? ` on ${first}.` : `, ${first} first.`}
      </>
    )
  if (status.state === 'quiet')
    return (
      <>
        All quiet on {target}
        <StatusLine.Quiet>
          {status.since ? ` since ${formatClock(status.since)}` : ''}.
        </StatusLine.Quiet>
      </>
    )
  if (status.state === 'paused')
    return (
      <>
        {target}
        <StatusLine.Quiet>
          : charts and stuck-query alerts running; AI checks paused
          {status.since ? ` since ${formatClock(status.since)}` : ''}.
        </StatusLine.Quiet>
      </>
    )
  return (
    <>
      {target || 'Your databases'}
      <StatusLine.Quiet>
        {target ? ': not monitored.' : ' are not monitored.'}
      </StatusLine.Quiet>
    </>
  )
}

/**
 * The caption under the sentence: what is watched out of `configured` databases, the last
 * reading, what comes next.
 */
export function homeCaption(status: HomeStatus, configured = 0): string | null {
  const parts: string[] = []
  if (status.watching > 0)
    parts.push(
      configured > status.watching
        ? `Watching ${status.watching} of ${plural(configured, 'database')}.`
        : `Watching ${plural(status.watching, 'database')}.`
    )
  if (status.last_reading_at)
    parts.push(`Last reading ${clockSeconds(status.last_reading_at)}.`)
  if (status.next_report_at)
    parts.push(
      `Next ${status.watching > 1 ? 'reports' : 'report'} at ${hourClock(status.next_report_at)}.`
    )
  return parts.length ? parts.join(' ') : null
}

const AFTER = [
  { step: 'Monitoring starts', when: 'as soon as it connects' },
  { step: 'Fixes show up in Actions', when: 'after about 10 minutes' },
  { step: 'A report every night', when: 'at 12 AM' },
]

/** Home with no database yet: one line, one action, and what follows. */
export function HomeEmpty() {
  const openAddDatabase = useOpenAddDatabase()
  return (
    <>
      <StatusLine tone="off">No database yet.</StatusLine>
      <div className="mt-5">
        <Button
          variant="primary"
          modifier="solid"
          label="Add a database"
          onClick={() => openAddDatabase()}
        />
      </div>
      <div className="mt-12 max-w-160">
        <Text as="p" level="caption" className="mb-2.5 text-content-layout-3">
          After you add one
        </Text>
        <DividedList columns="24px 1fr auto">
          {AFTER.map((item, index) => (
            <ListRow key={item.step}>
              <span className="text-caption tabular-nums text-content-layout-3">
                {index + 1}
              </span>
              <span className="text-body-medium text-content-layout-2">
                {item.step}
              </span>
              <span className="text-right text-caption text-content-layout-3">
                {item.when}
              </span>
            </ListRow>
          ))}
        </DividedList>
      </div>
    </>
  )
}

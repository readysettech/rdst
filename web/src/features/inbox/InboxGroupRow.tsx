import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { IconButton } from '@rs/ui-new/icon-button'
import { AnimatePresence, m, useReducedMotion } from '@rs/ui-new/motion'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'
import { plural } from '../monitor-kit/format'
import { MuteMenu } from '../monitor-kit/MuteMenu'
import { SeverityIcon } from '../monitor-kit/SeverityIcon'
import { itemBand, itemTone } from '../monitor-kit/severity'
import {
  Caption,
  DatabaseName,
  HOVER_ACTIONS,
  InboxItemRow,
  openQuery,
  queryHref,
  ResolveSplit,
  SentTime,
  SummaryLine,
} from './InboxItemRow'
import { groupSummary, type InboxGroup, sentStamp } from './inboxModel'
import { itemStory, type QueryLine } from './itemStory'
import { MEMBER_PAGE } from './OnsetClusters'
import { QueryLineLink, QueryLineList } from './QueryLines'
import type { InboxActions } from './useInboxActions'

/** Members shown per page; a linked member further down opens enough pages to show it. */
function highlightedIndex(group: InboxGroup, id?: number) {
  const index = group.items.findIndex((item) => item.id === id)
  return Math.max(
    MEMBER_PAGE,
    Math.ceil((index + 1) / MEMBER_PAGE) * MEMBER_PAGE
  )
}

interface InboxGroupRowProps {
  group: InboxGroup
  actions: InboxActions
  names: Map<string, string>
  expanded: boolean
  onToggle: () => void
  highlightId?: number
  freshIds: ReadonlySet<number>
}

/** Several alerts of one type on one target, filed close together, as one row. */
export function InboxGroupRow({
  group,
  actions,
  names,
  expanded,
  onToggle,
  highlightId,
  freshIds,
}: InboxGroupRowProps) {
  const reduced = useReducedMotion()
  const [shown, setShown] = useState(highlightedIndex(group, highlightId))
  const { lead, items } = group
  const tone = itemTone(lead)
  const band = itemBand(lead)
  const unread = items.filter((item) => item.state === 'new').length
  // One line per member query, each with its own numbers; urgent members first.
  const byKey = new Map<string, (typeof items)[number]>()
  const lines: QueryLine[] = []
  for (const item of [...items].sort((a, b) => b.severity - a.severity)) {
    const line = itemStory(item, names).queries[0]
    if (!line || byKey.has(line.key)) continue
    byKey.set(line.key, item)
    lines.push(line)
  }
  const [firstLine] = lines
  const memberOf = (key: string) => byKey.get(key) ?? lead
  const panelId = `inbox-group-${group.id.replaceAll(/[^\w-]/g, '-')}`
  const countTag = (
    <Tag
      label={
        unread > 0
          ? `${items.length} alerts, ${unread} unread`
          : `${items.length} alerts`
      }
      variant="neutral"
      modifier="ghost"
      size="small"
      className="shrink-0 whitespace-nowrap"
    />
  )
  const toggle = (
    <IconButton
      icon={expanded ? 'chevron-up' : 'chevron-down'}
      label={expanded ? 'Hide alerts' : `Show ${items.length} alerts`}
      modifier="ghost"
      size="small"
      aria-expanded={expanded}
      aria-controls={panelId}
      onClick={onToggle}
    />
  )
  const closeControls = (
    <>
      {band === 'recovered' ? (
        <Button
          label="Got it"
          icon="tick"
          iconPosition="left"
          modifier="outline"
          size="small"
          onClick={() => actions.acknowledge(items)}
        />
      ) : (
        <ResolveSplit
          label="Resolve all"
          onClose={(action) => actions.close(items, action)}
        />
      )}
      <MuteMenu
        target={lead.target}
        type={lead.type}
        label={`More actions for ${lead.title}`}
      />
    </>
  )
  return (
    <div>
      {band === 'fyi' || band === 'recovered' ? (
        <div className="group relative flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5 transition-colors duration-fast ease-base hover:bg-surface-raised">
          <span className="flex w-2 shrink-0 justify-center" />
          <SeverityIcon type={lead.type} tone={tone} size="small" />
          <div className="flex min-w-0 flex-1 basis-56 flex-col gap-0.5">
            <div className="flex min-w-0 items-center gap-2">
              <Text
                as="h3"
                level="label-medium"
                className="truncate text-content-layout-1"
              >
                {lead.title}
              </Text>
              {countTag}
            </div>
            <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
              {firstLine ? (
                <>
                  <QueryLineLink
                    line={{ ...firstLine, figures: null }}
                    href={queryHref(memberOf(firstLine.key), firstLine.key)}
                    onOpen={() =>
                      openQuery(memberOf(firstLine.key), firstLine.key, actions)
                    }
                    size="small"
                  />
                  {lines.length > 1 && (
                    <Text level="body-small" className="text-content-layout-3">
                      and{' '}
                      {plural(lines.length - 1, 'other query', 'other queries')}
                    </Text>
                  )}
                </>
              ) : (
                <Text level="body-small" className="text-content-layout-3">
                  {groupSummary(group)}
                </Text>
              )}
            </div>
          </div>
          <Text
            level="caption"
            className="ml-7 basis-full text-content-layout-3 tablet:ml-0 tablet:basis-auto tablet:shrink-0 tablet:whitespace-nowrap"
          >
            <SentTime stamp={sentStamp(items[0])} prefix="Latest" short />
          </Text>
          {toggle}
          <div
            className={cn(
              HOVER_ACTIONS,
              'laptop:pointer-fine:right-14 laptop:pointer-fine:top-1/2 laptop:pointer-fine:-translate-y-1/2'
            )}
          >
            {closeControls}
          </div>
        </div>
      ) : (
        <div className="group relative grid grid-cols-[auto_minmax(0,1fr)_auto] gap-x-3 gap-y-3 px-4 py-4 transition-colors duration-fast ease-base hover:bg-surface-raised tablet:gap-x-4">
          <div className="flex items-start gap-2 pt-0.5">
            <span className="flex w-2 shrink-0 justify-center" />
            <SeverityIcon type={lead.type} tone={tone} />
          </div>
          <div className="flex min-w-0 flex-col gap-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <Text
                as="h3"
                level={band === 'act_now' ? 'headline-5' : 'subtitle-1'}
                className="text-content-layout-1"
              >
                {lead.title}
              </Text>
              {countTag}
            </div>
            {lines.length > 0 && (
              <QueryLineList
                lines={lines}
                hrefFor={(key) => queryHref(memberOf(key), key)}
                onOpen={(key) => openQuery(memberOf(key), key, actions)}
              />
            )}
            {lines.length === 0 && (
              <SummaryLine
                summary={`${groupSummary(group)}.`}
                className="max-w-3xl"
              />
            )}
            <Text level="caption" className="text-content-layout-3">
              <Caption
                parts={[
                  <DatabaseName key="database" name={lead.target} />,
                  <SentTime
                    key="sent"
                    stamp={sentStamp(items[0])}
                    prefix="Latest"
                  />,
                ]}
              />
            </Text>
          </div>
          <div className="flex items-start">{toggle}</div>
          <div
            className={cn(
              HOVER_ACTIONS,
              'col-span-full justify-end laptop:pointer-fine:right-14 laptop:pointer-fine:top-3.5'
            )}
          >
            {closeControls}
          </div>
        </div>
      )}
      <AnimatePresence initial={false}>
        {expanded && (
          <m.div
            id={panelId}
            key="members"
            initial={reduced ? { opacity: 0 } : { height: 0, opacity: 0 }}
            animate={reduced ? { opacity: 1 } : { height: 'auto', opacity: 1 }}
            exit={reduced ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
            className="overflow-hidden border-t border-border-layout-1 bg-surface-layout-2/40"
          >
            <div className="divide-y divide-border-layout-1">
              {items.slice(0, shown).map((item) => (
                <InboxItemRow
                  key={item.id}
                  item={item}
                  actions={actions}
                  names={names}
                  showTarget={false}
                  nested
                  highlighted={item.id === highlightId}
                  fresh={freshIds.has(item.id)}
                />
              ))}
              {items.length > MEMBER_PAGE && (
                <div className="flex items-center gap-1 py-1.5 pr-4 pl-9">
                  {shown < items.length && (
                    <Button
                      label={`Show ${Math.min(MEMBER_PAGE, items.length - shown)} more`}
                      icon="chevron-down"
                      iconPosition="right"
                      modifier="ghost"
                      size="small"
                      onClick={() => setShown((value) => value + MEMBER_PAGE)}
                    />
                  )}
                  <Button
                    label={shown > MEMBER_PAGE ? 'Show fewer' : 'Hide alerts'}
                    icon="chevron-up"
                    iconPosition="right"
                    modifier="ghost"
                    size="small"
                    onClick={() =>
                      shown > MEMBER_PAGE ? setShown(MEMBER_PAGE) : onToggle()
                    }
                  />
                  <Text
                    level="caption"
                    className="ml-auto tabular-nums text-content-layout-3"
                  >
                    {Math.min(shown, items.length)} of {items.length}
                  </Text>
                </div>
              )}
            </div>
          </m.div>
        )}
      </AnimatePresence>
    </div>
  )
}

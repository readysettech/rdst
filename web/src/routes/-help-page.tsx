import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { focusRingClass } from '@rs/ui-new/focus'
import { Icon } from '@rs/ui-new/icon'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import { type ReactNode, useState } from 'react'
import { DesktopUpdateControl } from '../components/DesktopUpdateControl'
import { ReportDialog } from '../components/ReportDialog'
import { DISCORD_INVITE_URL, DOCS_URL } from '../features/settings/links'
import { useDesktopUpdates } from '../lib/useDesktopUpdates'
import { useSystemStatus } from '../lib/useSystemStatus'

const doorClass = `flex w-full cursor-pointer items-center gap-3.5 rounded-card border border-border-layout-2 bg-surface-primary-soft px-5 py-4.5 text-left transition-[border-color] duration-fast ease-base hover:border-content-layout-3 ${focusRingClass}`

function DoorBody({ name, line }: { name: string; line: string }) {
  return (
    <>
      <span className="min-w-0">
        <Text
          as="span"
          level="body-medium"
          className="block font-medium text-content-layout-1"
        >
          {name}
        </Text>
        <Text
          as="span"
          level="caption"
          className="mt-0.5 block text-content-layout-3"
        >
          {line}
        </Text>
      </span>
      <Icon
        name="chevron-right"
        label=""
        size="small"
        className="ml-auto text-content-layout-3"
      />
    </>
  )
}

function Keys({ children }: { children: ReactNode }) {
  return (
    <span className="flex items-center gap-1.5 text-caption text-content-layout-3">
      {children}
    </span>
  )
}

function Kbd({ children }: { children: string }) {
  return (
    <kbd className="rounded-sm border border-border-layout-2 px-1.25 font-mono text-label-extra-small font-normal text-content-layout-3">
      {children}
    </kbd>
  )
}

const LIST_SHORTCUTS = [
  { name: 'Move down, up', keys: ['j', 'k'] },
  { name: 'Resolve', keys: ['e'] },
  { name: 'Open', keys: ['o'] },
  { name: 'Search', keys: ['/'] },
  { name: 'Shortcuts', keys: ['?'] },
]

const GO_SHORTCUTS = [
  { name: 'Go to Home', key: 'h' },
  { name: 'Go to Inbox', key: 'i' },
  { name: 'Go to Actions', key: 'a' },
  { name: 'Go to Database', key: 'd' },
]

export function HelpPage() {
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const { data: status } = useSystemStatus()
  const updates = useDesktopUpdates()

  return (
    <>
      <PageBar title="Help" />
      <PageContainer className="pt-9 pb-24">
        <div className="grid grid-cols-2 gap-3">
          <a
            href={DOCS_URL}
            target="_blank"
            rel="noreferrer"
            className={doorClass}
          >
            <DoorBody
              name="Docs"
              line="Setup, monitoring, and what each check means."
            />
          </a>
          <Pressable
            className={doorClass}
            onClick={() =>
              document
                .getElementById('keys')
                ?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
            }
          >
            <DoorBody
              name="Keyboard shortcuts"
              line="Move through lists without the mouse."
            />
          </Pressable>
          <Pressable
            className={doorClass}
            onClick={() => setFeedbackOpen(true)}
          >
            <DoorBody name="Give feedback" line="Goes straight to the team." />
          </Pressable>
          <a
            href={DISCORD_INVITE_URL}
            target="_blank"
            rel="noreferrer"
            className={doorClass}
          >
            <DoorBody
              name="Join Discord"
              line="Ask other RDST users and the team."
            />
          </a>
        </div>

        <section id="keys" aria-labelledby="help-keys" className="mt-10">
          <h2
            id="help-keys"
            className="mb-3 text-headline-5 text-content-layout-1"
          >
            Keyboard shortcuts
          </h2>
          <div className="grid grid-cols-2 gap-x-12">
            <DividedList columns="1fr auto">
              {LIST_SHORTCUTS.map((item) => (
                <ListRow key={item.name} className="min-h-11.5 py-2">
                  <span className="text-body-small text-content-layout-1">
                    {item.name}
                  </span>
                  <Keys>
                    {item.keys.map((key) => (
                      <Kbd key={key}>{key}</Kbd>
                    ))}
                  </Keys>
                </ListRow>
              ))}
            </DividedList>
            <DividedList columns="1fr auto">
              {GO_SHORTCUTS.map((item) => (
                <ListRow key={item.name} className="min-h-11.5 py-2">
                  <span className="text-body-small text-content-layout-1">
                    {item.name}
                  </span>
                  <Keys>
                    <Kbd>g</Kbd>then<Kbd>{item.key}</Kbd>
                  </Keys>
                </ListRow>
              ))}
            </DividedList>
          </div>
        </section>

        <div className="mt-14 flex items-center gap-4 border-t border-border-layout-2 pt-3.5 text-caption text-content-layout-3">
          {status?.version && (
            <span className="tabular-nums">RDST {status.version}</span>
          )}
          <DesktopUpdateControl
            state={updates.state}
            install={updates.install}
          />
        </div>
      </PageContainer>
      <ReportDialog
        isOpen={feedbackOpen}
        onClose={() => setFeedbackOpen(false)}
      />
    </>
  )
}

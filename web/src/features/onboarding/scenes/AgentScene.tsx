// Scene 4: a coding agent asks RDST what it found through RDST's local tools, gets the slow
// query and its index back, writes the migration and marks the Inbox item resolved.
import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { SqlTokens } from '../../../components/SqlTokens'
import { outBack, ramp, rise, typed } from '../sceneClock'
import { MiniSidebar, Window } from './parts'

export const AGENT_DURATION = 10

const ASK = 'What did RDST find on shop? Fix it.'
const ANSWER =
  'Order history slowed from 6 ms to 184 ms. It reads all 2.4M rows in orders to find one customer. Adding the index RDST suggests:'
const SQL_LINES = [
  'CREATE INDEX CONCURRENTLY',
  '  orders_customer_created_idx',
  '  ON orders (customer_id, created_at DESC);',
]
const RESOLVE_AT = 6.2
const RESOLVED_AT = 7.2

function ToolCall({
  t,
  at,
  name,
  args,
  result,
}: {
  t: number
  at: number
  name: string
  args: string
  result: string
}) {
  const shown = ramp(t, at, at + 0.25)
  if (shown <= 0) return null
  return (
    <div style={rise(shown, 4)}>
      <div className="flex items-center gap-1.5 font-mono text-mono-small">
        <span className="size-1.5 rounded-pill bg-content-positive-soft" />
        <span className="text-content-layout-1">{name}</span>
        <span className="text-content-layout-3">({args})</span>
      </div>
      <div
        className="pl-3 font-mono text-mono-small text-content-layout-2"
        style={{ opacity: ramp(t, at + 0.35, at + 0.6) }}
      >
        {result}
      </div>
    </div>
  )
}

export function AgentScene({ t }: { t: number }) {
  const resolved = ramp(t, RESOLVED_AT, RESOLVED_AT + 0.4, outBack)
  const ask = typed(
    ASK,
    ramp(t, 0.6, 1.8, (x) => x)
  )
  const answer = typed(
    ANSWER,
    ramp(t, 3.8, 4.7, (x) => x)
  )
  const diff = ramp(t, 4.8, 5.2)

  return (
    <>
      <Window
        style={{ left: 222, top: 70, width: 354, height: 300 }}
        className={cn(
          'transition-colors duration-slow',
          resolved > 0.5 && 'border-border-positive-soft'
        )}
      >
        <div className="flex h-full">
          <MiniSidebar
            active="Inbox"
            inbox={resolved > 0.5 ? 0 : 1}
            databases={[
              {
                name: 'shop',
                state: 'watching',
                toDo: resolved > 0.5 ? undefined : '1 to do',
              },
            ]}
          />
          <div className="min-w-0 flex-1 border-l border-border-layout-2 bg-surface-layout-1 px-2 pt-4">
            <div className="px-2 text-label-medium text-content-layout-1">
              Inbox
            </div>
            <div
              className={cn(
                'mt-3 rounded-panel border px-2.5 py-2',
                resolved > 0.5
                  ? 'border-border-positive-soft bg-surface-positive-soft'
                  : 'border-border-layout-2 bg-surface-raised'
              )}
            >
              <div
                className={cn(
                  'flex items-center gap-1.5 text-label-extra-small',
                  resolved > 0.5
                    ? 'text-content-positive-soft'
                    : 'text-content-negative-soft'
                )}
              >
                <span
                  className={cn(
                    'size-1.5 rounded-pill',
                    resolved > 0.5
                      ? 'bg-content-positive-soft'
                      : 'bg-content-negative-plain'
                  )}
                />
                {resolved > 0.5 ? 'Resolved' : 'Act now'}
              </div>
              <div className="mt-1 text-caption text-content-layout-1">
                Order history is 30x slower than usual
              </div>
              <div
                className="mt-1 flex items-center gap-1 text-label-extra-small font-normal text-content-positive-soft"
                style={rise(resolved, 4)}
              >
                <Icon name="tick-double" label="" size="small" />
                Resolved by your coding agent
              </div>
            </div>
          </div>
        </div>
      </Window>

      <div
        className="absolute flex flex-col gap-2 overflow-hidden rounded-panel border border-border-layout-2 bg-surface-layout-2 px-3 pt-2.5 pb-3 shadow-elevation-3"
        style={{
          left: 14,
          top: 30,
          width: 300,
          height: 380,
          opacity: 1 - 0.35 * ramp(t, RESOLVE_AT + 0.4, RESOLVE_AT + 0.9),
        }}
      >
        <div className="flex items-center gap-2 text-label-extra-small text-content-layout-2">
          Coding agent
          <span className="font-normal text-content-layout-3">~/shop</span>
          <span className="ml-auto flex items-center gap-1 font-normal text-content-layout-3">
            <span className="size-1.5 rounded-pill bg-content-positive-soft" />
            rdst tools connected
          </span>
        </div>
        <div className="rounded-control border border-border-layout-2 px-2 py-1.5 font-mono text-mono-small text-content-layout-1">
          <span className="text-content-layout-3">&gt; </span>
          {ask}
        </div>
        <ToolCall
          t={t}
          at={2.1}
          name="rdst_list_inbox"
          args='target: "shop"'
          result="1 open: Order history is 30x slower"
        />
        <ToolCall
          t={t}
          at={2.9}
          name="rdst_get_inbox_item"
          args="id: 42"
          result="p95 184 ms, usually 6 ms. Fix: index"
        />
        {answer && (
          <div className="text-caption text-content-layout-1">{answer}</div>
        )}
        <div
          className="rounded-control border border-border-positive-soft bg-surface-positive-soft px-2 py-1.5"
          style={rise(diff, 6)}
        >
          {SQL_LINES.map((line, index) => (
            <div
              key={line}
              style={{
                clipPath: `inset(0 ${(1 - ramp(t, 4.9 + index * 0.25, 5.25 + index * 0.25)) * 100}% 0 0)`,
              }}
            >
              <SqlTokens
                sql={line}
                className="text-mono-small whitespace-pre"
              />
            </div>
          ))}
        </div>
        <ToolCall
          t={t}
          at={RESOLVE_AT - 0.3}
          name="rdst_inbox_action"
          args='verb: "resolve"'
          result="Resolved"
        />
      </div>
    </>
  )
}

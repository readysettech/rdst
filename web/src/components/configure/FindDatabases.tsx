// The way in to adding a database. "Import local databases" has RDST look on this computer
// itself (recent projects, Docker, the databases running here, saved connections); a
// provider's database goes to that provider's sign-in. The user picks one database, and
// their own coding agent can set it up in a fresh conversation, shown as steps, or they
// add it themselves. The coding agent's own ways in sit underneath.
import { cn } from '@rs/tailwind-base'
import { Alert } from '@rs/ui-new/alert'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import {
  type ComponentType,
  Fragment,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import {
  addFoundDatabase,
  cancelDiscovery,
  checkTarget,
  continueInApp,
  continueInTerminal,
  type DiscoveryAgent,
  type DiscoveryRun,
  evidenceKinds,
  type FoundDatabase,
  fetchDiscovery,
  fetchDiscoveryOptions,
  lookInProject,
  PROMPT_SUMMARY,
  PROVIDER_NAMES,
  readsItsOwnSecret,
  recommended,
  replyToAgent,
  SIGN_IN_PROVIDERS,
  type SignInProvider,
  signInProvider,
  startAgentFix,
  startDiscovery,
  startScan,
  startSetup,
} from '../../lib/agentDiscovery'
import {
  maskInReplays,
  type RdstEventProperties,
  trackEvent,
} from '../../lib/analytics'
import { useConfigure } from '../../lib/useConfigure'
import { userErrorClass } from '../../lib/userErrorClass'
import {
  AgentLogo,
  AwsLogo,
  DigitalOceanLogo,
  NeonLogo,
  SupabaseLogo,
} from '../providers/ProviderLogos'
import { AgentSignIn } from './AgentSignIn'
import { sourceLabel } from './ProviderImport'

const POLL_MS = 1200
// A scan takes seconds, so its steps are followed closely.
const SCAN_POLL_MS = 300

const ENGINE: Record<NonNullable<FoundDatabase['engine']>, string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL',
}

/** "PostgreSQL on Supabase", or just "Database" when RDST can't tell which. */
const engineLabel = (found: FoundDatabase) => {
  const engine = found.engine ? ENGINE[found.engine] : 'Database'
  const hosted = PROVIDER_NAMES[found.provider]
  return hosted ? `${engine} on ${hosted}` : engine
}

const LOGOS: Record<SignInProvider, ComponentType<{ size?: number }>> = {
  supabase: SupabaseLogo,
  neon: NeonLogo,
  digitalocean: DigitalOceanLogo,
  aws: AwsLogo,
}

const APP_NAMES = { claude: 'Claude', codex: 'Codex' } as const

/** Where a conversation carries on once the user took it out of RDST. */
const whereItIs = (place: 'claude' | 'codex' | 'terminal') =>
  place === 'terminal' ? 'a terminal' : `the ${APP_NAMES[place]} app`

// The places a scan checks, shown until the scan itself answers.
const SCAN_PLACES = [
  'Your recent projects',
  'Your agent and terminal history',
  'Docker containers',
  'Databases running on this computer',
  'Saved connections',
].map((label) => ({ method: '', label, state: 'running', found: 0 }))

type FindStage = RdstEventProperties['find_view_closed']['stage']

/** "localhost:5432/shop" from the parts that were found. */
export function whereLabel(found: FoundDatabase): string {
  if (!found.host)
    return found.instance
      ? `Cloud SQL ${found.instance}`
      : 'Where it runs is in the code'
  const host = found.host
  const port = found.port ? `:${found.port}` : ''
  return `${host}${port}${found.database ? `/${found.database}` : ''}`
}

/** A home-relative project path, short enough for one line. */
export function projectLabel(path: string | null): string | null {
  if (!path) return null
  // WSL's folders, as Windows opens them, read as WSL's own paths.
  const wsl = path.match(/^\\\\wsl(?:\.localhost|\$)\\[^\\]+(\\.*)$/i)
  if (wsl) return `WSL ${projectLabel(wsl[1].replaceAll('\\', '/'))}`
  return path
    .replace(/^\/home\/[^/]+/, '~')
    .replace(/^\/Users\/[^/]+/, '~')
    .replace(/^[A-Za-z]:\\Users\\[^\\]+/, '~')
}

const NUMBERS = [
  'no',
  'one',
  'two',
  'three',
  'four',
  'five',
  'six',
  'seven',
  'eight',
  'nine',
]

/** "one database", "12 databases". */
export function countLabel(count: number, noun = 'database'): string {
  return `${NUMBERS[count] ?? count} ${noun}${count === 1 ? '' : 's'}`
}

/** An agent's summary opens with its point; the rest stays in the conversation. */
const firstParagraph = (text: string | null) =>
  text?.split(/\n\s*\n/)[0]?.trim() || null

const nameOf = (found: FoundDatabase) =>
  found.name || found.database || whereLabel(found)

/** Whether an agent can set it up: a provider's database is imported through its sign-in
 * first, and the agent helps from there. */
export const agentCanSetUp = (found: FoundDatabase) => !signInProvider(found)

type StepState = 'done' | 'active' | 'waiting' | 'failed' | 'todo'

/** Numbered steps, each ticking off as it finishes. */
/** Steps the way an analysis shows them: a thin line for how far along, a tick for each
 * done step, a dot for the current one. Nothing spins. */
function Steps({
  steps,
  testId,
}: {
  steps: { label: string; state: StepState; detail?: ReactNode }[]
  testId: string
}) {
  const done = steps.filter((step) => step.state === 'done').length
  return (
    <div data-testid={testId}>
      <div className="mb-2.5 h-0.5 overflow-hidden rounded-pill bg-surface-primary-soft-hover">
        <div
          className="h-full bg-content-layout-2 transition-[width] duration-slow ease-base"
          style={{ width: `${Math.round((done / steps.length) * 100)}%` }}
        />
      </div>
      <ol className="m-0 list-none p-0">
        {steps.map((step) => (
          <li
            key={step.label}
            data-state={step.state}
            className="flex min-h-8.5 items-start gap-3 py-1.5"
          >
            <span className="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center">
              {step.state === 'done' ? (
                <Icon
                  name="tick"
                  label="Done"
                  size="small"
                  className="text-content-layout-2"
                />
              ) : step.state === 'failed' ? (
                <Icon
                  name="alert"
                  label="Stopped"
                  size="small"
                  className="text-content-negative-soft"
                />
              ) : step.state === 'active' || step.state === 'waiting' ? (
                <span
                  className={cn(
                    'h-2 w-2 rounded-full',
                    step.state === 'waiting'
                      ? 'bg-content-primary-soft'
                      : 'animate-pulse bg-content-layout-1'
                  )}
                />
              ) : (
                <span className="h-1.5 w-1.5 rounded-full border border-content-layout-3" />
              )}
            </span>
            <span className="min-w-0 flex-1">
              <Text
                as="span"
                level="body-small"
                className={cn(
                  'block',
                  step.state === 'todo'
                    ? 'text-content-layout-3'
                    : step.state === 'done'
                      ? 'text-content-layout-2'
                      : 'text-content-layout-1'
                )}
              >
                {step.label}
              </Text>
              {step.detail && (
                <Text
                  as="span"
                  level="caption"
                  className="block text-content-layout-3"
                >
                  {step.detail}
                </Text>
              )}
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}

function SourceTile({ found }: { found: FoundDatabase }) {
  const provider = signInProvider(found)
  const Logo = provider ? LOGOS[provider] : null
  return (
    <span className="grid size-9 shrink-0 place-items-center rounded-control border border-border-layout-2 bg-surface-layout-1 text-content-layout-2">
      {Logo ? (
        <Logo size={18} />
      ) : (
        <Icon name="database" label="" size="small" />
      )}
    </span>
  )
}

/** Where RDST would read the password from, by file name only. */
function secretWhere(found: FoundDatabase): string {
  const file = (path: string | null | undefined) =>
    path?.split(/[\\/]/).pop() ?? ''
  if (found.env_file && found.env_var)
    return `Its URL is in ${file(found.env_file)} (${found.env_var}).`
  if (found.password_file && found.password_var)
    return `Its password is in ${file(found.password_file)} (${found.password_var}).`
  if (found.url_file)
    return found.history
      ? 'Its URL is in one of your agent sessions or your shell history.'
      : `Its URL is in ${file(found.url_file)}.`
  if (found.secrets) return `Its secrets are in ${found.secrets}.`
  return "RDST didn't find its password."
}

/** One found database, picked with the others like radio buttons. */
function FoundOption({
  found,
  picked,
  best,
  grouped,
  onPick,
}: {
  found: FoundDatabase
  picked: boolean
  best: boolean
  /** Under its project's heading, so the row leaves the project out. */
  grouped: boolean
  onPick: () => void
}) {
  const project = grouped ? null : projectLabel(found.project)
  const [open, setOpen] = useState(false)
  return (
    <li>
      <label
        data-testid="found-database"
        className={cn(
          'flex w-full cursor-pointer items-center gap-3 px-4 py-3 text-left transition-colors duration-fast has-focus-visible:outline-2 has-focus-visible:outline-border-primary-soft',
          picked ? 'bg-surface-primary-soft' : 'hover:bg-surface-layout-2/60'
        )}
      >
        <input
          type="radio"
          name="found-database"
          className="sr-only"
          checked={picked}
          onChange={onPick}
        />
        <span
          aria-hidden
          className={cn(
            'grid size-4 shrink-0 place-items-center rounded-full border',
            picked ? 'border-content-primary-soft' : 'border-border-layout-2'
          )}
        >
          {picked && (
            <span className="size-2 rounded-full bg-content-primary-soft" />
          )}
        </span>
        <SourceTile found={found} />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <Text
              as="span"
              level="label-small"
              className="min-w-0 max-w-full truncate text-content-layout-1"
            >
              {nameOf(found)}
            </Text>
            {found.environment === 'production' && (
              <Tag
                size="small"
                variant="warning"
                modifier="ghost"
                label="Production"
              />
            )}
            {best && (
              <Tag
                size="small"
                variant="primary"
                modifier="ghost"
                label="Best match"
              />
            )}
            {found.saved_as && (
              <Tag
                size="small"
                variant="positive"
                modifier="ghost"
                label={`In RDST as ${found.saved_as}`}
              />
            )}
            {found.wsl && (
              <Tag
                size="small"
                variant="neutral"
                modifier="ghost"
                label="WSL"
              />
            )}
            {found.provider === 'docker' && (
              <Tag
                size="small"
                variant="neutral"
                modifier="ghost"
                label="Docker"
              />
            )}
            {found.reach === 'unreachable' && (
              <span title="It exists but doesn't answer from this computer: it may be in a private network or behind an allowlist. Claude can help reach it.">
                <Tag
                  size="small"
                  variant="neutral"
                  modifier="outline"
                  label="Not reachable from here"
                />
              </span>
            )}
            {found.secrets && !found.saved_as && (
              <Tag
                size="small"
                variant="neutral"
                modifier="ghost"
                label={`Secrets in ${found.secrets.split(', ')[0]}`}
              />
            )}
          </span>
          <Text
            as="span"
            level="caption"
            className="ph-mask block truncate text-content-layout-3"
          >
            {engineLabel(found)} · {whereLabel(found)}
            {project ? ` · ${project}` : ''}
          </Text>
          {(found.found_by.length > 0 || found.note) && (
            <Text
              as="span"
              level="caption"
              className="ph-mask block truncate text-content-layout-3"
            >
              {found.note ?? found.found_by.join(' · ')}
            </Text>
          )}
          {open && (
            <span
              className="mt-1.5 block space-y-0.5"
              data-testid="found-details"
            >
              {found.found_by.map((line) => (
                <Text
                  key={line}
                  as="span"
                  level="caption"
                  className="ph-mask block text-content-layout-2"
                >
                  {line}
                </Text>
              ))}
              <Text
                as="span"
                level="caption"
                className="ph-mask block text-content-layout-3"
              >
                {secretWhere(found)}
              </Text>
            </span>
          )}
        </span>
        <button
          type="button"
          className="shrink-0 self-start rounded-control px-1.5 py-0.5 text-caption text-content-layout-3 hover:text-content-layout-1"
          aria-expanded={open}
          onClick={(event) => {
            event.preventDefault()
            setOpen(!open)
          }}
          data-testid="found-details-toggle"
        >
          {open ? 'Less' : 'Details'}
        </button>
      </label>
    </li>
  )
}

/** A project RDST only listed, because macOS asks before an app reads its folder. RDST
 * reads it when the user picks it, so macOS asks then, once, in context. */
function WaitingProject({
  found,
  busy,
  onLook,
}: {
  found: FoundDatabase
  busy: boolean
  onLook: () => void
}) {
  const where = found.found_by[0]?.replace(/^A recent project in /, '') ?? ''
  return (
    <li
      className="flex items-center gap-3 px-4 py-3"
      data-testid="waiting-project"
    >
      <span className="grid size-9 shrink-0 place-items-center rounded-control border border-border-layout-2 bg-surface-layout-1 text-content-layout-2">
        <Icon name="folder-file" label="" size="small" />
      </span>
      <span className="min-w-0 flex-1">
        <Text
          as="span"
          level="label-small"
          className="ph-mask block truncate text-content-layout-1"
        >
          {nameOf(found)}
        </Text>
        <Text as="span" level="caption" className="block text-content-layout-3">
          {`A recent project in ${where}. Your Mac asks once before RDST looks inside.`}
        </Text>
      </span>
      <Button
        variant="primary"
        modifier="outline"
        size="small"
        label="Look in this project"
        loading={busy}
        onClick={onLook}
        data-testid="look-in-project"
      />
    </li>
  )
}

export interface FoundGroup {
  /** A project's databases, the ones outside any project, or the ones seen only in the
   * user's agent and terminal history. */
  kind: 'project' | 'elsewhere' | 'history'
  project: string | null
  items: FoundDatabase[]
}

/** The found databases by project, best first; then the ones outside any project, then
 * the ones seen only in history, which say least about what the user works on now. */
export function groupByProject(found: FoundDatabase[]): FoundGroup[] {
  const groups = new Map<string, FoundDatabase[]>()
  const outside: FoundDatabase[] = []
  const history: FoundDatabase[] = []
  for (const item of found) {
    if (item.history && !item.saved_as) history.push(item)
    else if (item.project)
      groups.set(item.project, [...(groups.get(item.project) ?? []), item])
    else outside.push(item)
  }
  return [
    ...[...groups].map(([project, items]) => ({
      kind: 'project' as const,
      project,
      items,
    })),
    ...(outside.length
      ? [{ kind: 'elsewhere' as const, project: null, items: outside }]
      : []),

    ...(history.length
      ? [{ kind: 'history' as const, project: null, items: history }]
      : []),
  ]
}

/** What the agent is doing, as it does it: its own words and the steps it runs. */
function Trace({
  run,
  testId = 'search-trace',
  kinds = ['say', 'tool'],
}: {
  run: DiscoveryRun
  testId?: string
  /** Which lines show: the agent's own, and with 'rdst' what it asked RDST to do. */
  kinds?: string[]
}) {
  const lines = run.activity
    .filter((line) => kinds.includes(line.kind))
    .slice(-6)
  if (!lines.length) return null
  return (
    <ol
      className="ph-mask m-0 mt-3 list-none space-y-1 rounded-control bg-surface-layout-2 px-3 py-2"
      data-testid={testId}
      aria-live="polite"
    >
      {lines.map((line, index) => (
        <li
          key={index}
          className={cn(
            'truncate text-caption',
            line.kind === 'tool'
              ? 'font-mono text-content-layout-3'
              : 'text-content-layout-2',
            index === lines.length - 1 && 'rdst-shimmer'
          )}
        >
          {line.text}
        </li>
      ))}
    </ol>
  )
}

function AgentSwitch({
  agents,
  agent,
  onPick,
}: {
  agents: DiscoveryAgent[]
  agent: DiscoveryAgent
  onPick: (agent: DiscoveryAgent) => void
}) {
  const [open, setOpen] = useState(false)
  if (agents.length < 2) return null
  return (
    <Dropdown open={open} onOpenChange={setOpen}>
      <Dropdown.Trigger asChild>
        <Pressable
          aria-label="Choose another coding agent"
          data-testid="agent-switch"
          className="-ml-1 grid size-8 cursor-pointer place-items-center rounded-control text-content-layout-3 hover:bg-surface-layout-2 hover:text-content-layout-1"
        >
          <Icon name="chevron-down" label="" size="small" />
        </Pressable>
      </Dropdown.Trigger>
      <Dropdown.Content align="start" className="min-w-52 rounded-panel p-1.5">
        {agents
          .filter((item) => item.key !== agent.key)
          .map((item) => (
            <Dropdown.Item
              key={item.key}
              label={item.label}
              onClick={() => {
                onPick(item)
                setOpen(false)
              }}
            />
          ))}
      </Dropdown.Content>
    </Dropdown>
  )
}

/** A small line drawing for a way in, in the theme's own colors. */
function Illustration({ kind }: { kind: 'scan' }) {
  const line = 'stroke-current'
  const soft = 'fill-current opacity-15'
  return (
    <svg
      aria-hidden
      viewBox="0 0 96 64"
      className="h-14 w-20 shrink-0"
      fill="none"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {kind === 'scan' && (
        <>
          <rect x="10" y="8" width="62" height="40" rx="5" className={soft} />
          <rect x="10" y="8" width="62" height="40" rx="5" className={line} />
          <path d="M4 54h74" className={line} />
          <ellipse cx="28" cy="22" rx="8" ry="3" className={line} />
          <path d="M20 22v12c0 1.7 3.6 3 8 3s8-1.3 8-3V22" className={line} />
          <ellipse cx="50" cy="26" rx="7" ry="2.6" className={line} />
          <path
            d="M43 26v9c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6v-9"
            className={line}
          />
          <circle cx="74" cy="40" r="10" className="fill-surface-layout-1" />
          <circle cx="74" cy="40" r="10" className={line} />
          <path d="M81 47l8 8" className={line} />
        </>
      )}
    </svg>
  )
}

/** One way in: a drawing, what it does, and where it goes. */
function OptionCard({
  kind,
  title,
  caption,
  primary,
  disabled,
  onClick,
  testId,
  logo,
  info,
}: {
  kind: 'scan'
  title: string
  caption: string
  primary?: boolean
  disabled?: boolean
  onClick: () => void
  testId: string
  /** Stands in for the illustration: the agent the card runs. */
  logo?: ReactNode
  /** More on what happens, behind a (?) beside the title. */
  info?: string
}) {
  return (
    <Pressable
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className={cn(
        'group flex w-full cursor-pointer items-center gap-4 rounded-card border text-left transition-all duration-fast hover:-translate-y-px hover:shadow-sm',
        primary
          ? 'border-border-primary-soft bg-surface-primary-soft px-5 py-4 text-content-primary-soft hover:bg-surface-primary-soft-hover'
          : 'border-border-layout-2 bg-surface-layout-1 px-4 py-3.5 text-content-layout-3 hover:border-border-primary-soft hover:text-content-primary-soft'
      )}
    >
      {logo ? (
        <span className="grid size-12 shrink-0 place-items-center rounded-card border border-border-layout-2 bg-surface-layout-1">
          {logo}
        </span>
      ) : (
        <Illustration kind={kind} />
      )}
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <Text
            as="span"
            level={primary ? 'label-medium' : 'label-small'}
            className="text-content-layout-1"
          >
            {title}
          </Text>
          {info && (
            <TooltipProvider delayDuration={150}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span
                    role="img"
                    aria-label={info}
                    className="inline-grid size-4 cursor-help place-items-center rounded-full border border-border-layout-2 text-[10px] leading-none text-content-layout-3 hover:text-content-layout-1"
                    onClick={(event) => event.stopPropagation()}
                    onKeyDown={(event) => event.stopPropagation()}
                    data-testid={`${testId}-info`}
                  >
                    ?
                  </span>
                </TooltipTrigger>
                <TooltipContent side="top" className="max-w-80" label={info} />
              </Tooltip>
            </TooltipProvider>
          )}
        </span>
        <Text
          as="span"
          level="caption"
          className="mt-0.5 block text-content-layout-3"
        >
          {caption}
        </Text>
      </span>
      <Icon
        name="chevron-right"
        label=""
        size="small"
        className="text-content-layout-3 transition-transform duration-fast group-hover:translate-x-0.5"
      />
    </Pressable>
  )
}

/** The decision the agent stopped to ask for, as buttons. The pick goes back to the agent as
 * its next message, in the same conversation. */
export function QuestionCard({
  run,
  onAnswered,
}: {
  run: DiscoveryRun
  onAnswered: (next: DiscoveryRun) => void
}) {
  const [other, setOther] = useState<string | null>(null)
  const [sending, setSending] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  if (!run.question) return null
  const name = run.agent?.name ?? 'Your agent'
  if (run.in_app) {
    return (
      <Text
        as="p"
        level="body-small"
        className="text-content-layout-2"
        data-testid="agent-question-in-app"
      >
        This conversation is open in {whereItIs(run.in_app)} now, so answer{' '}
        {name} there.
      </Text>
    )
  }
  const answer = (text: string, chose: 'option' | 'other') => {
    if (!text.trim()) return
    setSending(text)
    setProblem(null)
    replyToAgent(run.id, text)
      .then((next) => {
        trackEvent('agent_question_answered', {
          run_id: next.id,
          agent: run.agent?.key ?? 'unknown',
          chose,
          options: run.question?.options.length ?? 0,
        })
        onAnswered(next)
      })
      .catch((caught) =>
        setProblem(caught instanceof Error ? caught.message : String(caught))
      )
      .finally(() => setSending(null))
  }
  return (
    <div
      className="space-y-2.5 rounded-card border border-border-primary-soft bg-surface-primary-soft px-4 py-3"
      data-testid="agent-question"
    >
      <Text as="p" level="caption" className="text-content-layout-3">
        {name} needs you to decide
      </Text>
      <Text as="p" level="body-small" className="ph-mask text-content-layout-1">
        {run.question.question}
      </Text>
      <div className="flex flex-wrap gap-2">
        {run.question.options.map((option) => (
          <Button
            key={option}
            variant="primary"
            modifier="outline"
            size="small"
            label={option}
            // Long answers wrap inside the card instead of running past it.
            className="ph-mask h-auto min-w-0 max-w-full whitespace-normal py-1.5 text-left"
            innerClassName="min-w-0"
            loading={sending === option}
            disabled={sending !== null}
            onClick={() => answer(option, 'option')}
          />
        ))}
        {other === null && (
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Something else"
            disabled={sending !== null}
            onClick={() => setOther('')}
          />
        )}
      </div>
      {other !== null && (
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            answer(other, 'other')
          }}
        >
          <BaseInputText
            aria-label={`Answer ${name}`}
            value={other}
            maxLength={2000}
            autoFocus
            placeholder="Your answer"
            onChange={(event) => setOther(event.target.value)}
            className="ph-mask h-8.5 flex-1 bg-surface-layout-1 text-body-small"
          />
          <Button
            type="submit"
            variant="primary"
            modifier="solid"
            size="small"
            label="Send"
            loading={sending === other}
            disabled={!other.trim() || sending !== null}
          />
        </form>
      )}
      {problem && <Alert variant="negative" modifier="ghost" label={problem} />}
    </div>
  )
}

function ProviderTiles({
  disabled,
  onSignIn,
}: {
  disabled: boolean
  onSignIn: (provider: SignInProvider) => void
}) {
  return (
    <div className="grid grid-cols-2 gap-2 tablet:grid-cols-4">
      {SIGN_IN_PROVIDERS.map((provider) => {
        const Logo = LOGOS[provider]
        return (
          <Pressable
            key={provider}
            disabled={disabled}
            onClick={() => {
              trackEvent('find_option_chosen', { option: 'sign_in', provider })
              onSignIn(provider)
            }}
            data-testid={`sign-in-${provider}`}
            className="flex cursor-pointer flex-col items-center gap-2 rounded-panel border border-border-layout-2 bg-surface-layout-1 px-3 py-3.5 transition-colors duration-fast hover:border-border-primary-soft hover:bg-surface-primary-soft-hover"
          >
            <Logo size={24} />
            <Text as="span" level="caption" className="text-content-layout-2">
              {sourceLabel(provider)}
            </Text>
          </Pressable>
        )
      })}
    </div>
  )
}

/** The steps of an agent's setup, from what RDST saw its tools do. */
function setupSteps(run: DiscoveryRun, name: string, following = false) {
  const label = run.agent?.label ?? 'your agent'
  // Carried on in the agent's app: the conversation is going there, whatever RDST's own
  // run ended on.
  const started =
    following ||
    run.activity.some((line) => line.kind === 'say' || line.kind === 'tool')
  const added = Boolean(run.target)
  const ready = run.waiting || run.monitoring
  const failed =
    !following && (run.state === 'failed' || run.state === 'cancelled')
  const asking = !following && Boolean(run.question) && run.state !== 'running'
  const at = (done: boolean, reached: boolean): StepState =>
    done
      ? 'done'
      : !reached
        ? 'todo'
        : failed
          ? 'failed'
          : asking
            ? 'waiting'
            : 'active'
  return [
    { label: `Starting ${label}`, state: at(started, true) },
    {
      label: `Adding ${run.target ?? name} to RDST`,
      state: at(added, started),
      detail: added ? undefined : 'Reading its settings',
    },
    {
      label: 'Getting it ready to monitor',
      state: at(ready, added),
      detail: ready ? undefined : 'Checking it can be monitored',
    },
    {
      label: 'Turning on monitoring',
      state: run.monitoring
        ? 'done'
        : run.waiting
          ? 'waiting'
          : at(false, ready),
      detail: run.waiting ? 'Waiting for your click' : undefined,
    } as { label: string; state: StepState; detail?: string },
  ]
}

// The last import's results, so leaving the view and coming back doesn't look again.
const RESULTS_KEPT_MS = 5 * 60 * 1000
let lastImport: {
  run: DiscoveryRun
  search?: DiscoveryRun | null
  at: number
} | null = null

/** Forget the kept results, so the next view imports afresh. */
export function forgetLastImport() {
  lastImport = null
}

export function FindDatabases({
  onOpen,
  onSignIn,
  onFillIn,
  onAgents,
  onSetupChange,
  onPastePrompt,
  promptShown = false,
  onOpenApp,
  onEdit,
}: {
  /** Take the user to a database the agent set up, on its turn-on step when `enable`. */
  onOpen: (target: string, enable: boolean) => void
  onSignIn: (provider: SignInProvider) => void
  /** Open the connection form, with what was found when there's something. */
  onFillIn: (found: FoundDatabase | null) => void
  /** Told which agents RDST can run, once it knows. */
  onAgents?: (agents: DiscoveryAgent[]) => void
  /** Told while an agent sets a database up, so nothing else moves the user on. */
  onSetupChange?: (active: boolean) => void
  /** Show the prompt to paste into an agent the user already has open. */
  onPastePrompt?: () => void
  /** The prompt is showing already, so there's no button to show it. */
  promptShown?: boolean
  /** Open a new conversation in this desktop app with the prompt typed in. */
  onOpenApp?: (app: 'claude' | 'codex') => Promise<void>
  /** Edit the connection details of a database RDST already has. */
  onEdit?: (target: string) => void
}) {
  const [agents, setAgents] = useState<DiscoveryAgent[] | null>(null)
  const [agentKey, setAgentKey] = useState<string | null>(null)
  const [scan, setScan] = useState<DiscoveryRun | null>(() =>
    lastImport && Date.now() - lastImport.at < RESULTS_KEPT_MS
      ? lastImport.run
      : null
  )
  const [setup, setSetup] = useState<DiscoveryRun | null>(null)
  const [picked, setPicked] = useState<{ run: string; id: string } | null>(
    () => {
      const best = scan ? recommended(scan.candidates) : null
      return scan && best ? { run: scan.id, id: best.id } : null
    }
  )
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [apps, setApps] = useState<('claude' | 'codex')[]>([])
  // The agent making sense of what the scan found: its short list is what shows.
  const [search, setSearch] = useState<DiscoveryRun | null>(() =>
    lastImport && Date.now() - lastImport.at < RESULTS_KEPT_MS
      ? (lastImport.search ?? null)
      : null
  )
  const [withAgent, setWithAgent] = useState(() => Boolean(search))
  // Everything the scan found, instead of the agent's short list.
  const [showAll, setShowAll] = useState(false)
  const [asking, setAsking] = useState('')
  // A database RDST already has that it can't connect to, and why: it's fixed from here.
  const [savedFailed, setSavedFailed] = useState<{
    name: string
    message: string | null
  } | null>(null)
  const { removeTarget } = useConfigure()
  // A found database RDST couldn't connect to with what it found, and why.
  const [failed, setFailed] = useState<{
    id: string
    message: string | null
  } | null>(null)
  // The folded group the user opened.
  const [showHistory, setShowHistory] = useState<FoundGroup['kind'] | null>(
    null
  )
  const onAgentsRef = useRef(onAgents)
  onAgentsRef.current = onAgents
  const onSetupRef = useRef(onSetupChange)
  onSetupRef.current = onSetupChange

  useEffect(() => {
    let live = true
    fetchDiscoveryOptions()
      .then(({ agents: found, apps: installed }) => {
        if (!live) return
        setAgents(found)
        setApps(installed)
        onAgentsRef.current?.(found)
        trackEvent('find_view_shown', {
          agents: found.length,
          agent_keys: found.map((item) => item.key).join(','),
          apps: installed.join(','),
          signed_out: found
            .filter((item) => item.signed_in === false)
            .map((item) => item.key)
            .join(','),
        })
      })
      .catch(() => {
        if (!live) return
        setAgents([])
        onAgentsRef.current?.([])
      })
    return () => {
      live = false
    }
  }, [])

  // An agent's desktop app keeps its own sign-in: a way on when its CLI is signed out.
  const appFor = (signedOut: DiscoveryAgent) =>
    onOpenApp && apps.includes(signedOut.id)
      ? {
          name: APP_NAMES[signedOut.id],
          open: () => onOpenApp(signedOut.id),
        }
      : undefined

  // The agents again, once the user signed one in: the one that runs comes first.
  const reloadAgents = useCallback(
    () =>
      fetchDiscoveryOptions(true)
        .then(({ agents: found }) => {
          setAgents(found)
          onAgentsRef.current?.(found)
        })
        .catch(() => {}),
    []
  )

  // The import reports each place as it finishes.
  const scanId = scan?.state === 'running' ? scan.id : null
  useEffect(() => {
    if (!scanId) return
    const timer = window.setInterval(() => {
      fetchDiscovery(scanId)
        .then(setScan)
        .catch(() => {})
    }, SCAN_POLL_MS)
    return () => window.clearInterval(timer)
  }, [scanId])

  // Once it's done: what it found, for analytics, and the best one picked.
  const scanReported = useRef<string | null>(scan?.id ?? null)
  useEffect(() => {
    if (!scan || scan.state === 'running' || scanReported.current === scan.id)
      return
    scanReported.current = scan.id
    lastImport = { run: scan, search: null, at: Date.now() }
    const methods = scan.methods ?? []
    const counts = Object.fromEntries(
      methods.map((method) => [method.method, method.found])
    )
    trackEvent('find_scan_finished', {
      run_id: scan.id,
      state: scan.state,
      found: scan.candidates.length,
      already_added: scan.candidates.filter((item) => item.saved_as).length,
      hosted: scan.candidates.filter((item) => signInProvider(item)).length,
      running: scan.candidates.filter((item) =>
        item.found_by.some((line) =>
          /^(Docker container|PostgreSQL answering|MySQL answering)/.test(line)
        )
      ).length,
      from_projects: scan.candidates.filter((item) => item.project).length,
      projects_with_databases: new Set(
        scan.candidates.map((item) => item.project).filter(Boolean)
      ).size,
      seconds: scan.elapsed,
      projects: counts.projects ?? 0,
      docker: counts.docker ?? 0,
      ports: counts.ports ?? 0,
      clients: counts.clients ?? 0,
      folders:
        methods.find((method) => method.method === 'projects')?.looked.length ??
        0,
      failed: methods
        .filter((method) => method.state === 'failed')
        .map((method) => method.method)
        .join(','),
    })
    const best = recommended(scan.candidates)
    setPicked(best ? { run: scan.id, id: best.id } : null)
  }, [scan])

  // Back on this view with earlier results: what's in RDST may have changed since.
  const reopened = useRef({ scan: scan?.id, search: search?.id })
  useEffect(() => {
    const { scan: scanAgain, search: searchAgain } = reopened.current
    if (scanAgain)
      void fetchDiscovery(scanAgain)
        .then(setScan)
        .catch(() => {})
    if (searchAgain)
      void fetchDiscovery(searchAgain)
        .then(setSearch)
        .catch(() => {})
  }, [])

  // Once the scan is done, the agent makes sense of it.
  const scanDone = scan && scan.state !== 'running' ? scan.id : null
  const searchAgent = withAgent ? (agentKey ?? agents?.[0]?.key ?? null) : null
  useEffect(() => {
    if (!scanDone || !searchAgent || search) return
    let live = true
    startDiscovery(searchAgent, undefined, scanDone)
      .then((run) => live && setSearch(run))
      .catch((caught) => {
        if (!live) return
        setWithAgent(false)
        setError(caught instanceof Error ? caught.message : String(caught))
      })
    return () => {
      live = false
    }
  }, [scanDone, searchAgent, search])

  const searchId = search?.state === 'running' ? search.id : null
  useEffect(() => {
    if (!searchId) return
    const timer = window.setInterval(() => {
      fetchDiscovery(searchId)
        .then((next) => {
          setSearch(next)
          if (next.state === 'running') return
          if (lastImport) lastImport.search = next
          trackEvent('find_agent_search', {
            stage: 'finished',
            run_id: next.id,
            agent: next.agent?.key ?? 'unknown',
            state: next.state,
            found: next.candidates.length,
            seconds: next.elapsed,
          })
          const best = recommended(next.candidates)
          setPicked(best ? { run: next.id, id: best.id } : null)
        })
        .catch(() => {})
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [searchId])

  // A setup polls while it runs, and while the user carries it on in the agent's app.
  const following = Boolean(
    setup?.in_app &&
      setup.state !== 'running' &&
      !(setup.target && setup.monitoring)
  )
  const setupId =
    setup && (setup.state === 'running' || following) ? setup.id : null
  useEffect(() => {
    if (!setupId) return
    const timer = window.setInterval(() => {
      fetchDiscovery(setupId)
        .then(setSetup)
        .catch(() => {})
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [setupId])

  const settingUp = setup?.state === 'running'
  useEffect(() => {
    onSetupRef.current?.(settingUp)
  }, [settingUp])

  // The agent asks for the user's Turn on monitoring click: the database's page takes over.
  const handedOff = useRef<string | null>(null)
  useEffect(() => {
    if (!setup?.waiting || !setup.target || handedOff.current === setup.id)
      return
    handedOff.current = setup.id
    onSetupRef.current?.(false)
    onOpen(setup.target, true)
  }, [setup?.waiting, setup?.target, setup?.id, onOpen])

  // Where the user stood when the view closed, for what stops people.
  const opened = useRef(Date.now())
  const standing = useRef(() => ({
    stage: 'choosing' as FindStage,
    found: 0,
    used_agent: false,
    seconds_open: 0,
  }))
  standing.current = () => ({
    stage: (setup
      ? setup.state === 'running'
        ? setup.waiting
          ? 'awaiting_user'
          : 'setting_up'
        : setup.state === 'done'
          ? 'setup_done'
          : 'setup_failed'
      : scan
        ? scan.state === 'running'
          ? 'scanning'
          : 'picking'
        : 'choosing') as FindStage,
    found: scan?.candidates.length ?? 0,
    used_agent: Boolean(setup),
    seconds_open: Math.round((Date.now() - opened.current) / 1000),
  })
  useEffect(() => () => trackEvent('find_view_closed', standing.current()), [])

  // Only the class leaves the machine, never the message.
  useEffect(() => {
    if (error)
      trackEvent('user_error_shown', {
        surface: 'find_databases',
        error_class: userErrorClass(error),
      })
  }, [error])

  // How each setup ended, once.
  const reported = useRef(new Set<string>())
  useEffect(() => {
    if (!setup || setup.state === 'running') return
    if (reported.current.has(setup.id)) return
    reported.current.add(setup.id)
    trackEvent('find_agent_setup_finished', {
      run_id: setup.id,
      agent: setup.agent?.key ?? 'unknown',
      state: setup.state,
      reached: setup.monitoring
        ? 'monitoring'
        : setup.waiting
          ? 'awaiting_user'
          : setup.target
            ? 'getting_ready'
            : setup.activity.some((line) => line.kind !== 'rdst')
              ? 'adding'
              : 'starting',
      seconds: setup.elapsed,
      tool_steps: setup.activity.filter((line) => line.kind === 'tool').length,
    })
  }, [setup])

  const agent =
    agents?.find((item) => item.key === agentKey) ?? agents?.[0] ?? null

  /** Search this computer; with the agent, it then picks the ones the user works on. */
  const look = async (viaAgent: boolean) => {
    setError(null)
    setBusy('scan')
    setSearch(null)
    setShowAll(false)
    setWithAgent(viaAgent)
    trackEvent('find_option_chosen', {
      option: viaAgent ? 'search_with_agent' : 'scan',
      started: 'click',
    })
    try {
      const run = await startScan()
      if (!Array.isArray(run?.candidates))
        throw new Error("RDST couldn't start searching.")
      setScan(run)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(null)
    }
  }

  // The agent's short list once it's done; else (no agent, it failed or was stopped, or the
  // user asked for everything) what the scan found.
  const shortList = Boolean(
    search && search.state === 'done' && search.candidates.length && !showAll
  )
  const listRun = shortList ? search : scan
  // What's already in RDST shows on its own, first; the list is what's left to add.
  const inRdst = [
    ...new Map(
      [...(scan?.candidates ?? []), ...(search?.candidates ?? [])]
        .filter((entry) => entry.saved_as)
        .map((entry) => [entry.saved_as, entry] as const)
    ).values(),
  ]
  const found = (listRun?.candidates ?? []).filter((entry) => !entry.saved_as)
  // Names, hosts and folders show in titles and buttons too; recordings star them.
  maskInReplays(
    found.flatMap((item) => [
      item.name,
      item.host,
      item.database,
      item.user,
      projectLabel(item.project),
    ])
  )
  maskInReplays([setup?.target])
  const item =
    found.find(
      (entry) => listRun?.id === picked?.run && entry.id === picked?.id
    ) ?? null
  const pick = (entry: FoundDatabase) => {
    if (!listRun) return
    setPicked({ run: listRun.id, id: entry.id })
    trackEvent('find_database_picked', {
      source: 'scan',
      provider: entry.provider,
      environment: entry.environment,
      has_secret: readsItsOwnSecret(entry),
      found_by: evidenceKinds(entry),
    })
  }
  const acted = (
    action:
      | 'agent_setup'
      | 'add_self'
      | 'fill_in'
      | 'sign_in'
      | 'look_in_project'
      | 'look_for_more'
      | 'describe',
    extra: { agent?: string; provider?: string; ok?: boolean } = {}
  ) => trackEvent('find_action', { action, source: 'scan', ...extra })

  // Its URL or password is on disk: RDST adds it itself, no agent needed. The next screen
  // checks it, and offers the fixes (the details, the provider's allowlist, the agent) when
  // it can't connect.
  const connect = async () => {
    if (!listRun || !item) return
    setError(null)
    setBusy('connect')
    try {
      const added = await addFoundDatabase(listRun.id, item.id)
      acted('add_self', { provider: item.provider, ok: added.ok })
      if (added.ok && added.target) onOpen(added.target, true)
      else {
        setFailed({ id: item.id, message: added.message })
        // It was already in RDST: that one is what needs fixing.
        if (added.target)
          setSavedFailed({ name: added.target, message: added.message })
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(null)
    }
  }

  const lookIn = async (entry: FoundDatabase) => {
    if (!listRun || !entry.project) return
    acted('look_in_project')
    setError(null)
    setBusy(`look-${entry.id}`)
    try {
      // The list the user is looking at gets what the project names, in its place.
      const next = await lookInProject(listRun.id, entry.project)
      if (listRun.id === search?.id) setSearch(next)
      else setScan(next)
      const first = next.candidates.find(
        (other) => other.project === entry.project && other.host
      )
      if (first) setPicked({ run: next.id, id: first.id })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(null)
    }
  }

  // Open a database RDST has only once it connects; otherwise its fixes show on its row.
  const openSaved = async (name: string) => {
    setError(null)
    setBusy(`open-${name}`)
    try {
      const checked = await checkTarget(name)
      if (checked.ok) onOpen(name, true)
      else setSavedFailed({ name, message: checked.message })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(null)
    }
  }

  const fixSaved = async (
    name: string,
    message: string | null,
    project: string | null
  ) => {
    if (!agent) return
    acted('agent_setup', { agent: agent.key })
    setError(null)
    setBusy('setup')
    try {
      setSetup(await startAgentFix(agent.key, [name], message, project))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(null)
    }
  }

  const removeSaved = async (name: string) => {
    setError(null)
    setBusy(`remove-${name}`)
    try {
      await removeTarget(name)
      setSavedFailed(null)
      if (scan) setScan(await fetchDiscovery(scan.id))
      if (search) setSearch(await fetchDiscovery(search.id))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(null)
    }
  }

  // What the user tells the agent goes on in the same conversation, and its new short list
  // replaces the old one.
  const ask = async () => {
    if (!search || !asking.trim()) return
    acted('describe', { agent: search.agent?.key })
    setError(null)
    setBusy('ask')
    try {
      setSearch(await replyToAgent(search.id, asking))
      setAsking('')
      setShowAll(false)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(null)
    }
  }

  const setUp = async (problem?: string | null) => {
    if (!listRun || !item || !agent) return
    acted('agent_setup', { agent: agent.key })
    setError(null)
    setBusy('setup')
    try {
      setSetup(
        await startSetup(listRun.id, [item.id], agent.key, problem ?? undefined)
      )
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(null)
    }
  }

  // Connect manually: the connection form, filled in with what the import found.
  const connectManually = () => {
    if (!item) return
    acted('fill_in')
    onFillIn(item)
  }

  const continueIn = async (current: DiscoveryRun) => {
    if (!current.continue_in) return
    setError(null)
    try {
      await continueInApp(current.id, current.continue_in)
      // The app owns the conversation now: RDST stops offering to answer it here.
      setSetup((shown) =>
        shown?.id === current.id
          ? { ...shown, in_app: current.continue_in }
          : shown
      )
      trackEvent('find_continue_in_app', {
        run_id: current.id,
        app: current.continue_in,
        kind: current.kind,
      })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }

  if (agents === null) return null

  const errorAlert = error && (
    <Alert variant="negative" modifier="ghost" label={error} />
  )

  // Setting a database up: the agent's steps, and what it needs from the user.
  if (setup) {
    const which = setup.target ?? (item ? nameOf(item) : 'it')
    const outcome = !settingUp && !following
    const name = setup.agent?.name ?? 'Your agent'
    const title =
      following && setup.in_app
        ? `Continuing in ${setup.in_app === 'terminal' ? 'a terminal' : APP_NAMES[setup.in_app]}`
        : setup.target && setup.monitoring
          ? // However RDST's own run ended, the conversation got it there.
            `${which} is set up and monitored`
          : setup.state === 'done' && setup.question
            ? `${name} has a question about ${which}`
            : setup.state === 'running'
              ? `${name} is setting up ${which}`
              : setup.state === 'done'
                ? setup.monitoring
                  ? `${which} is set up and monitored`
                  : `${name} added ${which}`
                : setup.state === 'cancelled'
                  ? 'Setup stopped'
                  : setup.signed_out
                    ? `Sign in to ${setup.agent?.label ?? name} to set up ${which}`
                    : `${name} couldn't finish setting up ${which}`
    return (
      <section
        aria-label="Setting up your database"
        data-testid="find-databases"
        className="space-y-4"
      >
        <div className="flex items-start gap-3">
          <Text
            as="h3"
            level="label-medium"
            className="min-w-0 flex-1 text-content-layout-1"
            data-testid="find-databases-title"
          >
            {title}
          </Text>
          {settingUp && (
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Stop"
              onClick={() =>
                // Stopping a setup goes back to the list, to pick again.
                void cancelDiscovery(setup.id)
                  .then(() => setSetup(null))
                  .catch(() => setSetup(null))
              }
              data-testid="setup-stop"
            />
          )}
        </div>
        <Steps
          steps={setupSteps(setup, which, following)}
          testId="setup-steps"
        />
        {settingUp && (
          // What the agent is doing right now, and for how long, so a slow step reads as
          // slow rather than stuck.
          <>
            <Trace run={setup} testId="setup-trace" />
            <Text
              as="p"
              level="caption"
              className="text-content-layout-3"
              data-testid="setup-elapsed"
            >
              {setup.elapsed >= 60
                ? `Working for ${Math.floor(setup.elapsed / 60)} min ${setup.elapsed % 60} s`
                : `Working for ${setup.elapsed} s`}
            </Text>
          </>
        )}
        {following && setup.in_app && (
          // The app owns the conversation: RDST follows the agent's RDST calls here.
          <>
            <Text
              as="p"
              level="body-small"
              className="rdst-shimmer text-content-layout-2"
              data-testid="setup-in-app"
            >
              {name} is working in {whereItIs(setup.in_app)}. RDST updates these
              steps as it gets {which} ready.
            </Text>
            <Trace run={setup} testId="setup-trace" kinds={['rdst']} />
            <div className="flex flex-wrap items-center gap-2">
              {setup.in_app !== 'terminal' && (
                <Button
                  variant="primary"
                  modifier="solid"
                  label={`Open ${APP_NAMES[setup.in_app]}`}
                  icon="arrow-up-right"
                  iconPosition="right"
                  onClick={() => void continueIn(setup)}
                  data-testid="agent-continue"
                >
                  <AgentLogo agent={setup.in_app} className="mr-1.5" />
                </Button>
              )}
              <span className="flex-1" />
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Back to the list"
                onClick={() => setSetup(null)}
              />
            </div>
          </>
        )}
        {outcome && setup.signed_out && setup.agent && (
          <AgentSignIn
            agent={setup.agent}
            inApp={appFor(setup.agent)}
            onSignedIn={() =>
              void reloadAgents().then(() => {
                setSetup(null)
                if (item) void setUp()
              })
            }
          />
        )}
        {outcome &&
          !setup.question &&
          !setup.signed_out &&
          setup.message &&
          !(setup.monitoring && setup.state !== 'done') && (
            <Text
              as="p"
              level="body-small"
              className="ph-mask text-content-layout-2"
            >
              {firstParagraph(setup.message)}
            </Text>
          )}
        {outcome && <QuestionCard run={setup} onAnswered={setSetup} />}
        {outcome && (
          <div className="flex flex-wrap items-center gap-2">
            {setup.target ? (
              <Button
                variant="primary"
                modifier="solid"
                label={`Open ${setup.target}`}
                onClick={() =>
                  onOpen(setup.target as string, !setup.monitoring)
                }
                data-testid="setup-open"
              />
            ) : (
              item &&
              !setup.question && (
                // The agent didn't get it added: the form, filled in, is always a way on.
                <Button
                  variant="primary"
                  modifier="solid"
                  label="Connect manually"
                  onClick={connectManually}
                  data-testid="setup-manual"
                />
              )
            )}
            {setup.continue_in ? (
              <Button
                variant="primary"
                modifier={setup.target && !setup.in_app ? 'ghost' : 'solid'}
                label={`Continue in ${APP_NAMES[setup.continue_in]}`}
                icon="arrow-up-right"
                iconPosition="right"
                onClick={() => void continueIn(setup)}
                data-testid="agent-continue"
              />
            ) : (
              setup.resume_command && (
                <Button
                  variant="primary"
                  modifier={setup.target ? 'ghost' : 'solid'}
                  label="Continue in a terminal"
                  icon="arrow-up-right"
                  iconPosition="right"
                  onClick={() =>
                    void continueInTerminal(setup.id)
                      .then(() => {
                        // One terminal: the conversation carries on there.
                        setSetup((shown) =>
                          shown?.id === setup.id
                            ? { ...shown, in_app: 'terminal' }
                            : shown
                        )
                        trackEvent('find_continue_in_app', {
                          run_id: setup.id,
                          app: 'terminal',
                          kind: setup.kind,
                        })
                      })
                      .catch((caught) =>
                        setError(
                          caught instanceof Error
                            ? caught.message
                            : String(caught)
                        )
                      )
                  }
                  data-testid="agent-continue-terminal"
                />
              )
            )}
            <span className="flex-1" />
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Back to the list"
              onClick={() => setSetup(null)}
            />
          </div>
        )}
        {errorAlert}
      </section>
    )
  }

  // The coding agent's ways in, under whatever RDST shows.
  const stageIn = onOpenApp ? apps : []
  const agentWays = (stageIn.length > 0 || onPastePrompt) && (
    <div
      className="flex flex-wrap items-center gap-x-1 gap-y-1 border-t border-border-layout-2 pt-3"
      data-testid="agent-ways"
    >
      <Text as="span" level="caption" className="text-content-layout-3">
        {found.length > 0
          ? 'Or work in your own app:'
          : 'Rather have your coding agent do it?'}
      </Text>
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label="How your coding agent helps"
              className="mr-1 inline-grid size-4 cursor-help place-items-center rounded-full border border-border-layout-2 text-[10px] leading-none text-content-layout-3 hover:text-content-layout-1"
            >
              ?
            </button>
          </TooltipTrigger>
          <TooltipContent
            side="top"
            className="max-w-72"
            label="Your coding agent looks through your project for its database, adds it to RDST and helps turn monitoring on. Start it in your Claude or Codex app, or copy the prompt into any agent you have open."
          />
        </Tooltip>
      </TooltipProvider>
      {onOpenApp &&
        stageIn.map((app) => (
          <Button
            key={app}
            variant="primary"
            modifier="ghost"
            size="small"
            label={`Start in the ${APP_NAMES[app]} app`}
            icon="arrow-up-right"
            iconPosition="right"
            loading={busy === `app-${app}`}
            disabled={busy !== null && busy !== 'scan'}
            onClick={() => {
              trackEvent('find_option_chosen', { option: 'open_app', app })
              setBusy(`app-${app}`)
              void onOpenApp(app)
                .catch((caught) =>
                  setError(
                    caught instanceof Error ? caught.message : String(caught)
                  )
                )
                .finally(() => setBusy(null))
            }}
            data-testid={app === 'claude' ? 'open-in-app' : `open-in-${app}`}
          >
            <AgentLogo agent={app} size={14} className="mr-1.5" />
          </Button>
        ))}
      {onPastePrompt && !promptShown && (
        <TooltipProvider delayDuration={150}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Show the prompt"
                disabled={busy !== null && busy !== 'scan'}
                onClick={() => {
                  trackEvent('find_option_chosen', { option: 'paste_prompt' })
                  onPastePrompt()
                }}
                data-testid="agent-prompt-reveal"
              />
            </TooltipTrigger>
            <TooltipContent
              side="top"
              className="max-w-72"
              label={PROMPT_SUMMARY}
            />
          </Tooltip>
        </TooltipProvider>
      )}
    </div>
  )
  const providers = (
    <div>
      <Text as="p" level="caption" className="mb-2 text-content-layout-3">
        Or sign in to where it's hosted
      </Text>
      <ProviderTiles disabled={busy === 'setup'} onSignIn={onSignIn} />
    </div>
  )

  const agentName = agent?.name ?? 'your agent'
  const searching =
    busy === 'scan' ||
    scan?.state === 'running' ||
    Boolean(withAgent && scan && (!search || search.state === 'running'))

  // Nothing searched yet: the search button. Searching: the scan's steps, then the
  // agent's, with what it's doing right now.
  if (!scan || searching) {
    const methods = (scan?.methods ?? SCAN_PLACES).filter(
      (method) => method.method !== 'reach'
    )
    const looked = methods.every((method) => method.state !== 'running')
    const reach = scan?.methods?.find((method) => method.method === 'reach')
    const total = methods.reduce((sum, method) => sum + method.found, 0)
    const steps: { label: string; state: StepState; detail?: ReactNode }[] = [
      {
        label: 'Looking on this computer',
        state: looked ? 'done' : 'active',
        detail: looked
          ? `${total} traces in your projects, history, Docker and saved connections`
          : methods
              .filter((method) => method.state === 'running')
              .map((method) => method.label)
              .join(', '),
      },
      {
        label: 'Checking which ones are online',
        state: reach?.state === 'done' ? 'done' : looked ? 'active' : 'todo',
        detail:
          reach?.state === 'done'
            ? `${countLabel(reach.found)} online`
            : undefined,
      },
    ]
    if (withAgent)
      steps.push({
        label: `${agentName} is picking the ones you work on`,
        state:
          search?.state === 'running'
            ? 'active'
            : search?.state === 'done'
              ? 'done'
              : 'todo',
      })
    return (
      <section
        aria-label="Find your database"
        data-testid="find-databases"
        className="space-y-4"
      >
        {scan || busy === 'scan' ? (
          <div className="rounded-card border border-border-layout-2 bg-surface-layout-1 px-4 py-4">
            <div className="mb-3 flex items-center gap-3">
              <Text
                as="h3"
                level="label-medium"
                className="min-w-0 flex-1 text-content-layout-1"
                data-testid="find-databases-title"
              >
                {withAgent
                  ? `Searching for databases with ${agentName}`
                  : 'Searching this computer'}
              </Text>
              {search?.state === 'running' && (
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  label="Stop"
                  onClick={() =>
                    void cancelDiscovery(search.id)
                      .then(setSearch)
                      .catch(() => {})
                  }
                  data-testid="search-stop"
                />
              )}
            </div>
            <Steps testId="scan-steps" steps={steps} />
            {search?.state === 'running' && <Trace run={search} />}
          </div>
        ) : agent ? (
          <>
            <OptionCard
              kind="scan"
              primary
              title={`Search for databases with ${agent.name}`}
              caption={`${agent.label} helps search for databases on this computer and figures out the ones you've been working with most recently. ${agent.name} can then drive the setup in RDST.`}
              onClick={() => void look(true)}
              testId="search-with-agent"
              logo={<AgentLogo agent={agent.id} size={26} />}
              info={`RDST looks through your projects, agent sessions, terminal history, Docker and running databases to find the ones you've used recently. Then ${agent.label} picks out the ones you've worked on most and suggests those. It's read-only, so nothing on your computer or your databases changes.`}
            />
            <div className="-mt-2 flex justify-end">
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Search on this computer"
                onClick={() => void look(false)}
                data-testid="scan-computer"
              />
            </div>
          </>
        ) : (
          <OptionCard
            kind="scan"
            primary
            title="Search on this computer"
            caption="Your recent projects, Docker and the databases running here, in a few seconds."
            onClick={() => void look(false)}
            testId="scan-computer"
          />
        )}
        {!scan && providers}
        {!scan && !agent && agentWays}
        {errorAlert}
      </section>
    )
  }

  // What the import found, by project.
  const best = recommended(found)
  const groups = groupByProject(found)
  const grouped = groups.length > 1
  const provider = item ? signInProvider(item) : null
  const canConnect = Boolean(item?.host && readsItsOwnSecret(item))
  const name = agent?.name ?? 'your agent'
  // The agent's button with its picker beside it, never wrapping apart.
  const agentSetup = (modifier: 'solid' | 'ghost') =>
    agent && (
      <span className="inline-flex items-center">
        <Button
          variant="primary"
          modifier={modifier}
          label={`Set it up with ${agent.label}`}
          loading={busy === 'setup'}
          disabled={busy !== null}
          onClick={() => void setUp()}
          data-testid="agent-setup"
        >
          <AgentLogo agent={agent.id} className="mr-1.5" />
        </Button>
        <AgentSwitch
          agents={agents}
          agent={agent}
          onPick={(next) => setAgentKey(next.key)}
        />
      </span>
    )
  // No agent to run here, but its desktop app can take the prompt.
  const appSetup = onOpenApp && apps[0] && (
    <Button
      variant="primary"
      modifier="solid"
      label={`Set it up in the ${APP_NAMES[apps[0]]} app`}
      icon="arrow-up-right"
      iconPosition="right"
      loading={busy === `app-${apps[0]}`}
      disabled={busy !== null}
      onClick={() => {
        const app = apps[0] as 'claude' | 'codex'
        acted('agent_setup', { agent: `${app}:app` })
        setBusy(`app-${app}`)
        void onOpenApp(app)
          .catch((caught) =>
            setError(caught instanceof Error ? caught.message : String(caught))
          )
          .finally(() => setBusy(null))
      }}
      data-testid="agent-setup-in-app"
    >
      <AgentLogo agent={apps[0] as 'claude' | 'codex'} className="mr-1.5" />
    </Button>
  )
  return (
    <section
      aria-label="Your databases"
      data-testid="find-databases"
      className="space-y-4"
    >
      <div>
        <Text
          as="h3"
          level="label-medium"
          className="block text-content-layout-1"
          data-testid="find-databases-title"
        >
          {shortList
            ? `${search?.agent?.name ?? agentName} suggests ${countLabel(found.length)}. Pick one to set up.`
            : found.length
              ? showAll
                ? `Everything RDST found (${found.length})`
                : `Found ${countLabel(found.length)}. Pick one to set up.`
              : scan.state === 'failed'
                ? "RDST couldn't finish searching"
                : 'No databases found on this computer'}
        </Text>
        <Text as="p" level="caption" className="text-content-layout-3">
          {shortList
            ? `${search?.agent?.name ?? agentName} went through the ${countLabel(scan.candidates.length)} RDST found on this computer.`
            : search && search.state !== 'done' && !showAll
              ? search.signed_out
                ? `${search.agent?.label ?? agentName} is signed out, so here's what RDST found.`
                : `${search.agent?.name ?? agentName} didn't finish, so here's what RDST found.`
              : found.length === 0
                ? "Sign in to where it's hosted, or connect it manually."
                : null}
        </Text>
        {search?.state === 'done' && scan.candidates.length > 0 && (
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            className="-ml-2"
            label={
              showAll
                ? `Back to ${search.agent?.name ?? agentName}'s suggestions`
                : `See everything RDST found (${scan.candidates.length})`
            }
            onClick={() => {
              setShowAll(!showAll)
              setPicked(null)
            }}
            data-testid="show-all"
          />
        )}
        {search?.signed_out && search.agent && (
          <div className="mt-2">
            <AgentSignIn
              agent={search.agent}
              inApp={appFor(search.agent)}
              retryLabel="Search again"
              onSignedIn={() =>
                void reloadAgents().then(() => {
                  lastImport = lastImport && { ...lastImport, search: null }
                  setSearch(null)
                })
              }
            />
          </div>
        )}
        {scan.state === 'failed' && (
          <Button
            variant="primary"
            modifier="outline"
            size="small"
            label="Search again"
            className="mt-2"
            loading={busy === 'scan'}
            onClick={() => {
              lastImport = null
              setScan(null)
              void look(withAgent)
            }}
            data-testid="import-again"
          />
        )}
      </div>

      {found.length === 0 && agentWays}

      {inRdst.length > 0 && (
        <section
          aria-label="Already in RDST"
          data-testid="already-in-rdst"
          className="rounded-card border border-border-positive-soft bg-surface-positive-soft px-4 py-2.5"
        >
          <Text as="p" level="caption" className="mb-1 text-content-layout-2">
            Already in RDST
          </Text>
          <ul className="m-0 list-none space-y-1 p-0">
            {inRdst.map((entry) => (
              <Fragment key={entry.saved_as}>
                <li className="flex items-center gap-2">
                  <Icon
                    name="tick"
                    label=""
                    size="small"
                    className="shrink-0 text-content-positive-soft"
                  />
                  <Text
                    as="span"
                    level="label-small"
                    className="ph-mask min-w-0 truncate text-content-layout-1"
                  >
                    {entry.saved_as}
                  </Text>
                  <Text
                    as="span"
                    level="caption"
                    className="ph-mask min-w-0 flex-1 truncate text-content-layout-3"
                  >
                    {`${engineLabel(entry)} · ${whereLabel(entry)}`}
                  </Text>
                  {savedFailed?.name !== entry.saved_as && (
                    <Button
                      variant="primary"
                      modifier="ghost"
                      size="small"
                      label="Open"
                      loading={busy === `open-${entry.saved_as}`}
                      disabled={busy !== null}
                      onClick={() => void openSaved(entry.saved_as as string)}
                      data-testid="already-open"
                    />
                  )}
                </li>
                {savedFailed && savedFailed.name === entry.saved_as && (
                  <li
                    className="space-y-2 pb-1 pl-6"
                    data-testid="already-failed"
                  >
                    <Text
                      as="p"
                      level="caption"
                      className="ph-mask text-content-layout-2"
                    >
                      {`RDST can't connect to ${entry.saved_as}${savedFailed.message ? `: ${savedFailed.message}` : '.'}`}
                    </Text>
                    <div className="flex flex-wrap items-center gap-2">
                      {agent && (
                        <Button
                          variant="primary"
                          modifier="solid"
                          size="small"
                          label={`Have ${agent.label} fix it`}
                          loading={busy === 'setup'}
                          disabled={busy !== null}
                          onClick={() =>
                            void fixSaved(
                              entry.saved_as as string,
                              savedFailed.message,
                              entry.project
                            )
                          }
                          data-testid="already-fix"
                        >
                          <AgentLogo
                            agent={agent.id}
                            size={14}
                            className="mr-1.5"
                          />
                        </Button>
                      )}
                      {onEdit && (
                        <Button
                          variant="primary"
                          modifier="ghost"
                          size="small"
                          label="Edit connection details"
                          disabled={busy !== null}
                          onClick={() => onEdit(entry.saved_as as string)}
                          data-testid="already-edit"
                        />
                      )}
                      <Button
                        variant="primary"
                        modifier="ghost"
                        size="small"
                        label="Remove from RDST"
                        loading={busy === `remove-${entry.saved_as}`}
                        disabled={busy !== null}
                        onClick={() =>
                          void removeSaved(entry.saved_as as string)
                        }
                        data-testid="already-remove"
                      />
                    </div>
                  </li>
                )}
              </Fragment>
            ))}
          </ul>
        </section>
      )}

      {found.length > 0 && (
        <section
          aria-label="Found databases"
          className="max-h-96 overflow-y-auto rounded-card border border-border-layout-2 bg-surface-layout-1"
        >
          {groups.map((group) => {
            const folded =
              group.kind === 'history' &&
              !(showHistory === group.kind) &&
              !group.items.some((entry) => entry.id === item?.id)
            return (
              <div key={group.project ?? group.kind} data-testid="found-group">
                {grouped && (
                  <div className="sticky top-0 z-1 flex items-center gap-2 border-y border-border-layout-2 bg-surface-layout-2 px-4 py-2">
                    <Icon
                      name={
                        group.kind === 'project' ? 'folder-file' : 'database'
                      }
                      label=""
                      size="small"
                      className="shrink-0 text-content-layout-3"
                    />
                    <Text
                      as="p"
                      level="label-small"
                      className="ph-mask min-w-0 flex-1 truncate text-content-layout-1"
                    >
                      {group.kind === 'project'
                        ? projectLabel(group.project)
                        : group.kind === 'history'
                          ? `Only seen in your agent and terminal history (${group.items.length})`
                          : 'Other databases'}
                    </Text>
                    {group.kind === 'history' && (
                      <Button
                        variant="primary"
                        modifier="ghost"
                        size="small"
                        label={folded ? 'Show' : 'Hide'}
                        onClick={() =>
                          setShowHistory(folded ? group.kind : null)
                        }
                        data-testid={`found-${group.kind}-toggle`}
                      />
                    )}
                  </div>
                )}
                {!folded && (
                  <ul className="divide-y divide-border-layout-2">
                    {group.items.map((entry) =>
                      entry.deferred ? (
                        <WaitingProject
                          key={entry.id}
                          found={entry}
                          busy={busy === `look-${entry.id}`}
                          onLook={() => void lookIn(entry)}
                        />
                      ) : (
                        <FoundOption
                          key={entry.id}
                          found={entry}
                          picked={item?.id === entry.id}
                          best={entry.id === best?.id && found.length > 1}
                          grouped={grouped && group.kind === 'project'}
                          onPick={() => pick(entry)}
                        />
                      )
                    )}
                  </ul>
                )}
              </div>
            )
          })}
        </section>
      )}

      {item && (
        <div
          className="space-y-2 rounded-card border border-border-layout-2 bg-surface-layout-2/40 px-4 py-3"
          data-testid="picked-actions"
        >
          {item.saved_as ? (
            <Button
              variant="primary"
              modifier="solid"
              label={`Open ${item.saved_as}`}
              onClick={() => onOpen(item.saved_as as string, true)}
              data-testid="picked-open"
            />
          ) : failed?.id === item.id ? (
            // Connecting with what RDST found didn't work: the fixes, right here.
            <div className="space-y-2" data-testid="picked-failed">
              <Text as="p" level="body-small" className="text-content-layout-1">
                {`RDST couldn't connect to ${nameOf(item)} with what it found.`}
              </Text>
              {failed.message && (
                <Text
                  as="p"
                  level="caption"
                  className="ph-mask text-content-layout-3"
                >
                  {failed.message}
                </Text>
              )}
              <div className="flex flex-wrap items-center gap-2">
                {agent ? (
                  <Button
                    variant="primary"
                    modifier="solid"
                    label={`Have ${agent.label} fix it`}
                    loading={busy === 'setup'}
                    disabled={busy !== null}
                    onClick={() => void setUp(failed.message)}
                    data-testid="picked-fix"
                  >
                    <AgentLogo agent={agent.id} className="mr-1.5" />
                  </Button>
                ) : (
                  appSetup
                )}
                {provider && (
                  <Button
                    variant="primary"
                    modifier="ghost"
                    label={`Sign in to ${sourceLabel(provider)}`}
                    disabled={busy !== null}
                    onClick={() => {
                      acted('sign_in', { provider })
                      onSignIn(provider)
                    }}
                    data-testid="picked-sign-in"
                  />
                )}
                <Button
                  variant="primary"
                  modifier="ghost"
                  label="Enter the details yourself"
                  disabled={busy !== null}
                  onClick={connectManually}
                  data-testid="picked-manual"
                />
              </div>
              <Text as="p" level="caption" className="text-content-layout-3">
                {agent
                  ? `${agent.name} starts from what went wrong, like a changed password or a jump host, and asks you before changing anything.`
                  : provider
                    ? `If ${sourceLabel(provider)} only lets in addresses it knows, sign in and RDST adds yours.`
                    : 'Fix the password or the address, and RDST checks again.'}
              </Text>
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                {canConnect ? (
                  <Button
                    variant="primary"
                    modifier="solid"
                    label={`Connect ${nameOf(item)}`}
                    loading={busy === 'connect'}
                    disabled={busy !== null}
                    onClick={() => void connect()}
                    data-testid="picked-connect"
                  />
                ) : provider && !item.incomplete ? (
                  <>
                    <Button
                      variant="primary"
                      modifier="solid"
                      label={`Sign in to ${sourceLabel(provider)}`}
                      disabled={busy !== null}
                      onClick={() => {
                        acted('sign_in', { provider })
                        onSignIn(provider)
                      }}
                      data-testid="picked-sign-in"
                    />
                    {agentSetup('ghost')}
                  </>
                ) : (
                  <>
                    {agent ? agentSetup('solid') : appSetup}
                    {item.host && (
                      <Button
                        variant="primary"
                        modifier={agent || appSetup ? 'ghost' : 'solid'}
                        label="Connect manually"
                        disabled={busy !== null}
                        onClick={connectManually}
                        data-testid="picked-manual"
                      />
                    )}
                  </>
                )}
              </div>
              <Text as="p" level="caption" className="text-content-layout-3">
                {canConnect
                  ? `RDST connects with the ${item.url_file ? 'URL you used before' : 'details in your project'}. If that doesn't work, you can have ${name} fix it.`
                  : item.incomplete
                    ? `RDST saw this project uses a database but not where it runs. ${agent ? `${name} works it out from the project${item.secrets ? ` and ${item.secrets}` : ''}, and asks you before changing anything.` : 'Your coding agent can work it out from the project.'}`
                    : provider
                      ? `RDST imports ${nameOf(item)} from ${sourceLabel(provider)}${agent ? `, or ${name} finds its password and sets it up` : ''}.`
                      : agent
                        ? item.environment === 'production'
                          ? `This is a production database: ${name} adds it and checks it, but won't change it.`
                          : `${name} finds the password, adds the database and gets it ready to monitor. You see each step here, and it asks you before changing anything.`
                        : null}
              </Text>
            </>
          )}
        </div>
      )}

      {search && search.state !== 'running' && (
        // The same conversation: what the user says goes to the agent, and its new short
        // list replaces this one.
        <form
          className="flex items-center gap-2"
          data-testid="ask-agent"
          onSubmit={(event) => {
            event.preventDefault()
            void ask()
          }}
        >
          <BaseInputText
            aria-label={`Tell ${search.agent?.name ?? agentName} what you're looking for`}
            value={asking}
            maxLength={500}
            placeholder={`Not here? Tell ${search.agent?.name ?? agentName} what you're looking for`}
            onChange={(event) => setAsking(event.target.value)}
            className="ph-mask h-8.5 flex-1 bg-surface-layout-1 text-body-small"
          />
          <Button
            type="submit"
            variant="primary"
            modifier="outline"
            size="small"
            label="Send"
            loading={busy === 'ask'}
            disabled={!asking.trim() || busy !== null}
            data-testid="ask-agent-send"
          />
        </form>
      )}

      {providers}
      {found.length > 0 && agentWays}
      {errorAlert}
    </section>
  )
}

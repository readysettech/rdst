// The first-run tour: four short videos of the real app. Each plays on its own and moves on
// when it ends; Back, Next and the step bars move by hand, and Skip goes straight to the
// coding agent prompt. A video that can't play falls back to the same scene drawn live with motion.
import { Button } from '@rs/ui-new/button'
import { ReadysetLogo } from '@rs/ui-new/logo'
import { Modal, ModalContent, ModalContentContainer } from '@rs/ui-new/modal'
import { AnimatePresence, m, useReducedMotion } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import {
  type ComponentType,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react'
import { useOpenAddDatabase } from '../../components/configure/addDatabase'
import { markFirstRunPopupShown } from '../../components/GetStarted'
import { type OnboardingStep, trackEventOnce } from '../../lib/analytics'
import { isContainerRuntime } from '../../lib/desktop'
import { useSystemStatus } from '../../lib/useSystemStatus'
import {
  clearTourStep,
  markTourDone,
  onOpenTour,
  PRIVACY_DOC_URL,
  PRIVACY_POLICY_URL,
  savedTourStep,
  saveTourStep,
  tourPending,
} from './onboardingState'
import { useSceneClock } from './sceneClock'
import { AGENT_DURATION, AgentScene } from './scenes/AgentScene'
import { CONNECT_DURATION, ConnectScene } from './scenes/ConnectScene'
import { INBOX_DURATION, InboxScene } from './scenes/InboxScene'
import { STAGE_H, STAGE_W } from './scenes/parts'
import { WATCH_DURATION, WatchScene } from './scenes/WatchScene'
import { AuthorityNote } from './TrustNote'

interface TourStep {
  key: OnboardingStep
  title: string
  body: string
  /** The body for a server-mode RDST, where the user's coding agent isn't beside it. */
  serverBody?: string
  /** A still that replaces the video and scene in server mode, where they'd show the desktop flow. */
  serverStill?: { caption: string; lines: string[] }
  /** Shows who's behind RDST, where people decide whether to trust it. */
  authority?: boolean
  /** The scene's video, by file stem under /onboarding (webm and a poster png). */
  video: string
  /** The live scene: the fallback when the video can't play. */
  duration: number
  Scene: ComponentType<{ t: number }>
}

/** Where a scene's video files are served from; the desktop app serves them on its origin. */
export function videoSources(stem: string) {
  const base = `/onboarding/${stem}`
  return { webm: `${base}.webm`, poster: `${base}.png` }
}

export const TOUR_STEPS: TourStep[] = [
  {
    key: 'tour-connect',
    title: 'Connect your database',
    body: "Paste a connection string, or hand the job to Claude or any coding agent that's open in your project. It finds the database your app uses and sets it up for you.",
    serverBody:
      "Paste a connection string, or sign in to where it's hosted. On Kubernetes, RDST can also search its cluster for the PostgreSQL and MySQL databases it can reach.",
    serverStill: {
      caption: 'Search this cluster',
      lines: [
        'pg-rw.database.svc.cluster.local:5432',
        'mysql.inventory.svc.cluster.local:3306',
      ],
    },
    authority: true,
    video: 'tour-connect',
    duration: CONNECT_DURATION,
    Scene: ConnectScene,
  },
  {
    key: 'tour-watch',
    title: 'RDST watches in the background',
    body: "Every minute, RDST reads your database's query statistics: which queries ran, how often, and how long they took. It notices when one gets slower.",
    video: 'tour-watch',
    duration: WATCH_DURATION,
    Scene: WatchScene,
  },
  {
    key: 'tour-inbox',
    title: 'Fixes land in your Inbox',
    body: "When something's slow, you get an Inbox item that says why, with the fix and the SQL to run. After you run it, RDST checks that it worked.",
    video: 'tour-inbox',
    duration: INBOX_DURATION,
    Scene: InboxScene,
  },
  {
    key: 'tour-agent',
    title: 'Ask your coding agent what RDST found',
    body: "Claude or any coding agent can ask RDST what's slow through RDST's own tools. It gets the query and the fix, writes the change into your project, and marks the Inbox item resolved.",
    serverBody:
      'Claude Code or Codex on your own computer connects to this server from "Connect Claude or Codex" on a database\'s page, which hands you a command with an agent key. It gets the query and the fix, writes the change into your project, and marks the Inbox item resolved.',
    serverStill: {
      caption: 'Connect Claude Code to this server',
      lines: [
        'claude mcp add --transport http rdst \\',
        '  https://rdst.example.com/mcp \\',
        '  --header "Authorization: Bearer rdst_ak_…"',
      ],
    },
    video: 'tour-agent',
    duration: AGENT_DURATION,
    Scene: AgentScene,
  },
]

export type TourExit = 'done' | 'skip' | 'close'

/** The stage at its design size, scaled to the width it's given. */
function Stage({ children }: { children: ReactNode }) {
  const frame = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)
  useLayoutEffect(() => {
    const element = frame.current
    if (!element) return
    const measure = () => {
      const width = element.clientWidth
      if (width > 0) setScale(width / STAGE_W)
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return (
    <div
      ref={frame}
      aria-hidden="true"
      className="ph-no-capture relative w-full overflow-hidden"
      style={{ aspectRatio: `${STAGE_W} / ${STAGE_H}` }}
    >
      <div
        className="absolute top-0 left-0 origin-top-left"
        style={{
          width: STAGE_W,
          height: STAGE_H,
          transform: `scale(${scale})`,
        }}
      >
        {children}
      </div>
    </div>
  )
}

function StepBars({
  index,
  progress,
  onPick,
}: {
  index: number
  progress: number
  onPick: (index: number) => void
}) {
  return (
    <div className="flex gap-1.5">
      {TOUR_STEPS.map((step, i) => (
        <Pressable
          key={step.key}
          aria-label={`Step ${i + 1}: ${step.title}`}
          aria-current={i === index ? 'step' : undefined}
          onClick={() => onPick(i)}
          className="group flex h-4 flex-1 cursor-pointer items-center rounded-pill"
        >
          <span className="relative h-1 w-full overflow-hidden rounded-pill bg-content-layout-3/30 transition-colors duration-fast group-hover:bg-content-layout-3/50">
            <span
              className="absolute inset-y-0 left-0 rounded-pill bg-content-layout-1"
              style={{
                width: `${i < index ? 100 : i === index ? progress * 100 : 0}%`,
              }}
            />
          </span>
        </Pressable>
      ))}
    </div>
  )
}

const quietLink =
  'underline decoration-border-layout-2 underline-offset-2 transition-colors duration-fast hover:text-content-layout-1'

/** A static frame for a step whose video shows the desktop flow. */
function ServerStill({ caption, lines }: { caption: string; lines: string[] }) {
  return (
    <div
      className="w-full rounded-lg border border-border-layout-2 bg-surface-layout-1 p-5"
      data-testid="onboarding-server-still"
    >
      <Text as="p" level="label-small" className="text-content-layout-2">
        {caption}
      </Text>
      <pre className="mt-3 overflow-x-auto whitespace-pre font-mono text-body-small text-content-layout-1">
        {lines.join('\n')}
      </pre>
    </div>
  )
}

/** A scene's video, framed like a window on the stage. Its parent decides when it plays. */
function SceneVideo({
  stem,
  playing,
  videoRef,
  onEnded,
  onError,
  onBlocked,
}: {
  stem: string
  playing: boolean
  videoRef: RefObject<HTMLVideoElement | null>
  onEnded: () => void
  onError: () => void
  /** The platform refused to autoplay; the tour waits for Play. */
  onBlocked: () => void
}) {
  const sources = videoSources(stem)
  // Steps cross-fade, so each video mounts after the step changes and starts itself.
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    if (!playing) {
      video.pause()
      return
    }
    const attempt = video.play() as Promise<void> | undefined
    attempt?.catch?.((error: unknown) => {
      if ((error as { name?: string })?.name === 'NotAllowedError') onBlocked()
    })
  }, [videoRef, playing, onBlocked])
  return (
    <video
      ref={videoRef}
      src={sources.webm}
      poster={sources.poster}
      muted
      playsInline
      preload="auto"
      aria-hidden="true"
      tabIndex={-1}
      onEnded={onEnded}
      onError={onError}
      data-testid="onboarding-video"
      className="ph-no-capture block w-full rounded-panel border border-border-layout-2 bg-surface-layout-2 shadow-elevation-3"
      style={{ aspectRatio: `${STAGE_W} / ${STAGE_H}` }}
    />
  )
}

/** 0 to 1 through the video while it plays, read every frame for a smooth step bar. */
function useVideoProgress(
  videoRef: RefObject<HTMLVideoElement | null>,
  active: boolean,
  scene: string
): number {
  const [progress, setProgress] = useState(0)
  useEffect(() => {
    setProgress(0)
    if (!active) return
    let frame = 0
    const read = () => {
      const video = videoRef.current
      if (video?.duration)
        setProgress(Math.min(1, video.currentTime / video.duration))
      frame = requestAnimationFrame(read)
    }
    frame = requestAnimationFrame(read)
    return () => cancelAnimationFrame(frame)
  }, [videoRef, active, scene])
  return progress
}

function TourBody({
  start,
  onFinish,
  onStep,
}: {
  start: number
  onFinish: (exit: TourExit, step: number) => void
  /** Told each step as it shows, so a close from outside the body knows where it was. */
  onStep: (step: number) => void
}) {
  const reduced = Boolean(useReducedMotion())
  const [index, setIndex] = useState(() =>
    Math.max(0, Math.min(TOUR_STEPS.length - 1, start))
  )
  const [hovering, setHovering] = useState(false)
  // Paused by hand. With reduced motion a video waits on its poster until Play.
  const [held, setHeld] = useState(reduced)
  const [hidden, setHidden] = useState(false)
  const [failed, setFailed] = useState<Record<string, boolean>>({})
  const videoRef = useRef<HTMLVideoElement>(null)
  const step = TOUR_STEPS[index]
  const last = index === TOUR_STEPS.length - 1
  const still = isContainerRuntime() ? step.serverStill : undefined
  const useVideo = !still && !failed[step.key]

  const complete = useCallback((at: number) => {
    const key = TOUR_STEPS[at].key
    trackEventOnce(`tour-completed-${key}`, 'onboarding_step_completed', {
      step: key,
    })
  }, [])

  const go = useCallback(
    (next: number) => {
      if (next > index) for (let i = index; i < next; i += 1) complete(i)
      setIndex(Math.max(0, Math.min(TOUR_STEPS.length - 1, next)))
    },
    [index, complete]
  )

  useEffect(() => onStep(index), [onStep, index])

  useEffect(() => {
    trackEventOnce(`tour-viewed-${step.key}`, 'onboarding_step_viewed', {
      step: step.key,
    })
  }, [step.key])

  useEffect(() => {
    const update = () => setHidden(document.visibilityState === 'hidden')
    document.addEventListener('visibilitychange', update)
    return () => document.removeEventListener('visibilitychange', update)
  }, [])

  // Each step starts on its poster under reduced motion.
  useEffect(() => {
    if (reduced) setHeld(true)
  }, [reduced, index])

  const advance = () => {
    // The last scene holds on its final frame.
    if (!last) go(index + 1)
  }

  const videoPlaying = useVideo && !held && !hidden
  const blocked = useCallback(() => setHeld(true), [])

  const videoProgress = useVideoProgress(videoRef, videoPlaying, step.key)

  const t = useSceneClock({
    scene: index,
    duration: step.duration,
    paused: useVideo || hovering || held || hidden,
    reduced,
    onDone: advance,
  })

  const finish = (exit: TourExit) => {
    if (exit === 'done') complete(index)
    onFinish(exit, index)
  }

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'ArrowRight' && !last) go(index + 1)
    else if (event.key === 'ArrowLeft' && index > 0) go(index - 1)
  }

  const progress = useVideo
    ? reduced && held
      ? 1
      : videoProgress
    : reduced
      ? 1
      : t / step.duration
  const { Scene } = step
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: arrow keys page the tour from anywhere in the dialog
    <div
      className="grid min-h-0 grid-cols-1 tablet:grid-cols-[minmax(0,21rem)_minmax(0,1fr)]"
      onKeyDown={onKeyDown}
      data-testid="onboarding-tour"
      data-step={index}
    >
      <div className="order-2 flex min-h-0 flex-col px-7 pt-6 pb-6 tablet:order-1 tablet:pt-7">
        <div className="hidden items-center gap-2 text-label-small text-content-layout-2 tablet:flex">
          <ReadysetLogo width={18} height={18} />
          Welcome to RDST
        </div>
        <div className="tablet:mt-7">
          <StepBars index={index} progress={progress} onPick={go} />
        </div>
        <Text
          as="p"
          level="caption"
          className="mt-5 text-content-layout-3 tabular-nums"
        >
          Step {index + 1} of {TOUR_STEPS.length}
        </Text>
        <AnimatePresence mode="wait" initial={false}>
          <m.div
            key={step.key}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.2, ease: [0, 0, 0.2, 1] }}
            aria-live="polite"
          >
            <Text
              as="h2"
              level="headline-2"
              className="mt-1 text-content-layout-1"
            >
              {step.title}
            </Text>
            <Text
              as="p"
              level="body-medium"
              className="mt-3 text-content-layout-2"
            >
              {isContainerRuntime() && step.serverBody
                ? step.serverBody
                : step.body}
            </Text>
            {step.authority && <AuthorityNote className="mt-6" />}
          </m.div>
        </AnimatePresence>
        <Text
          as="p"
          level="caption"
          className="mt-auto flex flex-wrap gap-x-4 gap-y-1 pt-6 text-content-layout-3"
        >
          <a
            href={PRIVACY_DOC_URL}
            target="_blank"
            rel="noreferrer"
            className={quietLink}
          >
            How your data is handled
          </a>
          <a
            href={PRIVACY_POLICY_URL}
            target="_blank"
            rel="noreferrer"
            className={quietLink}
          >
            Privacy policy
          </a>
        </Text>
        <div className="mt-4 flex items-center gap-2">
          {!last && (
            <Button
              variant="primary"
              modifier="link"
              size="small"
              label="Skip"
              onClick={() => finish('skip')}
              className="-ml-3"
            />
          )}
          <span className="flex-1" />
          {index > 0 && (
            <Button
              variant="primary"
              modifier="ghost"
              label="Back"
              onClick={() => go(index - 1)}
            />
          )}
          <Button
            variant="primary"
            modifier="solid"
            label={last ? 'Add a database' : 'Next'}
            onClick={() => (last ? finish('done') : go(index + 1))}
            data-testid="onboarding-next"
          />
        </div>
      </div>
      <div
        className="relative order-1 flex items-center border-b border-border-layout-2 bg-surface-layout-2 p-4 tablet:order-2 tablet:border-b-0 tablet:border-l tablet:p-6"
        onPointerEnter={() => setHovering(true)}
        onPointerLeave={() => setHovering(false)}
        style={{
          backgroundImage:
            'radial-gradient(var(--color-border-layout-2) 1px, transparent 1.5px)',
          backgroundSize: '18px 18px',
        }}
      >
        <AnimatePresence mode="wait" initial={false}>
          <m.div
            key={step.key}
            className="w-full"
            initial={{ opacity: 0, scale: 0.985 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.985 }}
            transition={{ duration: 0.25, ease: [0.4, 0, 0.2, 1] }}
          >
            {still ? (
              <ServerStill caption={still.caption} lines={still.lines} />
            ) : useVideo ? (
              <SceneVideo
                stem={step.video}
                playing={videoPlaying}
                videoRef={videoRef}
                onEnded={advance}
                onBlocked={blocked}
                onError={() =>
                  setFailed((current) => ({ ...current, [step.key]: true }))
                }
              />
            ) : (
              <Stage>
                <Scene t={t} />
              </Stage>
            )}
          </m.div>
        </AnimatePresence>
        {(!reduced || useVideo) && (
          <div className="absolute right-3 bottom-3">
            <Button
              variant="primary"
              modifier="link"
              size="small"
              icon={held ? 'play' : undefined}
              iconPosition={held ? 'left' : 'none'}
              label={held ? 'Play' : 'Pause'}
              onClick={() => setHeld((current) => !current)}
            />
          </div>
        )}
      </div>
    </div>
  )
}

export function OnboardingTour({
  open,
  start = 0,
  onFinish,
}: {
  open: boolean
  /** The step to open at, 0-based. */
  start?: number
  onFinish: (exit: TourExit, step: number) => void
}) {
  // The X and Escape close the dialog; the body reports which step that happened on.
  const step = useRef(start)
  return (
    <Modal
      open={open}
      onOpenChange={(next) => !next && onFinish('close', step.current)}
    >
      <ModalContentContainer open={open}>
        <ModalContent
          size="extra-large"
          title="Welcome to RDST"
          description="How RDST works, in four short steps."
          className="max-w-5xl overflow-y-auto border-border-layout-2 bg-surface-primary-soft p-0 shadow-elevation-3"
          closeClassName="top-4 right-4"
          onOpenAutoFocus={(event) => {
            // Next is where the keyboard starts, not the first step bar.
            const next = (
              event.currentTarget as HTMLElement | null
            )?.querySelector<HTMLElement>('[data-testid="onboarding-next"]')
            if (!next) return
            event.preventDefault()
            next.focus()
          }}
        >
          <TourBody
            key={start}
            start={start}
            onFinish={onFinish}
            onStep={(index) => {
              step.current = index
            }}
          />
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}

/**
 * Shows the tour on a new install before anything else, and again when a screen asks
 * for it, at the step it asks for. Closing part way saves the step so the tour can resume
 * there; finishing or skipping clears it. Finishing or skipping the first run opens the
 * add-database dialog on the coding agent prompt, the next step.
 */
export function OnboardingTourHost() {
  const status = useSystemStatus()
  const openAddDatabase = useOpenAddDatabase()
  const [open, setOpen] = useState(false)
  const [start, setStart] = useState(0)
  const [firstRun, setFirstRun] = useState(false)
  const [resumed, setResumed] = useState(false)
  const targets = status.data?.targets.length

  useEffect(() => {
    if (targets === undefined) return
    if (targets > 0) {
      markTourDone()
      return
    }
    if (tourPending()) {
      setFirstRun(true)
      setResumed(false)
      setStart(0)
      setOpen(true)
    }
  }, [targets])

  useEffect(
    () =>
      onOpenTour((step) => {
        setResumed(step === savedTourStep())
        setStart(step)
        setOpen(true)
      }),
    []
  )

  const finish = (exit: TourExit, step: number) => {
    markTourDone()
    if (exit === 'close') saveTourStep(step)
    else clearTourStep()
    setOpen(false)
    // The last step's button says Add a database, so it always opens the coding agent
    // prompt. Skipping does on the first run, and on a resumed one with no database yet.
    // HomeEmpty doesn't open it a second time.
    if (
      exit === 'done' ||
      (exit === 'skip' && (firstRun || (resumed && targets === 0)))
    ) {
      markFirstRunPopupShown('add-database')
      openAddDatabase('agent')
    }
    setFirstRun(false)
  }

  return <OnboardingTour open={open} start={start} onFinish={finish} />
}

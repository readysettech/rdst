import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from '@rs/ui-new/use-toast';

// ---- types -----------------------------------------------------------------
export type Phase = 'idle' | 'provisioning' | 'ready' | 'tearing-down';
export type ContainerState = 'pending' | 'starting' | 'ready' | 'recovering' | 'error';
export type HealthState = 'running' | 'recovering' | 'failed';
export type DiscoveryMode = 'count_star' | 'sum_time';
export type LoadEventType = 'manual_cache' | 'manual_uncache' | 'qp_on' | 'qp_off' | 'mode_change';

export interface ContainerProgress {
  name: string;
  label: string;
  state: ContainerState;
  percent: number;
  // Live substep line for the step-by-step provision view, e.g.
  // "Seeding the Orders dataset... 42%".
  detail?: string;
}

export interface PreflightChecks {
  docker_installed: boolean;
  docker_running: boolean;
  images_present: boolean;
  missing_images: string[];
  download_mb: number;
  disk_space_ok: boolean;
  disk_free_gb: number;
  disk_required_gb: number;
}

export interface ThroughputMetrics {
  qps: number;
  p50_ms: number;
  p95_ms: number;
}

export interface LoadSample {
  t: number;
  direct: ThroughputMetrics;
  router: ThroughputMetrics;
}

export type LoadWindow = LoadSample;

export interface LoadEvent {
  t: number;
  type: LoadEventType;
  label: string;
}

export interface LoadHistory {
  samples: LoadSample[];
  events: LoadEvent[];
}

export type PatternStatus =
  | 'cached_querypilot'
  | 'cached_manual'
  | 'pass_through'
  | 'not_eligible'
  | 'denylisted'
  | 'unsupported';

export type ReasonKind =
  | 'selected'
  | 'below_rank'
  | 'below_min_execution'
  | 'denylisted'
  | 'not_select_shaped'
  | 'unsupported'
  | 'manual';

export interface PatternReason {
  kind: ReasonKind;
  rank?: number;
  metric?: DiscoveryMode | 'sum_time_us';
  metric_value?: number;
  cutoff?: number;
  winner_values?: number[];
  count?: number;
  threshold?: number;
}

export interface PatternRow {
  key: string;
  title: string;
  sql: string;
  group: string;
  status: PatternStatus;
  hits: number;
  postgres_hits: number;
  readyset_hits: number;
  direct_avg_ms: number | null;
  router_avg_ms: number | null;
  reason: PatternReason;
  log_reason: string | null;
  alt_rank?: number | null;
  alt_metric?: DiscoveryMode | 'sum_time_us' | null;
}

export interface QueryPilotStatus {
  enabled: boolean;
  mode: DiscoveryMode;
  next_pass_eta_s: number | null;
  schedule: '1min' | '15s' | string;
}

export interface DemoStatus {
  querypilot: QueryPilotStatus;
  health: Record<string, HealthState>;
  load_running: boolean;
  workers?: number;
  cache_budget: number;
  provisioned: boolean;
  last_error: string | null;
  // One-shot server-initiated toast (e.g. the one-hour auto-teardown notice).
  notice: string | null;
  // Epoch seconds when the environment auto-cleans; null when not provisioned.
  auto_teardown_at: number | null;
}

// ---- generic SSE reader ----------------------------------------------------
async function readSSE(
  url: string,
  init: RequestInit,
  onEvent: (event: string, data: unknown) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(url, { ...init, signal });
  if (!response.ok) throw new Error(await response.text() || response.statusText);
  if (!response.body) throw new Error('no response body');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let currentEvent = 'message';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) {
        currentEvent = 'message';
        continue;
      }
      if (trimmed.startsWith('event:')) {
        currentEvent = trimmed.substring(6).trim();
      } else if (trimmed.startsWith('data:')) {
        try {
          onEvent(currentEvent, JSON.parse(trimmed.substring(5).trim()));
        } catch {
          /* ignore keep-alive / partial frames */
        }
      }
    }
  }
}

// ---- contract helpers ------------------------------------------------------
function describeCacheError(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e);
  try {
    const detail = JSON.parse(raw)?.detail;
    if (typeof detail === 'string' && detail.includes('has not been seen')) {
      return "This query hasn't run yet - give the traffic a few seconds and try again.";
    }
    if (typeof detail === 'string') return detail;
  } catch {
    /* raw was not JSON */
  }
  return raw || 'Something went wrong - try again.';
}

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(await r.text() || r.statusText);
  return r.json() as Promise<T>;
}

const nowSeconds = () => Date.now() / 1000;

function numberOr(value: unknown, fallback = 0): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function metric(raw: any): ThroughputMetrics {
  return {
    qps: numberOr(raw?.qps),
    p50_ms: numberOr(raw?.p50_ms ?? raw?.p50),
    p95_ms: numberOr(raw?.p95_ms ?? raw?.p95),
  };
}

function normalizeSample(raw: any): LoadSample {
  return {
    t: numberOr(raw?.t, nowSeconds()),
    direct: metric(raw?.direct),
    router: metric(raw?.router ?? raw?.sqp),
  };
}

function normalizeEvent(raw: any): LoadEvent {
  return {
    t: numberOr(raw?.t, nowSeconds()),
    type: raw?.type ?? 'mode_change',
    label: String(raw?.label ?? 'demo event'),
  };
}

function normalizeHistory(raw: any): LoadHistory {
  return {
    samples: Array.isArray(raw?.samples) ? raw.samples.map(normalizeSample) : [],
    events: Array.isArray(raw?.events) ? raw.events.map(normalizeEvent) : [],
  };
}

function normalizePattern(raw: any): PatternRow {
  if (raw?.key && raw?.status && raw?.reason) {
    return {
      key: String(raw.key),
      title: String(raw.title ?? 'Unrecognized query'),
      sql: String(raw.sql ?? ''),
      group: String(raw.group ?? 'workload'),
      status: raw.status,
      hits: numberOr(raw.hits),
      postgres_hits: numberOr(raw.postgres_hits ?? raw.hits),
      readyset_hits: numberOr(raw.readyset_hits ?? raw.hits),
      direct_avg_ms: raw.direct_avg_ms == null ? null : numberOr(raw.direct_avg_ms),
      router_avg_ms: raw.router_avg_ms == null ? null : numberOr(raw.router_avg_ms),
      reason: raw.reason,
      log_reason: raw.log_reason ?? null,
      alt_rank: raw.alt_rank == null ? null : numberOr(raw.alt_rank),
      alt_metric: raw.alt_metric ?? null,
    };
  }

  const calls = numberOr(raw?.calls);
  const avg = calls > 0 && raw?.sum_time_us != null ? numberOr(raw.sum_time_us) / calls / 1000 : null;
  const cached = Boolean(raw?.cached);
  const owner = raw?.owner;
  const status: PatternStatus = cached
    ? owner === 'manual' ? 'cached_manual' : 'cached_querypilot'
    : raw?.role === 'uncacheable' ? 'unsupported' : raw?.past_threshold === false ? 'not_eligible' : 'pass_through';

  return {
    key: String(raw?.fingerprint ?? raw?.fingerprint_hex ?? raw?.key ?? raw?.sql ?? Math.random()),
    title: String(raw?.title ?? 'Unrecognized query'),
    sql: String(raw?.sql ?? ''),
    group: String(raw?.group ?? raw?.role ?? 'workload'),
    status,
    hits: calls,
    postgres_hits: numberOr(raw?.postgres_hits ?? calls),
    readyset_hits: numberOr(raw?.readyset_hits ?? calls),
    direct_avg_ms: avg,
    router_avg_ms: cached ? Math.min(avg ?? 0, 1) : avg,
    reason: owner === 'manual'
      ? { kind: 'manual' }
      : status === 'not_eligible'
        ? { kind: 'below_min_execution', count: calls, threshold: 5 }
        : status === 'unsupported'
          ? { kind: 'unsupported' }
          : cached
            ? { kind: 'selected', rank: 1, metric: 'count_star', metric_value: calls, cutoff: 3 }
            : { kind: 'below_rank', rank: 4, metric: 'count_star', metric_value: calls, cutoff: 3 },
    log_reason: raw?.log_reason ?? null,
    alt_rank: raw?.alt_rank == null ? null : numberOr(raw.alt_rank),
    alt_metric: raw?.alt_metric ?? null,
  };
}

function healthFromContainers(containers: Record<string, string> | undefined): Record<string, HealthState> {
  if (!containers) return {};
  return Object.fromEntries(Object.entries(containers).map(([name, state]) => {
    const s = String(state);
    if (s === 'running') return [name, 'running'];
    if (s === 'restarting' || s === 'created') return [name, 'recovering'];
    return [name, 'failed'];
  }));
}

function normalizeMode(mode: unknown): DiscoveryMode {
  return mode === 'sum_time' ? 'sum_time' : 'count_star';
}

function normalizeStatus(raw: any): DemoStatus {
  const querypilot = raw?.querypilot ?? {};
  const health = raw?.health ?? healthFromContainers(raw?.containers);
  const cacheBudget = numberOr(querypilot.cache_budget ?? raw?.cache_budget, 10);
  return {
    querypilot: {
      enabled: Boolean(querypilot.enabled ?? raw?.querypilot_enabled ?? !raw?.querypilot_paused),
      mode: normalizeMode(querypilot.mode ?? raw?.discovery_mode),
      next_pass_eta_s: querypilot.next_pass_eta_s == null ? null : numberOr(querypilot.next_pass_eta_s),
      schedule: querypilot.schedule ?? raw?.cron?.schedule ?? '1min',
    },
    health,
    load_running: Boolean(raw?.load_running),
    workers: raw?.workers == null ? undefined : numberOr(raw.workers),
    cache_budget: Math.min(40, Math.max(1, Math.round(cacheBudget))),
    provisioned: typeof raw?.provisioned === 'boolean' ? raw.provisioned : Boolean(raw?.ports),
    last_error: raw?.last_error ?? null,
    notice: raw?.notice ?? null,
    auto_teardown_at: raw?.auto_teardown_at == null ? null : numberOr(raw.auto_teardown_at, 0),
  };
}

const CONTAINER_LABELS: Record<string, string> = {
  pg: 'Postgres (Orders dataset)',
  readyset: 'Readyset cache engine',
  sqp: 'QueryPilot router',
  'qp-cron': 'QueryPilot accelerator',
};

const CONTAINER_NAMES = ['pg', 'readyset', 'sqp', 'qp-cron'];

const CONTAINERS: ContainerProgress[] = CONTAINER_NAMES.map((name) => ({
  name,
  label: CONTAINER_LABELS[name],
  state: 'pending',
  percent: 0,
}));

// Which sequential provision step a backend progress stage narrates.
const STAGE_STEP: Record<string, string> = {
  images: 'pg',
  start: 'pg',
  seed: 'pg',
  readyset: 'readyset',
  sqp: 'sqp',
  warmup: 'qp-cron',
  'qp-cron': 'qp-cron',
};

function containersFromHealth(health: Record<string, HealthState>): ContainerProgress[] {
  const names = Array.from(new Set([...CONTAINER_NAMES, ...Object.keys(health)]));
  return names.map((name) => {
    const h = health[name];
    return {
      name,
      label: CONTAINER_LABELS[name] ?? name,
      state: h === 'running' ? 'ready' : h === 'recovering' ? 'recovering' : h === 'failed' ? 'error' : 'pending',
      percent: h === 'running' ? 100 : h === 'recovering' ? 65 : h === 'failed' ? 100 : 0,
    };
  });
}

function resetPatternStats(patterns: PatternRow[], dropQueryPilotCaches: boolean): PatternRow[] {
  return patterns.map((p) => ({
    ...p,
    hits: 0,
    postgres_hits: 0,
    readyset_hits: 0,
    direct_avg_ms: null,
    router_avg_ms: null,
    status: dropQueryPilotCaches && p.status === 'cached_querypilot' ? 'pass_through' : p.status,
    reason: dropQueryPilotCaches && p.status === 'cached_querypilot'
      ? { kind: 'below_min_execution', count: 0, threshold: p.reason.threshold ?? 5 }
      : p.reason,
  }));
}

// ---- plain JSON actions ----------------------------------------------------
export const getPreflight = () => fetch('/api/demo/preflight').then((r) => json<PreflightChecks>(r));
export const getStatus = () => fetch('/api/demo/status').then((r) => json<any>(r)).then(normalizeStatus);
export const getHistory = () => fetch('/api/demo/load/history').then((r) => json<any>(r)).then(normalizeHistory);
export const getPatterns = (): Promise<PatternRow[]> =>
  fetch('/api/demo/patterns').then((r) => json<any>(r)).then((d) => (d.patterns ?? []).map(normalizePattern));
export const startLoad = () =>
  fetch('/api/demo/load/start', { method: 'POST' }).then((r) => json<any>(r));
export const stopLoad = () => fetch('/api/demo/load/stop', { method: 'POST' }).then((r) => json<any>(r));
export const setIntensity = (workers: number) =>
  fetch('/api/demo/load', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workers }) }).then((r) => json<any>(r));
export const cacheQuery = (key: string) =>
  fetch('/api/demo/cache', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key, fingerprint: key }) }).then((r) => json<any>(r));
export const uncacheQuery = (key: string) =>
  fetch('/api/demo/uncache', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key, fingerprint: key }) }).then((r) => json<any>(r));
export const setQueryPilot = (enabled: boolean) =>
  fetch('/api/demo/querypilot', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }) }).then((r) => json<any>(r));
export const setDiscoveryModeApi = (mode: DiscoveryMode) =>
  fetch('/api/demo/discovery-mode', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }) }).then((r) => json<any>(r));
export const setDemoSettings = (cacheBudget: number) =>
  fetch('/api/demo/settings', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cache_budget: cacheBudget }) }).then((r) => json<any>(r));
export const teardown = () => fetch('/api/demo/teardown', { method: 'POST' }).then((r) => json<any>(r));

// ---- the demo hook ---------------------------------------------------------
export function useDemo() {
  const [phase, setPhase] = useState<Phase>('idle');
  const [containers, setContainers] = useState<ContainerProgress[]>(CONTAINERS);
  const [provisionPct, setProvisionPct] = useState(0);
  const [samples, setSamples] = useState<LoadSample[]>([]);
  const [events, setEvents] = useState<LoadEvent[]>([]);
  const [patterns, setPatterns] = useState<PatternRow[]>([]);
  const [querypilotOn, setQuerypilotOn] = useState(false);
  const [mode, setMode] = useState<DiscoveryMode>('count_star');
  const [nextPassEta, setNextPassEta] = useState<number | null>(null);
  const [cacheBudget, setCacheBudgetState] = useState(10);
  const [loadRunning, setLoadRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const loadAbortRef = useRef<AbortController | null>(null);
  // Tracks the live phase for callbacks that must not fight the provision SSE
  // stream (which owns container states until provisioning completes).
  const phaseRef = useRef<Phase>('idle');
  phaseRef.current = phase;

  const addEvent = useCallback((type: LoadEventType, label: string) => {
    setEvents((es) => [...es, { t: nowSeconds(), type, label }].slice(-80));
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const s = await getStatus();
      setQuerypilotOn(s.querypilot.enabled);
      setMode(s.querypilot.mode);
      setNextPassEta(s.querypilot.next_pass_eta_s);
      setCacheBudgetState(s.cache_budget);
      setLoadRunning(s.load_running);
      // The provision SSE stream owns container states while provisioning or
      // tearing down; the 5s health poll must not overwrite them (that flapped
      // green containers back to orange "recovering" mid-provision).
      if (phaseRef.current !== 'provisioning' && phaseRef.current !== 'tearing-down') {
        setContainers(containersFromHealth(s.health));
      }
      if (s.last_error) setError(s.last_error);
      // Server-initiated toast, delivered exactly once (the backend clears it
      // after the first status read), e.g. the one-hour auto-teardown notice.
      if (s.notice) setNotice(s.notice);
      setPhase((current) => {
        if (current === 'provisioning' || current === 'tearing-down') return current;
        return s.provisioned ? 'ready' : 'idle';
      });
    } catch (e: any) {
      setError(`Status unavailable: ${e?.message ?? String(e)}`);
    }
  }, []);

  const refreshHistory = useCallback(async () => {
    try {
      const h = await getHistory();
      setSamples(h.samples.slice(-360));
      setEvents((current) => {
        const backendKeys = new Set(h.events.map((e) => `${e.type}:${e.label}:${Math.round(e.t)}`));
        const optimistic = current.filter((e) => !backendKeys.has(`${e.type}:${e.label}:${Math.round(e.t)}`));
        return [...h.events, ...optimistic].sort((a, b) => a.t - b.t).slice(-80);
      });
    } catch {
      /* history is unavailable before the backend has the new endpoint */
    }
  }, []);

  const refreshPatterns = useCallback(async () => {
    try {
      setPatterns(await getPatterns());
    } catch {
      /* patterns can be temporarily unavailable during provisioning */
    }
  }, []);

  const provision = useCallback(async () => {
    setError(null);
    setNotice(null);
    setPhase('provisioning');
    setProvisionPct(0);
    setContainers(CONTAINERS.map((c) => ({ ...c })));
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await readSSE('/api/demo/provision', { method: 'POST' }, (ev, data: any) => {
        const type = data?.type ?? ev;
        if (type === 'progress') {
          setProvisionPct(numberOr(data.percent));
          // Progress events narrate the currently active step: surface the
          // message as that step's live substep line.
          const stepName = STAGE_STEP[String(data.stage ?? '')];
          if (stepName && data.message) {
            setContainers((cs) => cs.map((c) => c.name === stepName
              ? { ...c, state: c.state === 'pending' ? 'starting' : c.state, detail: String(data.message) }
              : c));
          }
        } else if (type === 'container') {
          setProvisionPct(numberOr(data.percent));
          setContainers((cs) => cs.map((c) => c.name === data.name
            ? {
              ...c,
              // Provisioning only ever runs pending -> starting -> ready. Any
              // other not-yet-ready state maps to the neutral spinner; the
              // orange 'recovering' state is reserved for post-ready failures.
              state: data.state === 'ready' || data.state === 'running'
                ? 'ready'
                : data.state === 'error' || data.state === 'failed'
                  ? 'error'
                  : 'starting',
              label: data.label ?? c.label,
              percent: numberOr(data.percent, c.percent),
              detail: data.detail ? String(data.detail) : c.detail,
            }
            : c));
        } else if (type === 'complete') {
          setProvisionPct(100);
          // Show every container green for a brief beat before flipping to the
          // dashboard, so the ready-phase entrance transition can play instead
          // of teleporting.
          setContainers((cs) => cs.map((c) => ({ ...c, state: 'ready', percent: 100 })));
          const message = data?.message ?? 'Your demo is ready. Start the traffic to see the baseline.';
          window.setTimeout(() => {
            setPhase('ready');
            setNotice(message);
            void refreshStatus();
            void refreshHistory();
            void refreshPatterns();
          }, 650);
        } else if (type === 'error') {
          setError(data.message ?? 'Provisioning failed');
          setPhase('idle');
        }
      }, ctrl.signal);
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        setError(e?.message ?? String(e));
        setPhase('idle');
      }
    }
  }, [refreshHistory, refreshPatterns, refreshStatus]);

  const beginLoad = useCallback(async () => {
    setError(null);
    await startLoad();
    setLoadRunning(true);
    setNotice('Traffic is running. Watch the baseline before you cache anything.');
    await refreshHistory();
    await refreshPatterns();
    await refreshStatus();
  }, [refreshHistory, refreshPatterns, refreshStatus]);

  const endLoad = useCallback(async () => {
    loadAbortRef.current?.abort();
    setLoadRunning(false);
    await stopLoad();
    await refreshStatus();
  }, [refreshStatus]);

  const cache = useCallback(async (key: string, title?: string) => {
    try {
      await cacheQuery(key);
    } catch (e) {
      toast({
        title: "Couldn't cache that query",
        description: describeCacheError(e),
        variant: 'negative',
      });
      return;
    }
    addEvent('manual_cache', title ? `you cached ${title}` : 'you cached a query');
    await refreshPatterns();
    await refreshHistory();
  }, [addEvent, refreshHistory, refreshPatterns]);

  const uncache = useCallback(async (key: string, title?: string) => {
    try {
      await uncacheQuery(key);
    } catch (e) {
      toast({
        title: "Couldn't uncache that query",
        description: describeCacheError(e),
        variant: 'negative',
      });
      return;
    }
    addEvent('manual_uncache', title ? `you uncached ${title}` : 'you uncached a query');
    await refreshPatterns();
    await refreshHistory();
  }, [addEvent, refreshHistory, refreshPatterns]);

  const toggleQueryPilot = useCallback(async (on: boolean) => {
    setQuerypilotOn(on);
    // Enabling always opens on the most-expensive policy, matching the backend reset.
    if (on) setMode('sum_time');
    setPatterns((ps) => resetPatternStats(ps, true));
    addEvent(on ? 'qp_on' : 'qp_off', on ? 'QueryPilot on' : 'QueryPilot off');
    setNotice(on
      ? 'QueryPilot is on. It caches your top 10 most expensive queries.'
      : 'QueryPilot is off. Its caches were dropped; the ones you made stay.');
    await setQueryPilot(on);
    setPatterns((ps) => resetPatternStats(ps, true));
    await refreshStatus();
    await refreshPatterns();
    await refreshHistory();
  }, [addEvent, refreshHistory, refreshPatterns, refreshStatus]);

  const setDiscoveryMode = useCallback(async (nextMode: DiscoveryMode) => {
    if (nextMode === mode) return;
    setMode(nextMode);
    setPatterns((ps) => resetPatternStats(ps, true));
    addEvent('mode_change', `policy -> ${nextMode === 'sum_time' ? 'most expensive' : 'most frequent'}`);
    setNotice(nextMode === 'sum_time'
      ? 'QueryPilot dropped its picks — re-selecting the top 10 most expensive on the next pass.'
      : 'QueryPilot dropped its picks — re-selecting the top 20 most frequent on the next pass.');
    await setDiscoveryModeApi(nextMode);
    setPatterns((ps) => resetPatternStats(ps, true));
    await refreshStatus();
    await refreshPatterns();
    await refreshHistory();
  }, [addEvent, mode, refreshHistory, refreshPatterns, refreshStatus]);

  const setCacheBudget = useCallback(async (nextBudget: number) => {
    const clamped = Math.min(40, Math.max(1, Math.round(nextBudget)));
    setCacheBudgetState(clamped);
    await setDemoSettings(clamped);
    setNotice(`QueryPilot may cache up to ${clamped} queries on its next pass.`);
    await refreshStatus();
  }, [refreshStatus]);

  const tearDown = useCallback(async () => {
    setError(null);
    setPhase('tearing-down');
    // Show the containers "removing" during the interstitial, then ticked to
    // removed, with a minimum on-screen duration so a fast teardown still reads
    // as real work rather than teleporting back to the idle card.
    setContainers(CONTAINERS.map((c) => ({ ...c, state: 'pending', percent: 0 })));
    abortRef.current?.abort();
    loadAbortRef.current?.abort();
    setLoadRunning(false);
    const startedAt = Date.now();
    const MIN_INTERSTITIAL_MS = 1800;
    try {
      await teardown();
      setContainers(CONTAINERS.map((c) => ({ ...c, state: 'ready', percent: 100 })));
      const remaining = MIN_INTERSTITIAL_MS - (Date.now() - startedAt);
      if (remaining > 0) await new Promise((resolve) => window.setTimeout(resolve, remaining));
      setPhase('idle');
      setSamples([]);
      setEvents([]);
      setPatterns([]);
      setContainers(CONTAINERS.map((c) => ({ ...c })));
      setProvisionPct(0);
      setQuerypilotOn(false);
      setMode('count_star');
      setNextPassEta(null);
      setCacheBudgetState(10);
      setNotice("Everything's cleaned up — nothing left on your system.");
    } catch (e: any) {
      setError(e?.message ?? String(e));
      setPhase('ready');
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
    void refreshHistory();
    void refreshPatterns();
  }, [refreshHistory, refreshPatterns, refreshStatus]);

  useEffect(() => {
    const id = setInterval(() => {
      void refreshStatus();
    }, 5000);
    return () => clearInterval(id);
  }, [refreshStatus]);

  useEffect(() => {
    const id = setInterval(() => {
      setNextPassEta((eta) => eta == null ? eta : Math.max(0, eta - 1));
    }, 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!loadRunning) return undefined;
    const ctrl = new AbortController();
    loadAbortRef.current = ctrl;
    readSSE('/api/demo/load/stream', { method: 'GET' }, (_ev, data: any) => {
      const sample = normalizeSample(data);
      setSamples((ws) => [...ws, sample].slice(-360));
    }, ctrl.signal).catch((e: any) => {
      if (e?.name !== 'AbortError') setError(`Load stream unavailable: ${e?.message ?? String(e)}`);
    });
    return () => ctrl.abort();
  }, [loadRunning]);

  useEffect(() => {
    if (!loadRunning) return undefined;
    const id = setInterval(() => {
      void refreshPatterns();
    }, 2000);
    return () => clearInterval(id);
  }, [loadRunning, refreshPatterns]);

  useEffect(() => () => {
    abortRef.current?.abort();
    loadAbortRef.current?.abort();
  }, []);

  return {
    phase,
    containers,
    provisionPct,
    samples,
    windows: samples,
    events,
    patterns,
    querypilotOn,
    mode,
    nextPassEta,
    cacheBudget,
    loadRunning,
    error,
    notice,
    provision,
    beginLoad,
    endLoad,
    cache,
    uncache,
    toggleQueryPilot,
    setDiscoveryMode,
    setCacheBudget,
    tearDown,
    setIntensity,
    refreshPatterns,
    refreshStatus,
    refreshHistory,
  };
}

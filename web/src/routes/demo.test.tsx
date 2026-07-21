import { afterEach, beforeAll, describe, it, expect, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('@rs/ui-new/use-toast', () => ({ toast: vi.fn() }));
vi.mock('../components/SQLDisplay', () => ({
  SQLDisplay: ({ sql }: { sql: string }) => <div data-testid="sql-display">{sql}</div>,
}));

// DemoPage's bridge CTA navigates via useNavigate; stub it so the page can be
// rendered in isolation (no RouterProvider) and the hand-off target asserted.
const navigateSpy = vi.fn();
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>();
  return { ...actual, useNavigate: () => navigateSpy };
});

import { toast } from '@rs/ui-new/use-toast';
import { DemoPage, windowLiftRatio, eventDescription, parameterizeSql, clipToScrollAncestors } from './-demo-page';
import * as useDemoMod from '../lib/useDemo';
import type { LoadSample, PatternRow } from '../lib/useDemo';

type DemoState = ReturnType<typeof useDemoMod.useDemo>;

const at = 1_780_000_000;

function sample(t: number, directQps: number, routerQps: number): LoadSample {
  return {
    t,
    direct: { qps: directQps, p50_ms: 5, p95_ms: 38 },
    router: { qps: routerQps, p50_ms: 1, p95_ms: 2 },
  };
}

function row(overrides: Partial<PatternRow>): PatternRow {
  const base: PatternRow = {
    key: 'Q',
    title: 'Query',
    sql: 'SELECT 1',
    group: 'workload',
    status: 'pass_through',
    hits: 0,
    postgres_hits: 0,
    readyset_hits: 0,
    direct_avg_ms: null,
    router_avg_ms: null,
    reason: { kind: 'below_min_execution', count: 0, threshold: 5 },
    log_reason: null,
    alt_rank: null,
    alt_metric: null,
    ...overrides,
  };
  // Per-path hit counts default to the combined hits unless a test diverges them.
  return {
    ...base,
    postgres_hits: overrides.postgres_hits ?? base.hits,
    readyset_hits: overrides.readyset_hits ?? base.hits,
  };
}

const basePatterns: PatternRow[] = [
  row({
    key: 'H01',
    title: 'Revenue by category',
    sql: 'SELECT p.category, sum(oi.qty * oi.unit_cents) FROM order_items oi GROUP BY p.category',
    group: 'heavy',
    status: 'cached_querypilot',
    hits: 34,
    direct_avg_ms: 212.9,
    router_avg_ms: 0.9,
    reason: { kind: 'selected', rank: 1, metric: 'sum_time_us', metric_value: 7_238_000, cutoff: 10 },
    alt_rank: 24,
    alt_metric: 'count_star',
  }),
  row({
    key: 'M01',
    title: 'Orders per day (30d)',
    sql: "SELECT date_trunc('day', placed_at), count(*) FROM orders GROUP BY 1",
    group: 'mid_tier',
    status: 'cached_manual',
    hits: 12,
    direct_avg_ms: 6.1,
    router_avg_ms: 0.7,
    reason: { kind: 'manual' },
  }),
  row({
    key: 'M02',
    title: 'Customer reorder summary',
    sql: 'SELECT customer_id, count(*) FROM orders GROUP BY customer_id',
    group: 'mid_tier',
    status: 'pass_through',
    hits: 11,
    direct_avg_ms: 5.2,
    router_avg_ms: 5.1,
    reason: { kind: 'below_rank', rank: 12, metric: 'count_star', metric_value: 11, cutoff: 10 },
    alt_rank: 18,
    alt_metric: 'sum_time_us',
  }),
  row({
    key: 'C01',
    title: 'Cohort revenue (quarterly)',
    sql: "SELECT date_trunc('quarter', created_at), sum(total_cents) FROM orders GROUP BY 1",
    group: 'heavy',
    status: 'not_eligible',
    hits: 3,
    direct_avg_ms: 141.1,
    router_avg_ms: 142,
    reason: { kind: 'below_min_execution', count: 3, threshold: 5 },
  }),
  row({
    key: 'D01',
    title: 'Live server time',
    sql: 'SELECT now() AS server_time',
    group: 'system',
    status: 'denylisted',
    hits: 448,
    direct_avg_ms: 0.2,
    router_avg_ms: 0.3,
    reason: { kind: 'denylisted' },
  }),
  row({
    key: 'U01',
    title: 'Org chart walk',
    sql: 'WITH RECURSIVE t(n) AS (...) SELECT * FROM t',
    group: 'unsupported',
    status: 'unsupported',
    hits: 24,
    direct_avg_ms: 0.3,
    router_avg_ms: 0.4,
    reason: { kind: 'unsupported' },
  }),
  row({
    key: 'N01',
    title: 'Windowed loyalty rank',
    sql: 'SELECT customer_id, rank() OVER (ORDER BY spend DESC) FROM customers',
    group: 'mid_tier',
    status: 'pass_through',
    hits: 9,
    direct_avg_ms: 7,
    router_avg_ms: 7.1,
    reason: { kind: 'not_select_shaped' },
  }),
];

function makeBase(): DemoState {
  return {
    phase: 'ready',
    containers: [],
    provisionPct: 0,
    samples: [sample(at, 1_000, 1_000)],
    windows: [sample(at, 1_000, 1_000)],
    events: [],
    patterns: basePatterns,
    querypilotOn: false,
    mode: 'count_star',
    nextPassEta: 58,
    cacheBudget: 10,
    loadRunning: false,
    error: null,
    notice: null,
    provision: vi.fn(),
    beginLoad: vi.fn(),
    endLoad: vi.fn(),
    cache: vi.fn(),
    uncache: vi.fn(),
    toggleQueryPilot: vi.fn(),
    setDiscoveryMode: vi.fn(),
    setCacheBudget: vi.fn(),
    tearDown: vi.fn(),
    setIntensity: vi.fn(),
    refreshPatterns: vi.fn(),
    refreshStatus: vi.fn(),
    refreshHistory: vi.fn(),
  } as unknown as DemoState;
}

function mockDemo(overrides: Partial<DemoState>) {
  const state = { ...makeBase(), ...overrides } as DemoState;
  vi.spyOn(useDemoMod, 'useDemo').mockReturnValue(state);
  return state;
}

function markTourDone() {
  window.localStorage.setItem('qpdemo_walkthrough_done', '1');
}

function stubPreflight(body: Record<string, unknown>) {
  const fetchMock = vi.fn((_url: RequestInfo | URL) => Promise.resolve({
    ok: true,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as unknown as Response));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function rowScope(title: string) {
  const tr = screen.getByText(title).closest('tr');
  expect(tr).toBeTruthy();
  return within(tr as HTMLElement);
}

// Open the header kebab (system Dropdown / Radix menu). Under jsdom the Radix
// trigger opens reliably via keyboard; opening also needs these pointer/scroll
// APIs jsdom omits.
function openDemoActionsMenu() {
  fireEvent.keyDown(screen.getByRole('button', { name: 'Demo actions' }), {
    key: 'Enter',
  });
}

describe('DemoPage', () => {
  beforeAll(() => {
    // jsdom omits these; Radix menus call them on the trigger, so the header
    // kebab (system Dropdown) can be opened in tests.
    const proto = HTMLElement.prototype as unknown as {
      hasPointerCapture: () => boolean;
      setPointerCapture: () => void;
      releasePointerCapture: () => void;
      scrollIntoView: () => void;
    };
    proto.hasPointerCapture = () => false;
    proto.setPointerCapture = () => {};
    proto.releasePointerCapture = () => {};
    proto.scrollIntoView = () => {};
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.mocked(toast).mockClear();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('uses Start and Stop only for traffic control', () => {
    markTourDone();
    const beginLoad = vi.fn();
    const endLoad = vi.fn();
    const hook = vi.spyOn(useDemoMod, 'useDemo');
    hook.mockReturnValue({ ...makeBase(), beginLoad, endLoad, loadRunning: false });
    const { rerender } = render(<DemoPage />);

    fireEvent.click(screen.getByRole('button', { name: /Start/ }));
    expect(beginLoad).toHaveBeenCalledOnce();
    expect(screen.queryByText(/worker/i)).toBeNull();
    expect(screen.queryByText(/intensity/i)).toBeNull();

    hook.mockReturnValue({ ...makeBase(), beginLoad, endLoad, loadRunning: true });
    rerender(<DemoPage />);
    fireEvent.click(screen.getByRole('button', { name: /Stop/ }));
    expect(endLoad).toHaveBeenCalledOnce();
  });

  it('shows a QueryPilot toggle with a heartbeat and no budget or all-caps controls', () => {
    markTourDone();
    mockDemo({ querypilotOn: true, nextPassEta: 42 });
    render(<DemoPage />);

    expect(screen.getByLabelText('QueryPilot activity')).toBeTruthy();
    expect(screen.queryByLabelText('Cache budget')).toBeNull();
    expect(screen.queryByRole('button', { name: /cache budget/i })).toBeNull();
    expect(screen.queryByText('QUERYPILOT')).toBeNull();
    expect(screen.queryByText('CACHE BY')).toBeNull();
    expect(screen.queryByText(/next selection pass in/i)).toBeNull();
  });

  it('shows the caching policy control only while QueryPilot is on', () => {
    markTourDone();
    const hook = vi.spyOn(useDemoMod, 'useDemo');
    hook.mockReturnValue({ ...makeBase(), querypilotOn: false });
    const { rerender } = render(<DemoPage />);
    expect(screen.queryByRole('button', { name: 'Most expensive' })).toBeNull();

    hook.mockReturnValue({ ...makeBase(), querypilotOn: true });
    rerender(<DemoPage />);
    expect(screen.getByRole('button', { name: 'Most expensive' })).toBeTruthy();
  });

  it('routes demo errors and notices through toasts instead of inline bars', () => {
    markTourDone();
    mockDemo({ error: 'container exploded' });
    render(<DemoPage />);

    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({ variant: 'negative', description: 'container exploded' }),
    );
    expect(screen.queryByText('container exploded')).toBeNull();
  });

  it('surfaces the one-shot server notice (auto-teardown) as a toast', () => {
    markTourDone();
    mockDemo({ notice: 'Demo environment auto-cleaned after 1 hour.' });
    render(<DemoPage />);

    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({ description: 'Demo environment auto-cleaned after 1 hour.' }),
    );
  });

  it('renders the start card as a live preflight checklist when everything is ready', async () => {
    markTourDone();
    stubPreflight({
      docker_installed: true,
      docker_running: true,
      images_present: true,
      missing_images: [],
      download_mb: 0,
      disk_space_ok: true,
      disk_free_gb: 120,
      disk_required_gb: 2,
    });
    mockDemo({ phase: 'idle' });
    render(<DemoPage />);

    expect(screen.getByText("What's required")).toBeTruthy();
    expect(screen.queryByText("What you'll need")).toBeNull();
    expect(await screen.findByText('Docker is running')).toBeTruthy();
    expect(screen.getByText(/Container images downloaded/)).toBeTruthy();
    // The auto-clean line is a styled note, and the card has its own copy.
    expect(screen.getByText(/cleans itself up after an hour/)).toBeTruthy();
    expect(screen.getByText(/Cache a query and the effect shows immediately/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Start the demo/ })).toBeTruthy();
    const start = screen.getByRole('button', { name: /Start the demo/ }) as HTMLButtonElement;
    expect(start.disabled).toBe(false);
  });

  it('flags docker installed-but-stopped and pending image download, with a re-check', async () => {
    markTourDone();
    const fetchMock = stubPreflight({
      docker_installed: true,
      docker_running: false,
      images_present: false,
      missing_images: ['a', 'b', 'c', 'd'],
      download_mb: 500,
      disk_space_ok: true,
      disk_free_gb: 120,
      disk_required_gb: 2,
    });
    mockDemo({ phase: 'idle' });
    render(<DemoPage />);

    expect(await screen.findByText(/Docker isn't running — start Docker to continue/)).toBeTruthy();
    expect(screen.getByText(/Container images not downloaded yet — about 500 MB/)).toBeTruthy();
    const start = screen.getByRole('button', { name: /Start the demo/ }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);

    const preflightCalls = () =>
      fetchMock.mock.calls.filter(([u]) => String(u).includes('/preflight')).length;
    const before = preflightCalls();
    fireEvent.click(screen.getByRole('button', { name: /Re-check/ }));
    // Re-check must re-run the preflight; assert it triggered at least one more
    // call rather than a fixed total, which is brittle if the mount effect fires
    // more than once.
    await waitFor(() => {
      expect(preflightCalls()).toBeGreaterThan(before);
    });
  });

  it('flags docker not installed as a red blocker and disables Start', async () => {
    markTourDone();
    stubPreflight({
      docker_installed: false,
      docker_running: false,
      images_present: false,
      missing_images: ['a', 'b', 'c', 'd'],
      download_mb: 500,
      disk_space_ok: true,
      disk_free_gb: 120,
      disk_required_gb: 2,
    });
    mockDemo({ phase: 'idle' });
    render(<DemoPage />);

    expect(await screen.findByText(/Docker isn't installed — install Docker Desktop to continue/)).toBeTruthy();
    expect(screen.queryByText("Docker is running")).toBeNull();
    const start = screen.getByRole('button', { name: /Start the demo/ }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
  });

  it('shows a teardown interstitial with containers ticking to removed', () => {
    markTourDone();
    mockDemo({
      phase: 'tearing-down',
      containers: [
        { name: 'pg', label: 'Postgres (Orders dataset)', state: 'pending', percent: 0 },
        { name: 'readyset', label: 'Readyset cache engine', state: 'ready', percent: 100 },
      ] as unknown as DemoState['containers'],
    });
    render(<DemoPage />);
    expect(screen.getByText('Removing demo containers and data...')).toBeTruthy();
    expect(screen.getByText('removing')).toBeTruthy();
    // "removed" appears both as the status line and the tick's accessible label.
    expect(screen.getAllByText('removed').length).toBeGreaterThan(0);
  });

  it('expands a row to the parameterized query shape with no caption', () => {
    markTourDone();
    mockDemo({
      patterns: [row({
        key: 'P01',
        title: 'Order receipt by id',
        sql: 'SELECT id, status FROM orders WHERE id = 987654',
        status: 'pass_through',
        hits: 5,
      })],
    });
    render(<DemoPage />);

    fireEvent.click(screen.getByText('Order receipt by id'));
    const display = screen.getByTestId('sql-display');
    expect(display.textContent).toContain('WHERE id = $1');
    expect(display.textContent).not.toContain('987654');
    // The caption line was removed; the expander shows only the SQL block.
    expect(screen.queryByText(/literal values are parameterized/)).toBeNull();
    expect(screen.queryByText(/Query shape/)).toBeNull();
  });

  it('defaults to query-identity order and header clicks take a frozen snapshot', () => {
    markTourDone();
    const patterns = [
      row({ key: 'A', title: 'Slow uncached', status: 'pass_through', hits: 400, postgres_hits: 400, readyset_hits: 380 }),
      row({ key: 'B', title: 'Fast cached', status: 'cached_querypilot', hits: 900, postgres_hits: 300, readyset_hits: 900, reason: { kind: 'selected', rank: 1, metric: 'count_star', metric_value: 900, cutoff: 10 } }),
    ];
    const hook = vi.spyOn(useDemoMod, 'useDemo');
    let state = { ...makeBase(), patterns } as DemoState;
    hook.mockImplementation(() => state);
    const { rerender } = render(<DemoPage />);

    const titles = () => screen.getAllByTestId('pattern-title').map((el) => el.textContent);
    // Default: stable query identity, and the switch column is labeled.
    expect(titles()).toEqual(['Slow uncached', 'Fast cached']);
    expect(screen.getByText('Cache status')).toBeTruthy();
    // A header click snapshots the CURRENT values: Readyset-hits desc puts
    // the cached row first.
    fireEvent.click(screen.getByRole('button', { name: /Readyset hits/ }));
    expect(titles()).toEqual(['Fast cached', 'Slow uncached']);
    // Counters change so that live re-sorting WOULD flip the order; the
    // frozen snapshot must hold every row exactly where it was.
    state = {
      ...state,
      patterns: [
        { ...patterns[0], readyset_hits: 5_000 },
        { ...patterns[1], readyset_hits: 901 },
      ],
    } as DemoState;
    rerender(<DemoPage />);
    expect(titles()).toEqual(['Fast cached', 'Slow uncached']);
    // The Query header returns to the identity order.
    fireEvent.click(screen.getByRole('button', { name: /^Query/ }));
    expect(titles()).toEqual(['Slow uncached', 'Fast cached']);
  });

  it('clips tour anchor rects to scroll containers so hidden rows never anchor', () => {
    const container = {
      parentElement: null,
      getBoundingClientRect: () => new DOMRect(0, 100, 500, 200),
    } as unknown as HTMLElement;
    const el = { parentElement: container } as unknown as HTMLElement;
    const spy = vi.spyOn(window, 'getComputedStyle').mockReturnValue(
      { overflow: 'auto', overflowY: '', overflowX: '' } as CSSStyleDeclaration,
    );
    // Row half above the table's scroll viewport: clipped to the visible part.
    const clipped = clipToScrollAncestors(el, new DOMRect(0, 80, 500, 40));
    expect(clipped?.top).toBe(100);
    expect(clipped?.bottom).toBe(120);
    // Row scrolled fully out of the viewport: not visible, no phantom rect.
    expect(clipToScrollAncestors(el, new DOMRect(0, 20, 500, 40))).toBeNull();
    spy.mockRestore();
  });

  it('surfaces the Rosetta ask only on a definitive cannot-emulate answer', () => {
    markTourDone();
    mockDemo({ phase: 'idle' } as Partial<DemoState>);
    stubPreflight({
      docker_installed: true, docker_running: true, images_present: true,
      missing_images: [], download_mb: 0, disk_space_ok: true,
      disk_free_gb: 50, disk_required_gb: 2, amd64_emulation: 'unavailable',
    });
    render(<DemoPage />);
    return waitFor(() => {
      expect(screen.getByText(/Use Rosetta for x86_64\/amd64 emulation/)).toBeTruthy();
      expect((screen.getByRole('button', { name: /Start the demo/ }) as HTMLButtonElement).disabled).toBe(true);
    });
  });

  it('never mentions Rosetta when emulation is fine or not applicable', () => {
    markTourDone();
    mockDemo({ phase: 'idle' } as Partial<DemoState>);
    stubPreflight({
      docker_installed: true, docker_running: true, images_present: true,
      missing_images: [], download_mb: 0, disk_space_ok: true,
      disk_free_gb: 50, disk_required_gb: 2, amd64_emulation: 'ok',
    });
    render(<DemoPage />);
    return waitFor(() => {
      expect(screen.queryByText(/Rosetta/)).toBeNull();
      expect((screen.getByRole('button', { name: /Start the demo/ }) as HTMLButtonElement).disabled).toBe(false);
    });
  });

  it('shows sequential provision steps: done collapsed, active with live substep, pending dimmed', () => {
    markTourDone();
    mockDemo({
      phase: 'provisioning',
      provisionPct: 22,
      containers: [
        { name: 'pg', label: 'Postgres (Orders dataset)', state: 'ready', percent: 30 },
        { name: 'readyset', label: 'Readyset cache engine', state: 'starting', percent: 40, detail: 'Waiting for Readyset to connect and snapshot the dataset (9s)...' },
        { name: 'sqp', label: 'QueryPilot router', state: 'pending', percent: 0 },
        { name: 'qp-cron', label: 'QueryPilot accelerator', state: 'pending', percent: 0 },
      ] as unknown as DemoState['containers'],
    });
    render(<DemoPage />);

    expect(screen.getByText('Setting up the demo')).toBeTruthy();
    // "ready" appears as the collapsed row's status and the tick's label.
    expect(screen.getAllByText('ready').length).toBeGreaterThan(0);
    expect(screen.getByTestId('provision-substep').textContent).toContain('Waiting for Readyset to connect');
    expect(screen.getAllByText('waiting').length).toBe(2);
  });

  it('toggles the chart between the 5-minute and 1-minute windows', () => {
    markTourDone();
    // A full window of history so the axis label shows the window span, not the
    // shorter still-filling span.
    mockDemo({ samples: [sample(at - 400, 100, 100), sample(at, 100, 300)] });
    render(<DemoPage />);

    expect(screen.getByText('-5 min')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '1 min' }));
    expect(screen.getByText('-1 min')).toBeTruthy();
  });

  it('always renders the permanent lift-ratio line, even at baseline', () => {
    markTourDone();
    // Baseline: router and direct match, so the ratio reads ~1.0x and still
    // renders (no conditional unmount that would jump the layout).
    mockDemo({ samples: [sample(at - 5, 100, 100), sample(at, 100, 100)] });
    const { rerender } = render(<DemoPage />);
    expect(screen.getByTestId('lift-ratio').textContent).toContain('1.0x');
    expect(screen.getByTestId('lift-ratio').textContent).toContain('Postgres direct');

    // Lifted: whole-number multiple once the router pulls far ahead.
    mockDemo({ samples: [sample(at - 5, 100, 1000), sample(at, 100, 1000)] });
    rerender(<DemoPage />);
    expect(screen.getByTestId('lift-ratio').textContent).toContain('10x');
  });

  it('holds an honest prompt instead of a false "0.0x" before any traffic', () => {
    markTourDone();
    // No samples yet (the state the visitor lands on after provisioning): the
    // permanent line must not read "0.0x" (which reads as "Readyset does zero").
    mockDemo({ samples: [], loadRunning: false });
    render(<DemoPage />);
    const line = screen.getByTestId('lift-ratio');
    expect(line.textContent).toContain('Start traffic to see');
    expect(line.textContent).not.toContain('0.0x');
  });

  it('renders a benign no-traffic row as neutral "waiting for traffic", not an amber warning, and offers no manual toggle', () => {
    markTourDone();
    const waitingRow = row({
      key: 'W01',
      title: 'Idle workload query',
      status: 'not_eligible',
      hits: 0,
      reason: { kind: 'below_min_execution', count: 0, threshold: 5 },
    });
    mockDemo({ patterns: [waitingRow], loadRunning: false, samples: [] });
    render(<DemoPage />);
    expect(screen.getByText('waiting for traffic')).toBeTruthy();
    expect(screen.queryByText('not eligible yet')).toBeNull();
    // No manual-cache switch is offered before the query has ever run.
    expect(rowScope('Idle workload query').queryByRole('switch')).toBeNull();
  });

  it('shows the conviction bridge and hands off to Connect carrying the demo identity', () => {
    markTourDone();
    navigateSpy.mockClear();
    mockDemo({});
    render(<DemoPage />);
    expect(screen.getByText('Ready to see this on your data?')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Get this for your database/ }));
    expect(navigateSpy).toHaveBeenCalledWith({ to: '/onboarding', search: { from: 'demo' } });
  });

  it('guards teardown behind a confirm dialog and only tears down on explicit confirm', async () => {
    markTourDone();
    const tearDown = vi.fn();
    mockDemo({ tearDown });
    render(<DemoPage />);
    // The destructive control is demoted into the header kebab menu; selecting
    // it does not fire teardown — it opens a confirm with plain-words copy.
    openDemoActionsMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Shut down demo/ }));
    expect(tearDown).not.toHaveBeenCalled();
    expect(screen.getByText('Remove all demo containers and data? You can start it again anytime.')).toBeTruthy();
    // Cancel backs out without tearing down.
    fireEvent.click(screen.getByRole('button', { name: /Cancel/ }));
    expect(tearDown).not.toHaveBeenCalled();
    // Re-open the menu and confirm: only now does teardown fire, exactly once.
    // The dialog's confirm is a plain button; the menu item is a menuitem.
    openDemoActionsMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Shut down demo/ }));
    fireEvent.click(screen.getByRole('button', { name: /Shut down demo/ }));
    expect(tearDown).toHaveBeenCalledTimes(1);
  });

  it('shows the live cache budget in the popover, not the metric cutoff', () => {
    markTourDone();
    const patterns = [row({
      key: 'B01',
      title: 'Budget line query',
      group: 'heavy',
      status: 'pass_through',
      hits: 1200,
      direct_avg_ms: 5,
      reason: { kind: 'below_rank', rank: 12, metric: 'count_star', metric_value: 1200, cutoff: 1110 },
    })];
    mockDemo({ patterns, cacheBudget: 10, querypilotOn: true });
    render(<DemoPage />);

    fireEvent.click(rowScope('Budget line query').getByRole('button', { name: /details/i }));
    expect(document.body.textContent).toContain('budget: top 10');
    expect(document.body.textContent).not.toContain('top 1110');
  });

  it('renders chip popovers for every reason kind and expands rows to full SQL only', () => {
    markTourDone();
    mockDemo({ patterns: basePatterns, mode: 'sum_time', querypilotOn: true });
    render(<DemoPage />);

    fireEvent.click(rowScope('Revenue by category').getByRole('button', { name: /details/i }));
    expect(document.body.textContent).toContain('Cached: one of the top 10 most expensive queries right now.');
    fireEvent.click(rowScope('Orders per day (30d)').getByRole('button', { name: /details/i }));
    expect(document.body.textContent).toContain('Cached manually');
    fireEvent.click(rowScope('Customer reorder summary').getByRole('button', { name: /details/i }));
    expect(document.body.textContent).toContain('Not cached: not among the top 10 most frequently run queries right now.');
    fireEvent.click(rowScope('Cohort revenue (quarterly)').getByRole('button', { name: /details/i }));
    expect(document.body.textContent).toContain('3 of 5 runs');
    fireEvent.click(rowScope('Live server time').getByRole('button', { name: /details/i }));
    expect(document.body.textContent).toContain('Blocked: excluded from automatic caching');
    fireEvent.click(rowScope('Org chart walk').getByRole('button', { name: /details/i }));
    expect(document.body.textContent).toContain("Readyset can't cache this SQL shape");
    fireEvent.click(rowScope('Windowed loyalty rank').getByRole('button', { name: /details/i }));
    expect(document.body.textContent).toContain('not shaped for automatic selection');

    fireEvent.click(screen.getByText('Revenue by category'));
    expect(screen.getByTestId('sql-display').textContent).toContain('SELECT p.category');
    expect(screen.queryByText(/Query shape/)).toBeNull();
  });

  it('renders manual caching as a per-row switch that caches and uncaches', () => {
    markTourDone();
    const cache = vi.fn();
    const uncache = vi.fn();
    mockDemo({
      querypilotOn: false,
      cache,
      uncache,
      patterns: [
        row({ key: 'PT', title: 'Uncached query', status: 'pass_through', hits: 5 }),
        row({ key: 'MC', title: 'Manually cached query', status: 'cached_manual', hits: 5, reason: { kind: 'manual' } }),
      ],
    });
    render(<DemoPage />);

    const offSwitch = rowScope('Uncached query').getByRole('switch');
    const onSwitch = rowScope('Manually cached query').getByRole('switch');
    expect(offSwitch.getAttribute('aria-checked')).toBe('false');
    expect(onSwitch.getAttribute('aria-checked')).toBe('true');

    // Sliding off->on caches; on->off uncaches, through the existing handlers.
    fireEvent.click(offSwitch);
    expect(cache).toHaveBeenCalledWith('PT', 'Uncached query');
    fireEvent.click(onSwitch);
    expect(uncache).toHaveBeenCalledWith('MC', 'Manually cached query');
  });

  it('grays out and disables the per-row cache switches while QueryPilot is on', () => {
    markTourDone();
    const cache = vi.fn();
    mockDemo({
      querypilotOn: true,
      cache,
      patterns: [row({ key: 'PT', title: 'Uncached query', status: 'pass_through', hits: 5 })],
    });
    render(<DemoPage />);

    // The switch stays visible (grayed), not hidden, but cannot be toggled.
    const sw = rowScope('Uncached query').getByRole('switch') as HTMLButtonElement;
    expect(sw.disabled).toBe(true);
    fireEvent.click(sw);
    expect(cache).not.toHaveBeenCalled();
  });

  it('keeps a disabled, checked switch on QueryPilot-cached rows while QueryPilot is on', () => {
    markTourDone();
    const uncache = vi.fn();
    mockDemo({
      querypilotOn: true,
      uncache,
      patterns: [
        row({ key: 'QP', title: 'QueryPilot cached query', status: 'cached_querypilot', hits: 5, reason: { kind: 'selected', rank: 1, metric: 'count_star', metric_value: 900, cutoff: 10 } }),
        row({ key: 'PT', title: 'Uncached query', status: 'pass_through', hits: 5 }),
      ],
    });
    render(<DemoPage />);

    // Previously this row's switch vanished under QueryPilot; now it stays,
    // disabled and showing ON to reflect that the query is cached.
    const cachedSwitch = rowScope('QueryPilot cached query').getByRole('switch') as HTMLButtonElement;
    expect(cachedSwitch.disabled).toBe(true);
    expect(cachedSwitch.getAttribute('aria-checked')).toBe('true');

    // Uncached rows keep a disabled switch too, but showing OFF.
    const uncachedSwitch = rowScope('Uncached query').getByRole('switch') as HTMLButtonElement;
    expect(uncachedSwitch.disabled).toBe(true);
    expect(uncachedSwitch.getAttribute('aria-checked')).toBe('false');

    fireEvent.click(cachedSwitch);
    expect(uncache).not.toHaveBeenCalled();
  });

  it('confirms mode switches with an in-page dialog and shows the reset overlay while the PATCH is pending', async () => {
    markTourDone();
    let resolveMode: (() => void) | null = null;
    const setDiscoveryMode = vi.fn(() => new Promise<void>((resolve) => {
      resolveMode = resolve;
    }));
    const confirmSpy = vi.spyOn(window, 'confirm');
    mockDemo({ querypilotOn: true, setDiscoveryMode });
    render(<DemoPage />);

    fireEvent.click(screen.getByRole('button', { name: 'Most expensive' }));

    // The confirm is an in-page dialog styled like the tour bubbles; the browser
    // window.confirm / alert is banned.
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(screen.getByText('This resets all counters and starts the comparison over. QueryPilot drops every cache and re-selects under the new policy.')).toBeTruthy();
    // Command-and-control: the confirm no longer claims manual caches survive.
    expect(screen.queryByText(/cached manually are kept/)).toBeNull();
    expect(setDiscoveryMode).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Switch policy' }));
    expect(setDiscoveryMode).toHaveBeenCalledWith('sum_time');
    expect(screen.getByText('Counters resetting. Throughput dips while caches rebuild, then climbs as QueryPilot re-selects.')).toBeTruthy();

    expect(resolveMode).toBeTruthy();
    (resolveMode as unknown as () => void)();
    await waitFor(() => {
      expect(screen.queryByText('Counters resetting. Throughput dips while caches rebuild, then climbs as QueryPilot re-selects.')).toBeNull();
    });
  });

  it('cancels an in-page mode switch without calling the backend', () => {
    markTourDone();
    const setDiscoveryMode = vi.fn();
    mockDemo({ querypilotOn: true, mode: 'sum_time', setDiscoveryMode });
    render(<DemoPage />);

    fireEvent.click(screen.getByRole('button', { name: 'Most frequent' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(setDiscoveryMode).not.toHaveBeenCalled();
    expect(screen.queryByText(/This resets all counters/)).toBeNull();
  });

  it('drives the anchored walkthrough by events and Next, never revisiting Welcome', async () => {
    const hook = vi.spyOn(useDemoMod, 'useDemo');
    const welcome = /This demo spins up a small database/;
    let state = {
      ...makeBase(),
      // QueryPilot opens on the most-frequent policy in the new flow.
      mode: 'count_star',
      patterns: [
        row({ key: 'H01', title: 'Daily item revenue', group: 'expensive_aggregate', hits: 12, direct_avg_ms: 320, reason: { kind: 'below_rank', rank: 11, metric: 'sum_time_us', metric_value: 3_800_000 } }),
        row({ key: 'H02', title: 'Revenue by customer segment', group: 'expensive_aggregate', hits: 10, direct_avg_ms: 300, reason: { kind: 'below_rank', rank: 12, metric: 'sum_time_us', metric_value: 3_000_000 } }),
      ],
    } as DemoState;
    hook.mockImplementation(() => state);
    const { rerender } = render(<DemoPage />);

    expect(await screen.findByText(welcome)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Show me' }));
    expect(screen.getByText(/Traffic starts flowing to both paths/)).toBeTruthy();

    state = { ...state, loadRunning: true, samples: [sample(at, 1_000, 1_000)], windows: [sample(at, 1_000, 1_000)] };
    rerender(<DemoPage />);
    expect(await screen.findByText(/The same workload runs through Readyset \(teal\)/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText(/Every query in the workload/)).toBeTruthy();
    expect(screen.getByText(/Every query in the workload/)).toBeTruthy();

    // Hover/read steps never auto-advance: opening a status popover leaves the
    // table step in place; only its Next button advances.
    fireEvent.click(rowScope('Daily item revenue').getByRole('button', { name: /details/i }));
    expect(screen.getByText(/Every query in the workload/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText(/Two easy wins: cache these queries by hand/)).toBeTruthy();

    const cachedManual = state.patterns.map((p) => ({ ...p, status: 'cached_manual' as const, reason: { kind: 'manual' as const } }));
    state = { ...state, patterns: cachedManual };
    rerender(<DemoPage />);
    // After both easy wins are cached, the tour holds on those two rows to show
    // the per-query throughput jump, then Next advances to QueryPilot.
    expect(await screen.findByText(/Readyset latency drops sharply/)).toBeTruthy();
    // The two rows must STAY highlighted after caching (the cutout tracks the
    // suggested keys, not cacheability, which flips false once cached).
    expect(document.querySelectorAll('[data-tour-manual-suggested="true"]').length).toBe(2);
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText(/That was manual caching. Now, turn on QueryPilot/)).toBeTruthy();

    // Enable QueryPilot: the tour shows a visible chart-anchored waiting step
    // (no dead air) and must NOT flash Welcome again.
    state = { ...state, querypilotOn: true };
    rerender(<DemoPage />);
    expect(await screen.findByText(/QueryPilot is making its first pass/)).toBeTruthy();
    expect(screen.queryByText(welcome)).toBeNull();

    const frequentRow = row({ key: 'qp1', title: 'Product tile by id', group: 'cheap_point_lookup', status: 'cached_querypilot', hits: 900, direct_avg_ms: 5, router_avg_ms: 0.8, reason: { kind: 'selected', rank: 1, metric: 'count_star', metric_value: 900, cutoff: 20 } });
    state = { ...state, patterns: [...cachedManual, frequentRow] };
    rerender(<DemoPage />);
    expect(await screen.findByText(/the teal line climbs as Readyset serves them from memory/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText(/Hover a status chip to see why it was chosen/)).toBeTruthy();
    // The spotlight is pinned at step entry: a later-cached row that would
    // out-sort the pinned one must NOT steal the highlight mid-step.
    expect(document.querySelectorAll('[data-tour-cached-querypilot="true"]').length).toBe(1);
    const earlierCached = row({ key: 'aa1', title: 'Alphabetically first cached', group: 'cheap_point_lookup', status: 'cached_querypilot', hits: 800, direct_avg_ms: 4, router_avg_ms: 0.7, reason: { kind: 'selected', rank: 2, metric: 'count_star', metric_value: 800, cutoff: 20 } });
    state = { ...state, patterns: [...cachedManual, frequentRow, earlierCached] };
    rerender(<DemoPage />);
    const pinnedCachedRow = document.querySelector('[data-tour-cached-querypilot="true"]');
    expect(pinnedCachedRow?.textContent).toContain('Product tile by id');
    expect(document.querySelectorAll('[data-tour-cached-querypilot="true"]').length).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText(/Switch to Most expensive/)).toBeTruthy();

    // Switch to Most expensive: the re-selecting step anchors the chart while
    // the new caches build; Welcome must not re-appear here either. Its Next is
    // gated until QueryPilot fills the sum_time budget, so the why-not anchor
    // lands on a query that stays uncached rather than one still mid-pass.
    const uncachedCheap = row({ key: 'qp3', title: 'Product tile by id', group: 'cheap_point_lookup', status: 'pass_through', hits: 300, direct_avg_ms: 5, router_avg_ms: 5, reason: { kind: 'below_rank', rank: 14, metric: 'sum_time_us', metric_value: 1_500_000 } });
    state = { ...state, mode: 'sum_time', cacheBudget: 3, patterns: [...cachedManual, uncachedCheap] };
    rerender(<DemoPage />);
    expect(await screen.findByText(/re-selecting for Most expensive/)).toBeTruthy();
    expect(screen.queryByText(welcome)).toBeNull();
    // Majority not yet cached (2 of 3): Next is held disabled while caches land.
    expect((screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement).disabled).toBe(true);

    // An expensive query caches, filling the budget; the cheap query stays
    // uncached and is the stable why-not anchor. Next now enables.
    const expensiveRow2 = row({ key: 'qp2', title: 'Revenue by region', group: 'expensive_aggregate', status: 'cached_querypilot', hits: 40, direct_avg_ms: 260, router_avg_ms: 0.9, reason: { kind: 'selected', rank: 1, metric: 'sum_time_us', metric_value: 6_500_000, cutoff: 10 } });
    state = { ...state, patterns: [...cachedManual, expensiveRow2, uncachedCheap] };
    rerender(<DemoPage />);
    expect((screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText(/Hover the status of an uncached query to see why QueryPilot passed on it/)).toBeTruthy();
    // The why-not anchor pins at step entry too: a new uncached row that
    // would out-sort the pinned one must not move the spotlight.
    expect(document.querySelector('[data-tour-uncached="true"]')?.textContent).toContain('Product tile by id');
    const earlierUncached = row({ key: 'aa2', title: 'Alphabetically first uncached', group: 'cheap_point_lookup', status: 'pass_through', hits: 40, direct_avg_ms: 900, router_avg_ms: 900, reason: { kind: 'below_rank', rank: 20, metric: 'sum_time_us', metric_value: 100 } });
    state = { ...state, patterns: [...cachedManual, expensiveRow2, uncachedCheap, earlierUncached] };
    rerender(<DemoPage />);
    expect(document.querySelector('[data-tour-uncached="true"]')?.textContent).toContain('Product tile by id');
    expect(document.querySelectorAll('[data-tour-uncached="true"]').length).toBe(1);
    // The why-not step is a read step too: it advances via Next to the finale,
    // which carries the QueryPilot brand line and a Finish button.
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText(/QueryPilot keeps caching by the chosen policy/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Finish' }));
    expect(screen.queryByText(/Readyset QueryPilot keeps caching/)).toBeNull();

    // Replay tour now lives in the header kebab menu.
    openDemoActionsMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Replay tour/ }));
    expect(await screen.findByText(welcome)).toBeTruthy();
  });
});

describe('parameterizeSql', () => {
  it('replaces comparison literals and strings but keeps group-by ordinals', () => {
    const shape = parameterizeSql("SELECT id FROM orders WHERE id = 42 AND status = 'open' GROUP BY 1");
    expect(shape.text).toBe('SELECT id FROM orders WHERE id = $1 AND status = $2 GROUP BY 1');
    expect(shape.placeholders).toEqual(['$1', '$2']);
  });

  it('parameterizes function string arguments', () => {
    const shape = parameterizeSql("SELECT date_trunc('day', placed_at), count(*) FROM orders GROUP BY 1");
    expect(shape.text).toContain("date_trunc($1, placed_at)");
    expect(shape.text).toContain('GROUP BY 1');
  });
});

describe('eventDescription', () => {
  it('names the query a cache acted on from the recorded label', () => {
    expect(eventDescription({ t: at, type: 'manual_cache', label: 'you cached Orders per day (30d)' }))
      .toBe("Cached 'Orders per day (30d)' manually");
    expect(eventDescription({ t: at, type: 'manual_uncache', label: 'you uncached Orders per day (30d)' }))
      .toBe("Removed cache on 'Orders per day (30d)'");
  });

  it('describes QueryPilot and policy events specifically', () => {
    expect(eventDescription({ t: at, type: 'qp_on', label: 'QueryPilot on' })).toBe('QueryPilot turned on');
    expect(eventDescription({ t: at, type: 'qp_off', label: 'QueryPilot off' })).toBe('QueryPilot turned off');
    expect(eventDescription({ t: at, type: 'mode_change', label: 'mode changed to Most expensive' }))
      .toBe('Policy switched to Most expensive');
    expect(eventDescription({ t: at, type: 'mode_change', label: 'mode changed to Most frequent' }))
      .toBe('Policy switched to Most frequent');
  });
});

describe('windowLiftRatio', () => {
  it('averages the router/direct throughput over the recent window', () => {
    const s = [sample(at - 120, 100, 100), sample(at - 30, 200, 800), sample(at, 200, 1000)];
    // Only the last 60s count: mean(800,1000)/mean(200,200) = 900/200 = 4.5.
    expect(windowLiftRatio(s, 60)).toBeCloseTo(4.5, 5);
    // Rounds to a whole number for the final tour beat.
    expect(Math.round(windowLiftRatio(s, 60))).toBe(5);
  });

  it('returns 0 with no samples or zero direct throughput', () => {
    expect(windowLiftRatio([], 60)).toBe(0);
    expect(windowLiftRatio([sample(at, 0, 500)], 60)).toBe(0);
  });
});

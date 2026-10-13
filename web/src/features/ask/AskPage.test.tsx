import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AskPanel } from '@/components/AskPanel'
import { fetchAskExamples, fetchAskHistory } from '@/lib/api'
import { type AskResultEvent, useAsk } from '@/lib/ask'
import { downloadCsv } from '@/lib/csv'
import { renderWithClient } from '@/test-utils'

const navigate = vi.fn()

vi.mock('@/lib/ask', () => ({ useAsk: vi.fn() }))
vi.mock('@/lib/csv', () => ({
  toCsv: vi.fn(() => 'csv'),
  downloadCsv: vi.fn(),
  createCsvFilename: vi.fn(() => 'rows.csv'),
}))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAskExamples: vi.fn(async () => ({
    examples: [],
    source: 'introspection',
  })),
  fetchAskHistory: vi.fn(async () => ({ items: [] })),
}))
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => navigate,
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}))

type Ask = ReturnType<typeof useAsk>

function askState(overrides: Partial<Ask> = {}): Ask {
  return {
    ask: vi.fn(async () => {}),
    resumeWithAnswers: vi.fn(async () => {}),
    cancel: vi.fn(),
    state: 'idle',
    status: undefined,
    schemaLoaded: undefined,
    clarification: undefined,
    sqlGenerated: undefined,
    result: undefined,
    error: undefined,
    reset: vi.fn(),
    ...overrides,
  }
}

const SQL =
  "SELECT p.id, p.title, COUNT(v.id) AS votes, p.score, p.answercount FROM votes v JOIN posts p ON p.id = v.postid WHERE v.votetypeid = 2 AND v.creationdate >= now() - INTERVAL '7 days' GROUP BY p.id ORDER BY votes DESC LIMIT 10"

function answer(overrides: Partial<AskResultEvent> = {}): AskResultEvent {
  return {
    type: 'result',
    success: true,
    sql: `${SQL} LIMIT 1000`,
    columns: ['Post', 'Votes', 'Score', 'Answers'],
    rows: [
      ['Why does Postgres choose a sequential scan?', 412, 388, 7],
      ['Index on (a, b) vs two indexes', 201, 190, 4],
      ['EXPLAIN ANALYZE buffers: what to read first', 176, 171, 3],
      ['Vacuum never finishes on a hot table', 143, 130, 5],
      ['LIMIT with ORDER BY on a joined column', 98, 95, 2],
      ['shared_buffers: how big is too big?', 87, 80, 6],
      ['A seventh post', 10, 9, 1],
    ],
    row_count: 7,
    execution_time_ms: 400,
    llm_calls: 1,
    total_tokens: 10,
    query_hash: 'hash-1',
    query_tag: 'votes-this-week',
    limit_added: true,
    ...overrides,
  } as AskResultEvent
}

const history = [
  {
    hash: 'h1',
    question: 'Which 10 users have the most accepted answers?',
    sql: '',
    tag: '',
    target: 'demo',
    last_used: new Date().toISOString(),
  },
  {
    hash: 'h2',
    question: 'Which users have earned the most gold badges?',
    sql: '',
    tag: '',
    target: 'demo',
    last_used: '2026-09-18T11:00:00',
  },
]

function renderAsk(props: Partial<React.ComponentProps<typeof AskPanel>> = {}) {
  return renderWithClient(
    <AskPanel
      target="demo"
      initialQuestion="Which posts got the most votes this week?"
      {...props}
    />
  )
}

beforeEach(() => {
  navigate.mockReset()
  vi.mocked(fetchAskHistory).mockResolvedValue({ items: [] })
  vi.mocked(fetchAskExamples).mockResolvedValue({
    examples: [],
    source: 'introspection',
  })
})
afterEach(cleanup)

describe('the box', () => {
  it('ignores Enter while a question runs', () => {
    const ask = askState({ state: 'generating' })
    vi.mocked(useAsk).mockReturnValue(ask)
    renderAsk()
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Question' }), {
      key: 'Enter',
    })
    expect(ask.ask).not.toHaveBeenCalled()
  })

  it('refetches Earlier questions when a run completes', async () => {
    vi.mocked(fetchAskHistory).mockClear()
    vi.mocked(useAsk).mockReturnValue(askState())
    const { rerender } = renderAsk()
    await waitFor(() => expect(fetchAskHistory).toHaveBeenCalledTimes(1))
    vi.mocked(useAsk).mockReturnValue(
      askState({ state: 'complete', result: answer() })
    )
    rerender(
      <AskPanel
        target="demo"
        initialQuestion="Which posts got the most votes this week?"
      />
    )
    await waitFor(() => expect(fetchAskHistory).toHaveBeenCalledTimes(2))
  })

  it('asks for values while the question has placeholders, and clears the link once asked', async () => {
    const ask = askState()
    vi.mocked(useAsk).mockReturnValue(ask)
    renderAsk({ initialQuestion: 'SELECT * FROM t WHERE id = $1' })
    const note = 'This query has parameters. Say which values to use.'
    expect(screen.getByText(note)).toBeTruthy()
    const box = screen.getByRole('textbox', { name: 'Question' })
    fireEvent.change(box, { target: { value: 'SELECT * FROM t WHERE id = 7' } })
    expect(screen.queryByText(note)).toBeNull()
    fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(ask.ask).toHaveBeenCalled())
    expect(navigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '/ask', replace: true })
    )
  })

  it('is the page: the caption, Enter and the one Ask button', async () => {
    const ask = askState()
    vi.mocked(useAsk).mockReturnValue(ask)
    renderAsk()
    const box = screen.getByTestId('ask-box')
    expect(
      within(box).getByText(
        'Read-only on demo. 100 rows unless you ask for more.'
      )
    ).toBeTruthy()
    expect(within(box).getByText('Enter')).toBeTruthy()
    fireEvent.keyDown(within(box).getByRole('textbox', { name: 'Question' }), {
      key: 'Enter',
    })
    await waitFor(() =>
      expect(ask.ask).toHaveBeenCalledWith({
        question: 'Which posts got the most votes this week?',
        target: 'demo',
      })
    )
  })

  it('on a first visit, offers three questions the schema can answer', async () => {
    vi.mocked(useAsk).mockReturnValue(askState())
    vi.mocked(fetchAskExamples).mockResolvedValue({
      examples: [
        'Which users have earned the most gold badges?',
        'What are the most popular tags on posts scoring over 100?',
        'How many comments has each user with over 10,000 reputation written?',
        'A fourth one',
      ],
      source: 'introspection',
    })
    renderAsk({ initialQuestion: '' })
    const rows = await screen.findAllByTestId('ask-suggestion')
    expect(rows).toHaveLength(3)
    expect(
      screen.getByPlaceholderText('Ask demo anything its tables can answer')
    ).toBeTruthy()
    fireEvent.click(rows[1])
    expect(
      (screen.getByRole('textbox', { name: 'Question' }) as HTMLTextAreaElement)
        .value
    ).toBe('What are the most popular tags on posts scoring over 100?')
    expect(screen.queryByTestId('ask-earlier')).toBeNull()
  })
})

describe('answering', () => {
  it('shows the three steps, the usual wait and a Cancel', () => {
    const ask = askState({
      state: 'generating',
      status: { type: 'status', phase: 'generate', message: '' },
    })
    vi.mocked(useAsk).mockReturnValue(ask)
    renderAsk()
    const steps = screen.getByRole('list', { name: 'Answer steps' })
    expect(
      within(steps)
        .getAllByRole('listitem')
        .map((item) => item.getAttribute('data-state'))
    ).toEqual(['done', 'now', 'todo'])
    expect(screen.getByText('Usually under 5 s')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(ask.cancel).toHaveBeenCalled()
    expect(
      (screen.getByRole('button', { name: 'Ask' }) as HTMLButtonElement)
        .disabled
    ).toBe(true)
  })

  it('asks one question when the question is ambiguous and runs with the choice', () => {
    const ask = askState({
      state: 'clarification_needed',
      clarification: {
        type: 'clarification_needed',
        session_id: 's1',
        questions: [
          {
            id: 'top',
            question: 'Top by what?',
            options: ['Reputation', 'Accepted answers', 'Posts this month'],
          },
        ],
        interpretations: [
          {
            id: 1,
            description: 'users.reputation, highest first',
            likelihood: 0.6,
            assumptions: [],
          },
          {
            id: 2,
            description:
              "posts where acceptedanswerid points at the user's answer",
            likelihood: 0.3,
            assumptions: [],
          },
          {
            id: 3,
            description: 'count of posts since October 1',
            likelihood: 0.1,
            assumptions: [],
          },
        ],
      },
    })
    vi.mocked(useAsk).mockReturnValue(ask)
    renderAsk({ initialQuestion: 'Who are the top users?' })
    expect(screen.getByText('Top by what?')).toBeTruthy()
    expect(screen.getAllByRole('radio')).toHaveLength(3)
    expect(screen.getByText('users.reputation, highest first')).toBeTruthy()
    fireEvent.click(screen.getByRole('radio', { name: /Accepted answers/ }))
    fireEvent.click(
      screen.getByRole('button', { name: 'Run with accepted answers' })
    )
    expect(ask.resumeWithAnswers).toHaveBeenCalledWith({
      top: 'Accepted answers',
    })
  })
})

describe('the answer', () => {
  it('opens with the status line and the lead, then the table and the ghosts', async () => {
    const ask = askState({
      state: 'complete',
      result: answer(),
      sqlGenerated: {
        type: 'sql_generated',
        sql: SQL,
        explanation:
          'Upvotes per post from votes joined to posts, votes created in the last 7 days, top 10.',
      },
      schemaLoaded: {
        type: 'schema_loaded',
        target: 'demo',
        table_count: 7,
        source: 'introspection',
      } as Ask['schemaLoaded'],
    })
    vi.mocked(useAsk).mockReturnValue(ask)
    renderAsk()
    const status = screen.getByTestId('ask-status')
    expect(status.textContent).toContain('Answered from demo')
    expect(status.textContent).toContain('7 rows')
    expect(status.textContent).toContain('0.4 s')
    expect(status.textContent).toContain('saved as votes-this-week')
    expect(
      screen.getByText(
        '"Why does Postgres choose a sequential scan?" leads with 412 votes, more than twice the next post.'
      )
    ).toBeTruthy()
    const table = screen.getByTestId('ask-table')
    expect(within(table).getByText('Votes')).toBeTruthy()
    expect(within(table).getAllByTestId('ask-row')).toHaveLength(6)
    expect(within(table).queryByText('A seventh post')).toBeNull()
    fireEvent.click(within(table).getByRole('button', { name: '1 more row' }))
    expect(within(table).getByText('A seventh post')).toBeTruthy()
    fireEvent.click(within(table).getByRole('button', { name: 'Download CSV' }))
    expect(downloadCsv).toHaveBeenCalledWith('csv', 'rows.csv')

    expect(
      screen.getByRole('button', { name: 'Open in Database' })
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: "Ask again with today's data" })
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Analyze query' }))
    expect(navigate).toHaveBeenCalledWith({
      to: '/results',
      search: { query: `${SQL} LIMIT 1000`, target: 'demo', origin: 'ask' },
    })

    expect(screen.queryByTestId('ask-sql')).toBeNull()
    const disclosure = screen.getByRole('button', { name: /SQL that ran/ })
    expect(disclosure.textContent).toContain('LIMIT 1000 added')
    fireEvent.click(disclosure)
    expect(screen.getByTestId('ask-sql').textContent).toContain('FROM votes v')
  })

  it('says when the query ran and matched nothing', () => {
    const ask = askState({
      state: 'complete',
      result: answer({ rows: [], row_count: 0, execution_time_ms: 300 }),
      sqlGenerated: {
        type: 'sql_generated',
        sql: SQL,
        explanation: 'No posts had votes in the last 7 days.',
      },
    })
    vi.mocked(useAsk).mockReturnValue(ask)
    renderAsk()
    const status = screen.getByTestId('ask-status')
    expect(status.textContent).toContain('Query ran, no rows')
    expect(status.textContent).toContain('0.3 s')
    expect(
      screen.getByText('No posts had votes in the last 7 days.')
    ).toBeTruthy()
    expect(screen.queryByTestId('ask-table')).toBeNull()
    expect(screen.getByRole('button', { name: 'Edit question' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Show the SQL' }))
    expect(screen.getByTestId('ask-sql')).toBeTruthy()
  })

  it('names the database and its error when the query failed, with the SQL in view', () => {
    const ask = askState({
      state: 'error',
      error: {
        type: 'error',
        phase: 'execute',
        message: 'permission denied for table votes',
      } as Ask['error'],
      sqlGenerated: { type: 'sql_generated', sql: SQL, explanation: null },
    })
    vi.mocked(useAsk).mockReturnValue(ask)
    renderAsk()
    expect(
      screen.getAllByText(
        'demo refused the query: permission denied for table votes.'
      ).length
    ).toBeGreaterThan(0)
    expect(screen.getByTestId('ask-sql')).toBeTruthy()
    expect(screen.getByRole('button', { name: /SQL that failed/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Edit question' }))
    expect(ask.reset).toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(ask.ask).toHaveBeenCalledWith({
      question: 'Which posts got the most votes this week?',
      target: 'demo',
    })
  })
})

describe('Earlier', () => {
  it('lists the questions by day and asks one again', async () => {
    const ask = askState()
    vi.mocked(useAsk).mockReturnValue(ask)
    vi.mocked(fetchAskHistory).mockResolvedValue({ items: history })
    renderAsk({ initialQuestion: '' })
    const earlier = await screen.findByTestId('ask-earlier')
    expect(
      within(earlier).getByRole('heading', { name: 'Earlier' })
    ).toBeTruthy()
    await within(earlier).findByText('2 questions on demo')
    expect(within(earlier).getByText('Today')).toBeTruthy()
    expect(within(earlier).getByText('September 18')).toBeTruthy()
    expect(within(earlier).getByText('Sep 18')).toBeTruthy()
    fireEvent.click(
      within(earlier).getAllByRole('button', { name: 'Ask again' })[1]
    )
    await waitFor(() =>
      expect(ask.ask).toHaveBeenCalledWith({
        question: 'Which users have earned the most gold badges?',
        target: 'demo',
      })
    )
    expect(screen.queryByTestId('ask-suggestion')).toBeNull()
  })
})

it('lets the question be typed while the database is still resolving', () => {
  renderAsk({ target: null, targetResolving: true, initialQuestion: '' })
  const box = screen.getByRole('textbox', { name: 'Question' })
  expect((box as HTMLTextAreaElement).disabled).toBe(false)
  fireEvent.change(box, { target: { value: 'How many orders?' } })
  const ask = screen.getByRole('button', { name: 'Ask' })
  expect((ask as HTMLButtonElement).disabled).toBe(true)
})

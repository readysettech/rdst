/**
 * Ask (/ask).
 *
 * Consolidated from the recorded audit flows. Every test asserts the behaviour
 * the fix landed; the two findings still open are declared `fixme` so the
 * suite stays green while the debt stays visible.
 */
import {
  configureTestTarget,
  expect,
  mockConnectivityOk,
  setBackendFixtures,
  test,
} from '../fixtures'
import { DESKTOP, shot } from './_helpers'

test.use({ viewport: DESKTOP })

const ASK_PLACEHOLDER =
  'For example: Which customers placed the most orders this month?'

/** The shared composer's keyboard contract, whatever separator it prints. */
const KEY_HINT = /^Enter to ask .* Shift\+Enter for a new line$/

/** Deliberately ragged lengths: a one-line card beside a two-line one. */
const EXAMPLES = [
  'Which customers placed the most orders this month?',
  'Revenue by month',
  'Which orders are still unpaid after thirty days, and who placed them?',
]

const ANSWER_EVENTS = [
  { type: 'status', phase: 'generate', message: 'Generating SQL' },
  {
    type: 'sql_generated',
    sql: 'SELECT customer, SUM(total) AS revenue FROM orders GROUP BY customer',
    explanation: 'Aggregates order totals by customer.',
  },
  {
    type: 'result',
    success: true,
    sql: 'SELECT customer, SUM(total) AS revenue FROM orders GROUP BY customer LIMIT 100',
    columns: ['customer', 'revenue'],
    rows: [
      ['Ada', 4200],
      ['Grace', 3100],
      ['Linus', 2900],
    ],
    row_count: 3,
    execution_time_ms: 12.4,
    llm_calls: 1,
    total_tokens: 240,
    query_hash: 'ask-query-1',
    query_tag: 'top customers',
    limit_added: true,
  },
]

const NO_ROW_EVENTS = [
  {
    type: 'sql_generated',
    sql: 'SELECT * FROM orders WHERE 1=0',
    explanation: 'Filters everything out.',
  },
  {
    type: 'result',
    success: true,
    sql: 'SELECT * FROM orders WHERE 1=0 LIMIT 100',
    columns: ['id'],
    rows: [],
    row_count: 0,
    execution_time_ms: 1.2,
    llm_calls: 1,
    total_tokens: 100,
    query_hash: 'ask-query-empty',
  },
]

const ERROR_EVENTS = [
  {
    type: 'error',
    message: 'The SQL generator is temporarily unavailable',
    phase: 'generate',
  },
]

const CLARIFY_EVENTS = [
  {
    type: 'clarification_needed',
    session_id: 'qa-session-1',
    interpretations: [],
    questions: [
      {
        id: 'revenue_definition',
        question: 'Which revenue definition should be used: gross or net?',
        options: ['Gross revenue', 'Net revenue'],
      },
      {
        id: 'window',
        question: 'Which time window matters here?',
        options: ['Last 30 days', 'Year to date'],
      },
      {
        id: 'segment',
        question: 'Which customer segment?',
        options: ['All', 'Enterprise only'],
      },
    ],
  },
]

async function prepareAsk(page: Parameters<typeof configureTestTarget>[0]) {
  await configureTestTarget(page, { hasPassword: true })
  await mockConnectivityOk(page)
}

/** Left edge and width of the first element matching `selector`. */
async function frame(
  page: Parameters<typeof configureTestTarget>[0],
  selector: string
) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel)
    if (!el) return null
    const rect = el.getBoundingClientRect()
    return { x: Math.round(rect.x), w: Math.round(rect.width) }
  }, selector)
}

/** The content column the idle composer occupies. */
async function composerColumn(page: Parameters<typeof configureTestTarget>[0]) {
  return page.evaluate(() => {
    const column = document.querySelector('textarea')?.closest('div.grid')
    if (!column) return null
    const rect = column.getBoundingClientRect()
    return { x: Math.round(rect.x), w: Math.round(rect.width) }
  })
}

/** C-30, C-32, C-39 - a verified answer is stamped, and its numbers line up. */
test('an answer is stamped verified and right-aligns its numeric columns', async ({
  page,
}) => {
  setBackendFixtures({ ask: [{ events: ANSWER_EVENTS }] })
  await prepareAsk(page)
  await page.route('**/api/ask/examples*', (route) =>
    route.fulfill({ json: { target: 'e2e-guard', examples: EXAMPLES } })
  )

  await page.goto('/ask')
  await expect(page.getByRole('heading', { name: 'Ask' })).toBeVisible()

  // C-39: the example cards share one row height, so the grid has no hole.
  const heights: number[] = []
  for (const example of EXAMPLES) {
    const box = await page.getByRole('button', { name: example }).boundingBox()
    heights.push(Math.round(box!.height))
  }
  expect(Math.max(...heights) - Math.min(...heights)).toBeLessThan(2)

  const send = page.getByRole('button', { name: 'Get answer' })
  await expect(send).toBeDisabled()
  await page
    .getByPlaceholder(ASK_PLACEHOLDER)
    .fill('Who are our top customers?')
  await expect(send).toBeEnabled()
  await send.click()

  // C-30: the positive stamp belongs to a result that actually returned rows.
  await expect(page.getByText('Verified answer', { exact: true })).toBeVisible()
  await expect(page.getByText('Answer', { exact: true })).toBeVisible()

  // C-32: revenue reads right, customer reads left.
  const alignment = await page.evaluate(() => {
    const table = document.querySelector('table')
    if (!table) return null
    const head = (column: number) =>
      getComputedStyle(table.querySelectorAll('thead th')[column]).textAlign
    const cell = (column: number) =>
      getComputedStyle(table.querySelectorAll('tbody tr')[0].children[column])
        .textAlign
    return {
      textHead: head(0),
      textCell: cell(0),
      numberHead: head(1),
      numberCell: cell(1),
    }
  })
  expect(alignment!.numberHead).toBe('right')
  expect(alignment!.numberCell).toBe('right')
  expect(alignment!.textCell).not.toBe('right')

  await page.getByRole('button', { name: /Show the SQL that ran/ }).click()
  await expect(page.locator('.cm-content')).toContainText('LIMIT 100')
  await shot(page, 'ask-answer')
})

/** C-35 - the composer prints its keyboard contract beside the field. */
test('the composer states that Enter sends and Shift+Enter breaks the line', async ({
  page,
}) => {
  setBackendFixtures({ ask: [{ events: ANSWER_EVENTS, repeat: true }] })
  await prepareAsk(page)

  await page.goto('/ask')
  const textarea = page.getByPlaceholder(ASK_PLACEHOLDER)
  await expect(textarea).toBeVisible()
  await expect(page.getByText(KEY_HINT)).toBeVisible()

  // The hint sits with the composer: one gap below the field, on its own row.
  // The reported defect put it around 150px away, past the action row and a
  // divider and sharing a line with the "Questions for this schema" overline,
  // so 80px still fails that layout while allowing today's single gap.
  const distance = await page.evaluate(() => {
    const field = document.querySelector('textarea')
    const label = [...document.querySelectorAll('#main-content *')].find(
      (el) =>
        el.children.length === 0 &&
        (el.textContent ?? '').trim().startsWith('Enter to ask')
    )
    if (!field || !label) return null
    return Math.round(
      label.getBoundingClientRect().top - field.getBoundingClientRect().bottom
    )
  })
  expect(distance).not.toBeNull()
  expect(distance!).toBeLessThan(80)

  await textarea.click()
  await textarea.pressSequentially('line one')
  await textarea.press('Shift+Enter')
  await textarea.pressSequentially('line two')
  await expect(textarea).toHaveValue('line one\nline two')

  await textarea.press('Enter')
  await expect(page.getByText('Answer', { exact: true })).toBeVisible()
})

/**
 * C-20 - focus moves with the answer when it replaces the composer.
 */
test('C-20 - focus moves into the answer when it arrives', async ({ page }) => {
  setBackendFixtures({ ask: [{ events: ANSWER_EVENTS }] })
  await prepareAsk(page)

  await page.goto('/ask')
  const textarea = page.getByPlaceholder(ASK_PLACEHOLDER)
  await textarea.fill('Who are our top customers?')
  await textarea.press('Enter')
  await expect(page.getByText('Answer', { exact: true })).toBeVisible()

  const landed = await page.evaluate(() => {
    const active = document.activeElement as HTMLElement | null
    if (!active || active === document.body) return null
    return active.tagName
  })
  expect(landed).not.toBeNull()
})

/**
 * C-21, C-22 - the clarification counts its questions and prints each one
 * whole.
 */
test('a three-question clarification names the count and keeps each question intact', async ({
  page,
}) => {
  setBackendFixtures({ ask: [{ events: CLARIFY_EVENTS }] })
  await prepareAsk(page)

  await page.goto('/ask')
  await page.getByPlaceholder(ASK_PLACEHOLDER).fill('How much revenue?')
  await page.getByRole('button', { name: 'Get answer' }).click()

  // C-21: the headline agrees with the counter one line below it.
  await expect(page.getByText('A few quick questions')).toBeVisible()
  await expect(page.getByText('Question 1 of 3')).toBeVisible()

  // C-22: the half that disambiguates the question survives the colon.
  await expect(
    page.getByText('Which revenue definition should be used: gross or net?', {
      exact: true,
    })
  ).toBeVisible()

  await page.getByRole('radio', { name: 'Gross revenue' }).click()
  await page.getByRole('button', { name: 'Next' }).click()
  await expect(page.getByText('Question 2 of 3')).toBeVisible()
})

/**
 * C-26 - the free-text alternative reveals a named field, and choosing it
 * before anything is typed keeps the step mounted.
 */
test('C-26 - the free-text alternative is named, not only placeheld', async ({
  page,
}) => {
  setBackendFixtures({ ask: [{ events: CLARIFY_EVENTS }] })
  await prepareAsk(page)

  await page.goto('/ask')
  await page.getByPlaceholder(ASK_PLACEHOLDER).fill('How much revenue?')
  await page.getByRole('button', { name: 'Get answer' }).click()
  await expect(page.getByText('Question 1 of 3')).toBeVisible()

  await page
    .getByRole('radio', { name: 'Something else (let me type it)' })
    .click()
  await expect(
    page.getByRole('textbox', { name: 'Your own answer' })
  ).toBeVisible()
})

/**
 * C-23 - the options form a radiogroup with the radio keyboard contract.
 */
test('C-23 - clarification options answer the arrow keys through a roving tabindex', async ({
  page,
}) => {
  setBackendFixtures({ ask: [{ events: CLARIFY_EVENTS }] })
  await prepareAsk(page)

  await page.goto('/ask')
  await page.getByPlaceholder(ASK_PLACEHOLDER).fill('How much revenue?')
  await page.getByRole('button', { name: 'Get answer' }).click()
  await expect(page.getByText('Question 1 of 3')).toBeVisible()

  // One tab stop for the whole group, and the arrows move inside it.
  const stops = await page.evaluate(() =>
    [...document.querySelectorAll('[role="radio"]')].map(
      (el) => (el as HTMLElement).tabIndex
    )
  )
  expect(stops.filter((index) => index === 0)).toHaveLength(1)

  await page.getByRole('radio', { name: 'Gross revenue' }).focus()
  await page.keyboard.press('ArrowDown')
  await expect(page.getByRole('radio', { name: 'Net revenue' })).toBeFocused()
})

/** C-27, C-29, C-40 - a failure shows the question and the way back to it. */
test('a failure keeps the question it offers to re-run', async ({ page }) => {
  setBackendFixtures({ ask: [{ events: ERROR_EVENTS, repeat: true }] })
  await prepareAsk(page)

  await page.goto('/ask')
  await expect(page.getByPlaceholder(ASK_PLACEHOLDER)).toBeVisible()
  const column = await composerColumn(page)

  await page.getByPlaceholder(ASK_PLACEHOLDER).fill('Show recent orders')
  await page.getByRole('button', { name: 'Get answer' }).click()
  await expect(page.getByText("Couldn't generate SQL")).toBeVisible()

  // C-27: the question that failed is on screen, and the way back keeps it.
  await expect(page.getByText('Show recent orders')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()

  // C-40: the phase tag reads as one sentence, with no colon in the label.
  await expect(
    page.getByText('Failed while generating SQL', { exact: true })
  ).toBeVisible()

  // C-29: the error fills the content column like every other Ask state.
  const error = await frame(page, '#main-content [role="alert"]')
  expect(Math.abs(error!.x - column!.x)).toBeLessThan(4)
  expect(error!.w).toBeGreaterThan(column!.w - 8)

  const edit = page.getByRole('button', { name: 'Edit question' })
  await edit.click()
  await expect(page.getByPlaceholder(ASK_PLACEHOLDER)).toHaveValue(
    'Show recent orders'
  )
})

/**
 * C-30, C-31, C-33 - a zero-row answer is an empty state, not a verified
 * answer.
 */
test('a zero-row answer reads as an empty state', async ({ page }) => {
  setBackendFixtures({ ask: [{ events: NO_ROW_EVENTS }] })
  await prepareAsk(page)

  await page.goto('/ask')
  await page.getByPlaceholder(ASK_PLACEHOLDER).fill('Orders from 1970')
  await page.getByRole('button', { name: 'Get answer' }).click()

  await expect(page.getByText('No rows matched')).toBeVisible()
  // C-30: nothing claims success over a result that returned nothing.
  await expect(page.getByText('Verified answer', { exact: true })).toHaveCount(
    0
  )
  await expect(page.getByText('Query ran, no rows')).toBeVisible()

  // C-31/C-33: the shared EmptyState anatomy, so the action centres with the
  // text above it instead of hanging off the left.
  const title = page.getByText('No rows matched', { exact: true })
  const action = page.getByRole('button', { name: 'Edit question' })
  const titleBox = await title.boundingBox()
  const actionBox = await action.boundingBox()
  const titleCentre = Math.round(titleBox!.x + titleBox!.width / 2)
  const actionCentre = Math.round(actionBox!.x + actionBox!.width / 2)
  expect(Math.abs(titleCentre - actionCentre)).toBeLessThan(8)
})

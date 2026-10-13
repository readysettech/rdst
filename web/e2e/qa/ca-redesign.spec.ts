/**
 * The redesign's consistency rules (plan 3.7), ported from the mockup audit
 * (`docs/plans/ca-redesign-mockups/audit.mjs`) to the built app: the 1120
 * content axis, the page bar's full-width hairline, one solid button per view,
 * purple only on sign-in, the type scale, chips that never wrap, two-pane pages
 * that never scroll globally, and the nav highlight. Runs against the
 * `?monitorFixture=` scenarios, one entry per page and state the waves built:
 * Home, Database with its panes and dialogs, Inbox, Actions, Activity, Report,
 * the full analysis, Ask, Settings, Help, Readyset Platform and sign-in.
 */
import { configureTestTarget, expect, test } from '../fixtures'

const VIEWPORT = { width: 1800, height: 1125 }

/** Font sizes the mockups use, in px; everything else is off the scale. */
const TYPE_SCALE = new Set([
  11, 12, 12.5, 13, 14, 15, 17, 20, 22, 24, 28, 40, 48,
])

/** The rising purple, as the browser reports `surface-rising-solid`. */
const PURPLE = 'rgb(110, 86, 207)'

/** The nav highlight of a database's own page: its item under the Databases caption. */
const DATABASE_ITEM = 'Databases'

interface Scenario {
  id: string
  path: string
  /**
   * The sidebar item marked current, `DATABASE_ITEM` for a database's item, or
   * null when none should be.
   */
  nav: string | null
  /** The page bar title; sign-in has no bar. */
  title: string | null
  /** The page never scrolls globally and has two panels. */
  twoPane?: boolean
  /** A shell-less page: no sidebar, no canvas. */
  bare?: boolean
  /** A dialog the URL opens over the page, by its title. */
  dialog?: string
}

const SCENARIOS: Scenario[] = [
  { id: 'home', path: '/?monitorFixture=ca-home', nav: 'Home', title: 'Home' },
  {
    id: 'home-one',
    path: '/?monitorFixture=ca-home-one',
    nav: 'Home',
    title: 'Home',
  },
  {
    id: 'home-empty',
    path: '/?monitorFixture=ca-home-empty',
    nav: 'Home',
    title: 'Home',
  },
  {
    id: 'database',
    path: '/database?monitorFixture=ca-database&view=live',
    nav: DATABASE_ITEM,
    title: null,
    twoPane: true,
  },
  {
    id: 'database-24h',
    path: '/database?monitorFixture=ca-database&view=live&range=24h',
    nav: DATABASE_ITEM,
    title: null,
    twoPane: true,
  },
  {
    id: 'database-latency',
    path: '/database?monitorFixture=ca-database&view=live&metric=latency',
    nav: DATABASE_ITEM,
    title: null,
    twoPane: true,
  },
  {
    id: 'database-first-run',
    path: '/database?monitorFixture=ca-first-run&view=live',
    nav: DATABASE_ITEM,
    title: null,
    twoPane: true,
  },
  {
    id: 'database-unmonitored',
    path: '/database?monitorFixture=ca-unmonitored',
    nav: DATABASE_ITEM,
    title: null,
    twoPane: true,
  },
  {
    id: 'database-unreachable',
    path: '/database?monitorFixture=ca-unreachable',
    nav: DATABASE_ITEM,
    title: null,
    twoPane: true,
  },
  {
    id: 'database-open',
    path: '/database?monitorFixture=ca-database&query=ca0000000003',
    nav: DATABASE_ITEM,
    title: null,
    twoPane: true,
  },
  {
    id: 'database-settings',
    path: '/database?monitorFixture=ca-database&panel=settings',
    nav: DATABASE_ITEM,
    title: null,
    twoPane: true,
  },
  {
    id: 'database-analyze',
    path: '/database?monitorFixture=ca-database&panel=analyze',
    nav: DATABASE_ITEM,
    title: null,
    twoPane: true,
    dialog: 'Analyze a query',
  },
  {
    id: 'database-enable',
    path: '/database?monitorFixture=ca-unmonitored&panel=enable',
    nav: DATABASE_ITEM,
    title: null,
    twoPane: true,
    dialog: 'Monitor e2e-guard',
  },
  { id: 'settings', path: '/configure', nav: 'Settings', title: 'Settings' },
  { id: 'help', path: '/help', nav: 'Help', title: 'Help' },
  {
    id: 'platform',
    path: '/platform',
    nav: 'Readyset Platform',
    title: 'Readyset Platform',
  },
  { id: 'sign-in', path: '/account-login', nav: null, title: null, bare: true },
  {
    id: 'inbox',
    path: '/inbox?monitorFixture=ca-inbox',
    nav: 'Inbox',
    title: 'Inbox',
    twoPane: true,
  },
  {
    id: 'inbox-stuck',
    path: '/inbox?monitorFixture=ca-inbox&item=1',
    nav: 'Inbox',
    title: 'Inbox',
    twoPane: true,
  },
  {
    id: 'inbox-changes',
    path: '/inbox?monitorFixture=ca-inbox&item=2',
    nav: 'Inbox',
    title: 'Inbox',
    twoPane: true,
  },
  {
    id: 'inbox-report',
    path: '/inbox?monitorFixture=ca-inbox-report&item=3',
    nav: 'Inbox',
    title: 'Inbox',
    twoPane: true,
  },
  {
    id: 'inbox-closed',
    path: '/inbox?monitorFixture=ca-inbox&state=closed&item=4',
    nav: 'Inbox',
    title: 'Inbox',
    twoPane: true,
  },
  {
    id: 'inbox-empty',
    path: '/inbox?monitorFixture=ca-home-empty',
    nav: 'Inbox',
    title: 'Inbox',
    twoPane: true,
  },
  {
    id: 'actions',
    path: '/actions?monitorFixture=ca-database',
    nav: 'Actions',
    title: 'Actions',
    twoPane: true,
  },
  {
    id: 'actions-open',
    path: '/actions?monitorFixture=ca-database&action=1',
    nav: 'Actions',
    title: 'Actions',
    twoPane: true,
  },
  {
    id: 'actions-applied',
    path: '/actions?monitorFixture=ca-database&scope=applied',
    nav: 'Actions',
    title: 'Actions',
    twoPane: true,
  },
  {
    id: 'activity',
    path: '/agent-activity?monitorFixture=ca-home',
    nav: 'Activity',
    title: 'Activity',
  },
  {
    id: 'report',
    path: '/database?monitorFixture=ca-report&view=report',
    nav: DATABASE_ITEM,
    title: null,
    twoPane: true,
  },
  {
    id: 'report-signed-out',
    path: '/database?monitorFixture=ca-signed-out&view=report',
    nav: DATABASE_ITEM,
    title: null,
    twoPane: true,
  },
  {
    id: 'report-none',
    path: '/database?monitorFixture=ca-first-run&view=report',
    nav: DATABASE_ITEM,
    title: null,
    twoPane: true,
  },
  {
    id: 'analysis',
    path: `/results?monitorFixture=ca-database&target=demo&hash=ca0000000003&analysisId=ca_ca0000000003&query=${encodeURIComponent(
      'select id, title from posts where owneruserid = $1 order by creationdate desc limit $2'
    )}`,
    nav: null,
    title: null,
  },
  {
    id: 'ask',
    path: '/ask?monitorFixture=ca-database',
    nav: 'Ask',
    title: 'Ask',
  },
]

interface Audit {
  problems: string[]
  solids: number
  nav: string | null
  /** The label of the sidebar nav holding the current item. */
  navGroup: string | null
  words: number
}

/** The audit, evaluated in the page: every rule reports a line per violation. */
function audit(scenario: Scenario): Audit {
  const out: string[] = []
  const visible = (el: Element) => {
    const box = el.getBoundingClientRect()
    return (
      box.width > 0 &&
      box.height > 0 &&
      getComputedStyle(el).visibility !== 'hidden'
    )
  }
  const describe = (el: Element) =>
    el.tagName.toLowerCase() +
    (el.className && typeof el.className === 'string'
      ? `.${el.className.trim().split(/\s+/).slice(0, 3).join('.')}`
      : '')
  const main = document.querySelector('#main-content')
  const twoPane = document.querySelector('[data-two-pane]')

  // 1. The content axis: 1120px centered with 48px side padding, the bar's
  // hairline across the canvas with its content on the axis.
  if (main && !twoPane && !scenario.bare) {
    const mainBox = main.getBoundingClientRect()
    const contentWidth = Math.min(1120, mainBox.width - 96)
    const left = mainBox.left + 48 + (mainBox.width - 96 - contentWidth) / 2
    const right = left + contentWidth
    const outside: string[] = []
    for (const el of main.querySelectorAll('*')) {
      if (el.closest('[data-page-bar]') || el.closest('[role="dialog"]'))
        continue
      if (!visible(el)) continue
      const box = el.getBoundingClientRect()
      if (box.left < left - 1.5 || box.right > right + 1.5)
        outside.push(
          `${describe(el)} [${Math.round(box.left - left)},${Math.round(box.right - right)}]`
        )
    }
    if (outside.length)
      out.push(
        `outside 1120 container (${outside.length}): ${outside.slice(0, 4).join('; ')}`
      )
    const bar = main.querySelector(':scope > [data-page-bar]')
    if (!bar) out.push('no page bar')
    else {
      const barBox = bar.getBoundingClientRect()
      if (
        Math.abs(barBox.left - mainBox.left) > 1.5 ||
        Math.abs(barBox.right - mainBox.right) > 1.5
      )
        out.push(
          `bar hairline not full width [${Math.round(barBox.left - mainBox.left)},${Math.round(barBox.right - mainBox.right)}]`
        )
      const title = bar.querySelector('h1, p')
      if (title) {
        const titleBox = title.getBoundingClientRect()
        if (Math.abs(titleBox.left - left) > 1.5)
          out.push(
            `bar content off container axis by ${Math.round(titleBox.left - left)}`
          )
      }
      const tabs = bar.querySelector('[role="tablist"]')
      if (tabs) {
        const tabBox = tabs.getBoundingClientRect()
        if (Math.abs(tabBox.left - left) > 1.5)
          out.push(
            `tabs off container axis by ${Math.round(tabBox.left - left)}`
          )
      }
    }
  }

  // 2. One solid button per view: dialog, pane and page count separately.
  const solids = [
    ...document.querySelectorAll('button, a[href], [role="button"]'),
  ].filter(
    (el) =>
      visible(el) &&
      (el.className as string).split(/\s+/).includes('bg-surface-primary-solid')
  )
  const layers: Record<string, string[]> = {}
  for (const solid of solids) {
    const layer = solid.closest('[role="dialog"]')
      ? 'dialog'
      : solid.closest('section[aria-label]')?.closest('[data-two-pane]')
        ? 'pane'
        : 'page'
    layers[layer] = [...(layers[layer] ?? []), (solid.textContent ?? '').trim()]
  }
  for (const [layer, labels] of Object.entries(layers))
    if (labels.length > 1)
      out.push(
        `${labels.length} solid buttons in ${layer}: ${labels.join(' | ')}`
      )

  // 3. Purple only on sign-in.
  if (!scenario.bare) {
    const purple: string[] = []
    for (const el of document.querySelectorAll('body *')) {
      if (!visible(el)) continue
      const style = getComputedStyle(el)
      for (const property of [
        'color',
        'backgroundColor',
        'borderColor',
        'fill',
        'stroke',
      ] as const)
        if (style[property].replace(/\s/g, '') === PURPLE.replace(/\s/g, '')) {
          purple.push(`${describe(el)}:${property}`)
          break
        }
    }
    if (purple.length)
      out.push(
        `purple used (${purple.length}): ${purple.slice(0, 3).join('; ')}`
      )
  }

  // 4. Chips never wrap.
  const wrapped = [...document.querySelectorAll('.rounded-pill')].filter(
    (el) => visible(el) && el.getBoundingClientRect().height > 30
  )
  if (wrapped.length)
    out.push(
      `wrapped chips (${wrapped.length}): ${wrapped
        .slice(0, 3)
        .map((el) => (el.textContent ?? '').trim())
        .join(' | ')}`
    )

  // 5. The type scale.
  const sizes: Record<string, string[]> = {}
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || el.closest('svg')) continue
    const own = [...el.childNodes].some(
      (node) => node.nodeType === 3 && (node.textContent ?? '').trim()
    )
    if (!own) continue
    const size = Number.parseFloat(getComputedStyle(el).fontSize)
    if (!TYPE_SCALE.has(size))
      sizes[size] = [
        ...(sizes[size] ?? []),
        `${describe(el)} "${(el.textContent ?? '').trim().slice(0, 24)}"`,
      ]
  }
  for (const [size, els] of Object.entries(sizes))
    out.push(
      `off-scale ${size}px (${els.length}): ${els.slice(0, 2).join('; ')}`
    )

  // 6. Two-pane pages: no global scroll, no gap while the second panel is closed.
  if (twoPane) {
    const scroller = document.scrollingElement
    if (scroller && scroller.scrollHeight > innerHeight + 2)
      out.push(
        `two-panel page scrolls globally (${scroller.scrollHeight} > ${innerHeight})`
      )
    const second = twoPane.children[1]
    if (second && !twoPane.hasAttribute('data-open')) {
      const gap = getComputedStyle(second).marginLeft
      if (gap !== '0px') out.push(`closed pane reserves gap ${gap}`)
    }
  }

  // 7. The nav highlight.
  const current = document.querySelector('#app-sidebar a[aria-current="page"]')
  return {
    problems: out,
    solids: solids.length,
    nav: current
      ? (current.textContent ?? '').trim().replace(/\d+$/, '').trim()
      : null,
    navGroup: current?.closest('nav')?.getAttribute('aria-label') ?? null,
    words: ((main ?? document.body) as HTMLElement).innerText
      .split(/\s+/)
      .filter(Boolean).length,
  }
}

test.describe('Continuous Analysis redesign rules', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize(VIEWPORT)
    await configureTestTarget(page, { hasPassword: true })
  })

  for (const scenario of SCENARIOS) {
    test(`${scenario.id} keeps the redesign rules`, async ({ page }) => {
      await page.goto(scenario.path)
      // An open dialog hides the page from the accessibility tree, so it is
      // the dialog that proves the scenario loaded.
      if (scenario.dialog)
        await expect(
          page.getByRole('dialog', { name: scenario.dialog })
        ).toBeVisible()
      else if (scenario.title)
        await expect(
          page.getByRole('heading', { level: 1, name: scenario.title })
        ).toBeVisible()
      else await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
      await page.waitForTimeout(500)

      const result = await page.evaluate(audit, scenario)
      const problems = [...result.problems]
      const highlight =
        result.navGroup === DATABASE_ITEM ? DATABASE_ITEM : result.nav
      if (scenario.nav && highlight !== scenario.nav)
        problems.push(
          `nav highlight: ${result.nav ?? 'none'} (expected ${scenario.nav})`
        )
      if (!scenario.nav && result.nav)
        problems.push(`nav highlight: ${result.nav}`)
      if (scenario.bare) {
        if (await page.locator('#app-sidebar').count())
          problems.push('sign-in renders the shell')
        if (result.solids !== 1)
          problems.push(`sign-in has ${result.solids} solid buttons`)
      }
      expect(problems, `${scenario.id} (${result.words} words)`).toEqual([])
    })
  }

  test('the Inbox opens Changes ready on the plan and leads to Actions', async ({
    page,
  }) => {
    await page.goto('/inbox?monitorFixture=ca-inbox&item=2')
    await expect(
      page.getByRole('heading', { level: 2, name: '5 changes ready on demo' })
    ).toBeVisible()
    await expect(page.getByText('2 to do first', { exact: true })).toBeVisible()
    await expect(page.getByText('Top changes', { exact: true })).toBeVisible()
    await expect(
      page.getByText('Index posts (creationdate, owneruserid, score)', {
        exact: true,
      })
    ).toBeVisible()
    await expect(
      page.getByText('Opening Actions for demo closes this item.', {
        exact: true,
      })
    ).toBeVisible()
    const open = page.getByRole('link', { name: 'Open Actions' })
    await expect(open).toHaveAttribute('href', '/actions?db=demo')
    await open.click()
    await expect(page).toHaveURL(/\/actions\?(.*&)?db=demo/)
    await expect(
      page.getByRole('heading', { level: 1, name: 'Actions' })
    ).toBeVisible()
  })

  test('an Actions change opens in the side pane, walks the plan and closes on Escape', async ({
    page,
  }) => {
    await page.goto('/actions?monitorFixture=ca-database')
    const twoPane = page.locator('[data-two-pane]')
    await expect(twoPane).toBeVisible()
    await expect(twoPane).not.toHaveAttribute('data-open', 'true')
    const name = page
      .getByTestId('plan-row')
      .first()
      .getByRole('button', { name: /Add posts \(creationdate/ })
    await name.click()
    await expect(twoPane).toHaveAttribute('data-open', 'true')
    await expect(page).toHaveURL(/[?&]action=1(&|$)/)
    const pane = page.getByRole('region', {
      name: 'Add an index on posts (creationdate, owneruserid, score)',
    })
    await expect(pane.getByTestId('plan-detail')).toBeVisible()
    await expect(name).toHaveAttribute('aria-current', 'true')
    // The pane holds the change's actions: one solid on the page, I did this.
    await expect(
      page.locator('button.bg-surface-primary-solid:visible')
    ).toHaveText(['I did this'])
    await pane.getByRole('button', { name: 'Next' }).click()
    await expect(page).toHaveURL(/[?&]action=101(&|$)/)
    await page.keyboard.press('k')
    await expect(page).toHaveURL(/[?&]action=1(&|$)/)
    await page.keyboard.press('Escape')
    await expect(twoPane).not.toHaveAttribute('data-open', 'true')
    await expect(page).not.toHaveURL(/[?&]action=/)
    await expect(name).toBeFocused()
  })

  test('the Database pane opens from a row, scrolls on its own and closes on Escape', async ({
    page,
  }) => {
    await page.goto('/database?monitorFixture=ca-database&view=live')
    const twoPane = page.locator('[data-two-pane]')
    await expect(twoPane).toBeVisible()
    await expect(twoPane).not.toHaveAttribute('data-open', 'true')
    await page.getByTestId('query-row').first().click()
    await expect(twoPane).toHaveAttribute('data-open', 'true')
    await expect(
      page.getByRole('region', { name: /SELECT|COUNT/ })
    ).toBeVisible()
    await page.waitForTimeout(400)
    const scroll = await page.evaluate(() => {
      const pane = document.querySelector('[data-two-pane]')
      const panels = pane ? [...pane.children] : []
      // Each panel scrolls in its own design-system scroll area viewport.
      const scrollers = panels.map((panel) => {
        const viewport = panel.querySelector(
          '[data-radix-scroll-area-viewport]'
        )
        const style = viewport ? getComputedStyle(viewport) : null
        return style?.overflowY === 'auto' || style?.overflowY === 'scroll'
      })
      const scroller = document.scrollingElement
      return {
        pageScrolls: Boolean(
          scroller && scroller.scrollHeight > innerHeight + 2
        ),
        primaryScrolls: scrollers[0] ?? false,
        gap: panels[1] ? getComputedStyle(panels[1]).marginLeft : null,
        columns: document.querySelectorAll('[data-testid="query-row"] > *')
          .length,
      }
    })
    expect(scroll.pageScrolls).toBe(false)
    expect(scroll.primaryScrolls).toBe(true)
    expect(scroll.gap).toBe('12px')
    const solids = await page
      .locator('section[aria-label] button.bg-surface-primary-solid:visible')
      .count()
    expect(solids).toBeLessThanOrEqual(1)
    await page.keyboard.press('Escape')
    await expect(twoPane).not.toHaveAttribute('data-open', 'true')
    await page.waitForTimeout(400)
    expect(
      await page.evaluate(() => {
        const pane = document.querySelector('[data-two-pane]')
        return pane?.children[1]
          ? getComputedStyle(pane.children[1]).marginLeft
          : null
      })
    ).toBe('0px')
  })
})

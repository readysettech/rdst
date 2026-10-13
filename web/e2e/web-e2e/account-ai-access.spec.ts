import type { Page } from '@playwright/test'
import {
  acceptBrowserError,
  addDatabaseThroughDialog,
  clearTargets,
  expect,
  mainContent,
  test,
} from '../fixtures'
import { signInWithEmail } from './sign-in'

/** Live account enrollment, hosted inference, and sign-out coverage. */

// The add-database dialog names a database after its database name.
const TARGET = 'rdst_test'

async function resetLocalState(page: Page): Promise<void> {
  // Playwright retries reuse the RDST backend process. Reset server-side
  // account and target state so every attempt exercises the first-run gate
  // instead of inheriting a completed signup from the previous attempt.
  const logout = await page.request.post('/api/account/logout')
  expect(logout.ok()).toBe(true)
  await clearTargets(page.request)
}

/** Signed out with no key, Home is the AI choice inside the app shell. */
async function expectSignedOutAiAccess(page: Page): Promise<void> {
  await page.goto('/')
  const main = mainContent(page)
  await expect(
    main.getByText('Set up AI to get fixes and the daily report.')
  ).toBeVisible()
  await expect(
    main.getByRole('button', { name: 'Sign in', exact: true })
  ).toBeVisible()
  await expect(
    main.getByRole('button', { name: 'Add key', exact: true })
  ).toBeVisible()
  await expect(page.getByTestId('sidebar-sign-in')).toBeVisible()
  await expect(page.getByTestId('sidebar-account-email')).toHaveCount(0)
}

test('account login unlocks hosted Ask and sign-out restores the AI access choice', async ({
  browserErrors,
  page,
}) => {
  // This is the only browser suite that intentionally crosses several live
  // services. Chromium logs their handled 429 responses as generic resource
  // errors; every operation below still has an explicit functional assertion.
  acceptBrowserError(
    browserErrors,
    'Failed to load resource: the server responded with a status of 429 ()'
  )
  await resetLocalState(page)
  await expectSignedOutAiAccess(page)

  // The alternate BYOK path must remain usable without creating an account.
  await mainContent(page)
    .getByRole('button', { name: 'Add key', exact: true })
    .click()
  const keyDialog = page.getByRole('dialog')
  await expect(
    keyDialog.getByRole('heading', { name: 'Update Anthropic API key' })
  ).toBeVisible()
  await expect(keyDialog.getByLabel('Anthropic API key')).toBeVisible()
  await keyDialog.getByRole('button', { name: 'Cancel' }).click()

  // Supabase rate-limits magic links per address, so every attempt signs in
  // as a fresh account on the catch-all domain.
  const signedInPage = await signInWithEmail(
    page,
    `account-r${test.info().retry}`
  )

  await test.step('configure a live database', async () => {
    let bootstrapRequests = 0
    await signedInPage.route('**/api/bootstrap', async (route) => {
      const request = route.request()
      const body = request.postDataJSON() as { target?: string }
      bootstrapRequests += 1
      const response = await route.fetch({
        postData: JSON.stringify({ ...body, annotate: false }),
      })
      await route.fulfill({ response })
    })
    await addDatabaseThroughDialog(signedInPage, {
      host: process.env.RDST_E2E_DB_HOST!,
      port: process.env.RDST_E2E_DB_PORT!,
      database: TARGET,
      user: 'rdst_test',
      password: process.env.RDST_E2E_DB_PASSWORD!,
    })
    // The dialog turns monitoring on, and a monitored database assesses only the
    // queries monitoring picked. This spec assesses a query of its own, so
    // monitoring goes off here; Ask works either way.
    const monitoring = await signedInPage.request.put(
      `/api/monitor/targets/${TARGET}/settings`,
      { data: { enabled: false } }
    )
    expect(monitoring.ok()).toBe(true)
    await signedInPage.goto('/')

    // Ask is available as soon as the target is connected. Semantic-layer
    // discovery deliberately continues in the background, so waiting for it
    // here would turn an unrelated model job into a prerequisite for Ask.
    // A real database is tested, saved, and promoted before the dialog closes;
    // give that more than the fake backend's instant reply.
    await expect(signedInPage).toHaveURL('/', { timeout: 60_000 })
    const targets = await signedInPage.request.get('/api/configure/targets')
    expect(targets.ok()).toBe(true)
    await expect(targets.json()).resolves.toMatchObject({
      default_target: TARGET,
      targets: [{ name: TARGET, is_default: true }],
    })
    expect(bootstrapRequests).toBe(1)
  })

  await test.step('run Ask through Readyset-hosted inference', async () => {
    await signedInPage.goto('/ask')
    const box = signedInPage.getByTestId('ask-box')
    const ask = box.getByRole('button', { name: 'Ask', exact: true })
    await box
      .getByRole('textbox', { name: 'Question' })
      .fill('How many rows are in title_basics?')
    await ask.click()
    const answer = signedInPage.getByTestId('ask-answer')
    const failure = signedInPage.getByTestId('ask-failure')
    const upstreamBusy = failure.getByText(
      'The included AI is temporarily busy. Try again.'
    )
    let answered = false
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await expect(answer.or(failure).first()).toBeVisible({ timeout: 180_000 })
      if (await answer.isVisible()) {
        answered = true
        break
      }
      // This suite uses OpenRouter's shared DeepInfra pool. A sustained
      // engine_overloaded 429 is outside the product and preview Worker; keep
      // the login/session/sign-out contract covered while recording that the
      // external inference assertion could not run. Any other Ask failure is
      // still fatal.
      await expect(upstreamBusy).toBeVisible()
      if (attempt === 2) {
        test.info().annotations.push({
          type: 'external-provider',
          description:
            'DeepInfra shared pool remained overloaded after three bounded attempts',
        })
        break
      }
      // The box keeps the question after a failure; asking it again is the
      // same run as the failure's own retry.
      await ask.click()
    }
    if (!answered) return
    await expect(answer).toContainText('Answered from')
    expect(
      (await answer.getByTestId('ask-sql').innerText()).toLowerCase()
    ).toContain('title_basics')
  })

  await test.step('a question that needs clarification is asked, not failed', async () => {
    // The clarification detector asks the host for a strict schema and rejects
    // any drift from it. A host that ignores the contract fails every question
    // at this step while the keyservice records the call as a success, so this
    // has to run against a real host, and the failure text is asserted absent.
    await signedInPage.goto('/ask')
    const box = signedInPage.getByTestId('ask-box')
    await box
      .getByRole('textbox', { name: 'Question' })
      .fill('Show me the top titles')
    await box.getByRole('button', { name: 'Ask', exact: true }).click()
    const choice = signedInPage.getByTestId('ask-choice')
    const answer = signedInPage.getByTestId('ask-answer')
    const clarifyFailure = signedInPage.getByText(
      'Failed while clarifying the question'
    )
    await expect(choice.or(answer).or(clarifyFailure).first()).toBeVisible({
      timeout: 180_000,
    })
    await expect(clarifyFailure).not.toBeVisible()
    if (await choice.isVisible()) {
      // One question or several, one at a time with the first option chosen:
      // run with each until the last one asks for the answer.
      for (let step = 0; step < 6 && (await choice.isVisible()); step += 1) {
        const question = await choice.locator('p').first().innerText()
        await choice.getByRole('button', { name: /^Run with / }).click()
        await expect(choice.getByText(question, { exact: true })).toHaveCount(
          0,
          { timeout: 60_000 }
        )
      }
      await expect(answer.or(clarifyFailure).first()).toBeVisible({
        timeout: 180_000,
      })
      await expect(clarifyFailure).not.toBeVisible()
    }
  })

  await test.step('run a persisted Jev assessment through the preview Keyservice', async () => {
    const sql =
      'SELECT primarytitle FROM title_basics WHERE startyear > 2000 ORDER BY primarytitle'
    const added = await signedInPage.request.post('/api/query-registry', {
      data: { sql, target: TARGET },
    })
    expect(added.ok()).toBe(true)
    const hash = ((await added.json()) as { hash: string }).hash
    // The live target carries background traffic, so every assertion is
    // pinned to this one shape: search matches a hash prefix.
    const query = `/api/query-registry?target=${TARGET}&search=${hash}&view=all&source=all&params=all&activity=all&impact=all&sort=jev-priority&limit=10`
    await expect(async () => {
      const registry = await signedInPage.request.get(query)
      expect(registry.ok()).toBe(true)
      const body = (await registry.json()) as {
        queries: { jev_assessment?: { status: string; model: string } }[]
      }
      expect(body.queries[0]?.jev_assessment).toMatchObject({
        status: 'complete',
        model: 'jev-1.13.0',
      })
    }).toPass({ timeout: 120_000 })
  })

  await test.step('sign out and require an AI access choice again', async () => {
    // `section=ai` is the recovery deep link and opens the Anthropic-key
    // dialog. The settings panel itself is selected with `panel=ai`.
    await signedInPage.goto('/configure?panel=ai')
    const account = signedInPage.getByRole('region', { name: 'Account' })
    await account.getByRole('button', { name: 'Sign out' }).click()
    await expect(account.getByText('Not signed in')).toBeVisible()
    await expectSignedOutAiAccess(signedInPage)
    const status = await signedInPage.request.get('/api/account/status')
    expect(status.ok()).toBe(true)
    expect((await status.json()).signed_in).toBe(false)
  })
})

import type { Page } from '@playwright/test'
import { acceptBrowserError, clearTargets, expect, test } from '../fixtures'
import { ciAddress, waitForEmail } from './mailbox'

/** Live account enrollment, hosted inference, and sign-out coverage. */

const KEYSERVICE = process.env.RDST_KEYSERVICE_URL!.replace(/\/$/, '')
const TARGET = 'keyservice-e2e'

const previewWorker =
  /^https:\/\/rdst-keyservice-pr-[a-zA-Z0-9_-]+\.readysetio\.workers\.dev$/.test(
    KEYSERVICE
  )
const allowedLocalWorker =
  process.env.RDST_ALLOW_LOCAL_KEYSERVICE_E2E === '1' &&
  /^http:\/\/(127\.0\.0\.1|localhost):8788$/.test(KEYSERVICE)

if (!previewWorker && !allowedLocalWorker) {
  throw new Error(
    'Refusing to run against a shared keyservice. Use a per-CL preview Worker, ' +
      'or explicitly opt into the loopback-only local Worker with ' +
      `RDST_ALLOW_LOCAL_KEYSERVICE_E2E=1; got ${KEYSERVICE}`
  )
}

function supabaseMagicLink(html: string): string {
  const links = [...html.matchAll(/href=["']([^"']+)["']/gi)].map((match) =>
    match[1].replaceAll('&amp;', '&')
  )
  const link = links.find(
    (value) =>
      value.includes('/auth/v1/verify') || value.includes('/verify?token=')
  )
  if (!link)
    throw new Error('The Supabase email contained no magic sign-in link')
  return link
}

async function resetLocalState(page: Page): Promise<void> {
  // Playwright retries reuse the RDST backend process. Reset server-side
  // account and target state so every attempt exercises the first-run gate
  // instead of inheriting a completed signup from the previous attempt.
  const logout = await page.request.post('/api/account/logout')
  expect(logout.ok()).toBe(true)
  await clearTargets(page.request)
}

/** The AI access card on Settings, in its signed-out state. */
async function expectSignedOutAiAccess(page: Page): Promise<void> {
  await page.goto('/configure?panel=ai')
  await expect(page.getByText('AI access', { exact: true })).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Use the included AI' })
  ).toBeVisible()
  await expect(page.getByText(/Signed in as/)).toHaveCount(0)
}

async function signInWithEmail(page: Page, emailLabel: string): Promise<Page> {
  const email = ciAddress(emailLabel)
  const requestedAt = new Date()

  await page.goto('/configure?panel=ai')
  await page.getByRole('button', { name: 'Use the included AI' }).click()
  const dialog = page.getByRole('dialog')
  await expect(
    dialog.getByRole('heading', { name: 'Sign in to Readyset' })
  ).toBeVisible()
  await expect(
    dialog.getByRole('button', { name: 'Continue with Google' })
  ).toBeVisible()
  await expect(
    dialog.getByRole('button', { name: 'Continue with GitHub' })
  ).toBeVisible()
  await dialog.getByLabel('Email address').fill(email)
  await dialog.getByRole('button', { name: 'Send sign-in link' }).click()
  await expect(dialog.getByText('Check your email')).toBeVisible()

  const mail = await waitForEmail(email, { newerThan: requestedAt })
  const signedInPage = await page.context().newPage()
  await signedInPage.goto(supabaseMagicLink(mail.html ?? ''))
  await expect(async () => {
    const response = await signedInPage.request.get('/api/account/status')
    expect(response.ok()).toBe(true)
    expect((await response.json()).signed_in).toBe(true)
  }).toPass({ timeout: 60_000 })

  // The card shows the account once the session exists. If the provider
  // preference has not followed the sign-in yet, choose the included AI.
  await signedInPage.goto('/configure?panel=ai')
  await expect(signedInPage.getByText(`Signed in as ${email}`)).toBeVisible()
  const useIncluded = signedInPage.getByRole('button', {
    name: 'Use the included AI',
  })
  if (await useIncluded.isVisible()) await useIncluded.click()
  return signedInPage
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
  await page.getByRole('button', { name: 'Set key' }).click()
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
    await signedInPage.goto('/onboarding')
    await signedInPage.locator('[name="name"]').fill(TARGET)
    await signedInPage
      .locator('[name="host"]')
      .fill(process.env.RDST_E2E_DB_HOST!)
    await signedInPage
      .locator('[name="port"]')
      .fill(process.env.RDST_E2E_DB_PORT!)
    await signedInPage.locator('[name="database"]').fill('rdst_test')
    await signedInPage.locator('[name="user"]').fill('rdst_test')
    await signedInPage
      .locator('[name="password"]')
      .fill(process.env.RDST_E2E_DB_PASSWORD!)
    await signedInPage.getByRole('button', { name: 'Test & connect' }).click()
    // The e2e role has write access, so RDST asks before adding it.
    const writableAccount = signedInPage.getByRole('dialog', {
      name: 'Use this database account?',
    })
    await expect(writableAccount).toBeVisible({ timeout: 60_000 })
    await writableAccount.getByRole('button', { name: 'Add target' }).click()

    // Ask is available as soon as the target is connected. Semantic-layer
    // discovery deliberately continues in the background, so waiting for it
    // here would turn an unrelated model job into a prerequisite for Ask.
    // A real database is tested, saved, and promoted before the app leaves
    // onboarding; give that more than the fake backend's instant reply.
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
    await signedInPage
      .getByPlaceholder(
        'For example: Which customers placed the most orders this month?'
      )
      .fill('How many rows are in title_basics?')
    await signedInPage.getByRole('button', { name: 'Get answer' }).click()
    const answer = signedInPage.getByText('Answer', { exact: true })
    const retry = signedInPage.getByRole('button', { name: 'Try again' })
    const upstreamBusy = signedInPage.getByText(
      'The included AI is temporarily busy. Try again.'
    )
    let answered = false
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await expect(answer.or(retry).first()).toBeVisible({ timeout: 180_000 })
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
      await retry.click()
    }
    if (!answered) return
    await expect(answer).toBeVisible({ timeout: 180_000 })
    await signedInPage
      .getByRole('button', { name: /Show the SQL that ran/ })
      .click()
    expect(
      (await signedInPage.locator('.cm-content').innerText()).toLowerCase()
    ).toContain('title_basics')
  })

  await test.step('a question that needs clarification is asked, not failed', async () => {
    // The clarification detector asks the host for a strict schema and rejects
    // any drift from it. A host that ignores the contract fails every question
    // at this step while the keyservice records the call as a success, so this
    // has to run against a real host, and the failure text is asserted absent.
    await signedInPage.goto('/ask')
    await signedInPage
      .getByPlaceholder(
        'For example: Which customers placed the most orders this month?'
      )
      .fill('Show me the top titles')
    await signedInPage.getByRole('button', { name: 'Get answer' }).click()
    const clarification = signedInPage.getByText(
      /One quick question|A few quick questions/
    )
    const answer = signedInPage.getByText('Answer', { exact: true })
    const clarifyFailure = signedInPage.getByText(
      'Failed while clarifying the question'
    )
    await expect(
      clarification.or(answer).or(clarifyFailure).first()
    ).toBeVisible({ timeout: 180_000 })
    await expect(clarifyFailure).not.toBeVisible()
    if (await clarification.isVisible()) {
      await expect(
        signedInPage.getByText('You asked: Show me the top titles')
      ).toBeVisible()
      // One question or several: answer each with its first option, then
      // ask for the answer from the last one.
      for (let step = 0; step < 6; step += 1) {
        await signedInPage.getByRole('radio').first().check()
        const next = signedInPage.getByRole('button', { name: 'Next' })
        if (await next.isVisible()) {
          await next.click()
          continue
        }
        await signedInPage.getByRole('button', { name: 'Get answer' }).click()
        break
      }
      await expect(answer.or(clarifyFailure).first()).toBeVisible({
        timeout: 180_000,
      })
      await expect(clarifyFailure).not.toBeVisible()
    }
  })

  await test.step('sign out and require an AI access choice again', async () => {
    // `section=ai` is the recovery deep link and opens the Anthropic-key
    // dialog. The settings panel itself is selected with `panel=ai`.
    await signedInPage.goto('/configure?panel=ai')
    await signedInPage.getByRole('button', { name: 'Sign out' }).click()
    await expectSignedOutAiAccess(signedInPage)
    const status = await signedInPage.request.get('/api/account/status')
    expect(status.ok()).toBe(true)
    expect((await status.json()).signed_in).toBe(false)
  })
})

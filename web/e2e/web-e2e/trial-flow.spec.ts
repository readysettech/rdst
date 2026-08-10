import type { Page } from '@playwright/test'
import { clearTargets, expect, test } from '../fixtures'
import {
  ciAddress,
  linkFrom,
  reportPasswordFrom,
  waitForEmail,
} from './mailbox'

/**
 * Cold signup through to a trial credit spent by a real question.
 *
 * Nothing here is faked or shortcut. A live Postgres container is connected
 * through the onboarding form, a trial account is registered from scratch
 * against this CL's preview Worker, the verification link comes out of an email
 * that genuinely arrived, and the credit is spent by asking a question in the
 * app - which reaches Anthropic through the Worker's proxy on the trial token.
 * A health check then produces a report, which is emailed, unlocked with the
 * password from that email, and opened.
 *
 * One test rather than several: the flow is a single sequence, and splitting it
 * would leave later steps depending on state an earlier failure destroyed.
 */

const KEYSERVICE = process.env.RDST_KEYSERVICE_URL!.replace(/\/$/, '')
const ADMIN_SECRET = process.env.RDST_KEYSERVICE_ADMIN_SECRET!
const TARGET = 'keyservice-e2e'

// This suite registers accounts, spends credit and calls admin routes that read
// the whole account table. Only ever point it at an ephemeral per-CL Worker.
// `keyservice_base_url()` falls back to production whenever
// RDST_KEYSERVICE_URL is unset, so refusing anything else here is what stops a
// missing variable from turning into signups and admin reads against real
// users. The CI runner enforces the same rule; this is the copy that travels
// with the test.
const PREVIEW_URL =
  /^https:\/\/rdst-keyservice-pr-[a-zA-Z0-9_-]+\.readysetio\.workers\.dev$/
if (!PREVIEW_URL.test(KEYSERVICE)) {
  throw new Error(
    `Refusing to run: RDST_KEYSERVICE_URL must be a per-CL preview Worker, ` +
      `got ${KEYSERVICE}`
  )
}

// One address per CL per build, so concurrent builds never contend and the
// account has never existed before.
const EMAIL = ciAddress('trial')

async function adminFetch(path: string, init: RequestInit = {}) {
  return fetch(`${KEYSERVICE}${path}`, {
    ...init,
    headers: {
      ...(init.headers ?? {}),
      Authorization: `Bearer ${ADMIN_SECRET}`,
      'Content-Type': 'application/json',
    },
  })
}

/** Idempotent, so it serves as both the pre-run sweep and the cleanup. */
async function deleteTrialUser() {
  const response = await adminFetch('/admin/users', {
    method: 'DELETE',
    body: JSON.stringify({ email: EMAIL }),
  })
  expect(
    response.ok,
    `admin delete failed: ${response.status} ${await response.text()}`
  ).toBe(true)
}

/**
 * Credit left on the trial, as the running app understands it.
 *
 * This is the number behind the balance in the sidebar. The proxy returns the
 * post-charge balance with every completion and the client persists it, so
 * reading it here asks the same question a user answers by glancing at the
 * badge - just in cents rather than rounded token counts, which a single cheap
 * completion would not visibly move.
 */
async function remainingCents(page: Page): Promise<number> {
  const response = await page.request.get('/api/trial/status')
  expect(response.ok(), `GET /api/trial/status failed: ${response.status()}`).toBe(true)
  const status = (await response.json()) as {
    active: boolean
    remaining_cents: number | null
  }
  expect(status.active, 'the app does not consider the trial active').toBe(true)
  expect(status.remaining_cents, 'no remaining_cents in trial status').not.toBeNull()
  return status.remaining_cents!
}

test.afterAll(async () => {
  await deleteTrialUser()
})

test('a trial pays for a real question and mails an openable report', async ({
  page,
}) => {
  // In the test body rather than beforeAll: a retry runs in a fresh worker, and
  // the account has to be gone again for the retry to be a first signup too.
  await deleteTrialUser()
  const registeredAt = new Date()

  await test.step('connect the live Postgres container', async () => {
    await clearTargets(page.request)
    await page.goto('/onboarding')
    await expect(
      page.getByRole('heading', { name: 'Connect your database' })
    ).toBeVisible()
    await page.locator('[name="name"]').fill(TARGET)
    await page.locator('[name="host"]').fill(process.env.RDST_E2E_DB_HOST!)
    await page.locator('[name="port"]').fill(process.env.RDST_E2E_DB_PORT!)
    await page.locator('[name="database"]').fill('rdst_test')
    await page.locator('[name="user"]').fill('rdst_test')
    await page
      .locator('[name="password"]')
      .fill(process.env.RDST_E2E_DB_PASSWORD!)
    await page.getByRole('button', { name: 'Test & connect' }).click()
    await expect(page).toHaveURL('/')

    // Connecting starts the background bootstrap. `ask` needs the semantic
    // layer, so wait for it server-side rather than racing the UI.
    await expect(async () => {
      const status = await page.request.get(
        `/api/semantic-layer/status?target=${TARGET}`
      )
      expect(status.ok()).toBe(true)
      expect((await status.json()).exists).toBe(true)
    }).toPass({ timeout: 60_000 })
  })

  const token = await test.step('sign up and collect the emailed token', async () => {
    await page.getByRole('button', { name: 'Get free AI credits' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()

    await dialog.locator('input[name="trial-email"]').fill(EMAIL)
    await dialog.getByRole('button', { name: 'Start Free Trial' }).click()

    // The dialog advances to the token step only once /register returned 2xx.
    const tokenField = dialog.locator('input[name="trial-token"]')
    await expect(tokenField).toBeVisible()

    // Requiring the preview's own origin proves the mail came from this
    // build's Worker rather than a concurrent CL's.
    const message = await waitForEmail(EMAIL, {
      contains: KEYSERVICE,
      newerThan: registeredAt,
    })
    const verifyUrl = linkFrom(message, `${KEYSERVICE}/verify`)

    // A second tab, like a real user clicking through from their mail client:
    // the dialog keeps its state.
    const verifyPage = await page.context().newPage()
    await verifyPage.goto(verifyUrl)
    await expect(verifyPage.getByText('Email Verified!')).toBeVisible()

    const value = (await verifyPage.locator('#token-box').innerText())
      .replace('Copy', '')
      .trim()
    expect(value, 'no trial token rendered on the verify page').toMatch(
      /^[0-9a-f-]{36}$/
    )
    await verifyPage.close()

    await tokenField.fill(value)
    await dialog.getByRole('button', { name: 'Activate' }).click()

    // Activation closes the dialog from the sidebar rather than showing a
    // success step, so the balance appearing is what proves the trial took.
    // Anchored so a stray number elsewhere cannot satisfy it: the badge's own
    // text is exactly "<remaining>/<limit>", e.g. 925K/925K.
    await expect(dialog).toBeHidden()
    await expect(page.getByText(/^[\d.]+[KMB]?\/[\d.]+[KMB]?$/)).toBeVisible()
    return value
  })

  expect(token).toBeTruthy()
  const balanceBefore = await remainingCents(page)
  expect(balanceBefore, 'a fresh trial should start with credit').toBeGreaterThan(0)

  await test.step('ask a real question and get a real answer', async () => {
    await page.goto('/ask')
    await expect(page.getByRole('heading', { name: 'Ask' })).toBeVisible()
    await page
      .getByPlaceholder('Ask a question about your data...')
      .fill('How many rows are in title_basics?')
    // Exact, because the accessible-name match is a substring by default and
    // the history cards rendered beside the input also contain "Ask" once a
    // previous run has left questions on disk. Without this the step passes on
    // a clean server and fails on a retry.
    await page.getByRole('button', { name: 'Ask', exact: true }).click()

    // A live model call behind a schema load and a validation pass, so this is
    // by far the slowest assertion in the suite.
    await expect(page.getByText('Answer', { exact: true })).toBeVisible({
      timeout: 180_000,
    })

    // "Answer" alone would also render for a degraded result, so pin down that
    // this is a real one: it is stamped with our target, a result set came back
    // from Postgres, and the SQL names a table the model could only know from
    // the semantic layer built a few steps ago.
    await expect(page.getByText(new RegExp(`Answered from ${TARGET}`))).toBeVisible()
    await expect(page.getByText(/^\d+ rows?$/)).toBeVisible()

    await page.getByRole('button', { name: /Show the SQL that ran/ }).click()
    const sql = (await page.locator('.cm-content').innerText()).trim()
    expect(
      sql.toLowerCase(),
      `generated SQL never mentions title_basics:\n${sql}`
    ).toContain('title_basics')

    // Put the model's actual output in the build log. When this suite is the
    // thing standing between a broken keyservice and a release, being able to
    // read what came back matters more than a green tick.
    console.log(`\n--- SQL generated by the model ---\n${sql}\n`)
  })

  // The point of the whole suite: the question reached Anthropic on the trial
  // token, and the charge came back. A trial that never decrements never ends.
  await expect
    .poll(() => remainingCents(page), { timeout: 60_000, intervals: [3_000] })
    .toBeLessThan(balanceBefore)

  console.log(
    `\n--- trial balance: ${balanceBefore}c before the question, ` +
      `${await remainingCents(page)}c after ---\n`
  )

  await test.step('run a health check and open the emailed report', async () => {
    const requestedAt = new Date()

    // Everything this target has audited before. The registry id a start
    // returns is not the snapshot id the report is filed under, so the new
    // report is identified by being one that did not exist a moment ago.
    // Matching on "has an analysis" alone would find a stale run from an
    // earlier audit, and the failure then looks like a broken button.
    const runIdsBefore = new Set(
      (
        (await (await page.request.get('/api/audit/runs')).json()) as {
          runs: { run_id: string }[]
        }
      ).runs.map((run) => run.run_id)
    )

    // A metrics-only health check with insights. That is the only path which
    // runs the health LLM, and a report without a health analysis cannot be
    // emailed - the button on the run page stays disabled. The Health Check
    // screen posts to /api/audit/capture instead, which never produces one,
    // so driving this from the screen would test a permanently dead button.
    const started = await page.request.post('/api/audit', {
      data: { target: TARGET, insights: true },
    })
    expect(
      started.ok(),
      `starting the health check failed: ${started.status()}`
    ).toBe(true)
    let runId = ''
    await expect(async () => {
      const response = await page.request.get('/api/audit/runs')
      expect(response.ok()).toBe(true)
      const { runs } = (await response.json()) as {
        runs: { run_id: string; target_name: string; has_analysis: boolean }[]
      }
      const fresh = runs.find(
        (run) =>
          run.target_name === TARGET &&
          run.has_analysis &&
          !runIdsBefore.has(run.run_id)
      )
      expect(
        fresh,
        'the health check has not filed an analysed report yet'
      ).toBeDefined()
      runId = fresh!.run_id
    }).toPass({ timeout: 240_000 })

    // The email action lives on the run's own page, not the launcher.
    await page.goto(`/audit/runs/${runId}`)
    const emailButton = page.getByRole('button', {
      name: 'Email me this report',
    })
    await expect(emailButton).toBeEnabled()
    await emailButton.click()

    // Report delivery keeps its own confirmation, separate from the trial's:
    // a trial-verified address still has no report token, so a first-time
    // address is asked to confirm once. Handle both openings - already
    // confirmed, or confirming now - so the suite does not depend on which
    // side of that line the environment happens to be on.
    const dialog = page.getByRole('dialog')
    const addressField = dialog.locator('input[name="report-email"]')
    const confirmation = dialog.getByText('Sending to')
    await expect(addressField.or(confirmation).first()).toBeVisible()

    if (await addressField.isVisible()) {
      await addressField.fill(EMAIL)
    } else {
      await expect(dialog.getByText(EMAIL, { exact: true })).toBeVisible()
    }
    await dialog.getByRole('button', { name: 'Send report' }).click()

    // A first-time address gets a one-time confirmation link, and the report
    // only follows once it is clicked. Same inbox, same discrimination on the
    // preview's own origin.
    if (await dialog.getByText(/Confirmation email sent to/).isVisible()) {
      const confirmMail = await waitForEmail(EMAIL, {
        contains: `${KEYSERVICE}/verify-report`,
        newerThan: requestedAt,
      })
      const confirmUrl = linkFrom(confirmMail, `${KEYSERVICE}/verify-report`)
      const confirmPage = await page.context().newPage()
      await confirmPage.goto(confirmUrl)
      await confirmPage.close()
    }

    await expect(dialog.getByText(/Report sent to/)).toBeVisible({
      timeout: 120_000,
    })
    await expect(dialog.getByText('Report on its way')).toBeVisible({
      timeout: 60_000,
    })

    // Same inbox as the trial token, discriminated by the preview's own origin.
    const message = await waitForEmail(EMAIL, {
      contains: `${KEYSERVICE}/report/`,
      newerThan: requestedAt,
    })
    const reportUrl = linkFrom(message, `${KEYSERVICE}/report/`)
    const password = reportPasswordFrom(message)

    // The report is password-gated, so a wrong password must not open it -
    // otherwise the gate is decoration and the link alone leaks the report.
    const reportPage = await page.context().newPage()
    await reportPage.goto(reportUrl)
    await expect(reportPage.getByRole('heading', { name: 'Report Access' })).toBeVisible()
    await reportPage.locator('#pw').fill('00000000')
    await reportPage.getByRole('button', { name: 'View Report' }).click()
    await expect(reportPage.getByText(/Incorrect password/)).toBeVisible()

    // Then the real one opens it.
    await reportPage.locator('#pw').fill(password)
    await reportPage.getByRole('button', { name: 'View Report' }).click()
    await expect(
      reportPage.getByRole('heading', { name: 'Report Access' })
    ).toHaveCount(0)

    // Past the gate is not the same as a report: an empty or error document
    // would also clear it. Check there is a real one behind the password -
    // named for the database it examined, and long enough to be findings
    // rather than a shell.
    const body = (await reportPage.locator('body').innerText()).trim()
    expect(body, 'the unlocked report does not name the target').toContain(TARGET)
    expect(
      body.length,
      `the unlocked report is only ${body.length} characters, which is a ` +
        `shell rather than a report`
    ).toBeGreaterThan(500)

    console.log(
      `\n--- hosted report opened, ${body.length} characters of content ---\n`
    )
    await reportPage.close()
  })
})

import type { Page } from '@playwright/test'
import { expect } from '../fixtures'
import { ciAddress, waitForEmail } from './mailbox'

const keyservice = process.env.RDST_KEYSERVICE_URL?.replace(/\/$/, '') ?? ''
const previewWorker =
  /^https:\/\/keyservice-pr-[a-zA-Z0-9-]+\.readyset\.io$/.test(keyservice)
const localWorker =
  process.env.RDST_ALLOW_LOCAL_KEYSERVICE_E2E === '1' &&
  /^http:\/\/(127\.0\.0\.1|localhost):8788$/.test(keyservice)
const personalMatch = keyservice.match(
  /^https:\/\/(?:rdst-keyservice-([a-zA-Z0-9_-]+)\.readysetio\.workers\.dev|keyservice-([a-zA-Z0-9_-]+)\.readyset\.io)$/
)
const personalName = personalMatch?.[1] ?? personalMatch?.[2]
const personalWorker =
  process.env.RDST_ALLOW_PERSONAL_KEYSERVICE_E2E === '1' &&
  Boolean(personalName) &&
  !personalName?.startsWith('pr-') &&
  !/(?:^|[-_])(?:prod|production|staging)/.test(personalName ?? '')

if (!previewWorker && !localWorker && !personalWorker) {
  throw new Error(
    'Refusing to run against a shared keyservice. Use a per-CL preview Worker, ' +
      'RDST_ALLOW_LOCAL_KEYSERVICE_E2E=1 for loopback, or ' +
      `RDST_ALLOW_PERSONAL_KEYSERVICE_E2E=1 for a personal Worker; got ${keyservice}`
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

export async function signInWithEmail(
  page: Page,
  emailLabel: string
): Promise<Page> {
  const email = ciAddress(emailLabel)

  // A preview Worker deployed moments ago can answer 404 until its route is live, and the
  // sign-in page would show that as a browser error.
  await expect
    .poll(
      async () =>
        (
          await page.request.get(`${keyservice}/health`).catch(() => null)
        )?.status(),
      {
        timeout: 120_000,
        intervals: [2_000],
        // A job retried after its build finished finds the preview already torn down:
        // only a new build deploys one.
        message: `${keyservice} isn't answering. On a retry after the build finished, its preview Worker was torn down: rebuild the whole build instead of retrying this job.`,
      }
    )
    .toBe(200)
  const requestedAt = new Date()

  // Signed out, the app runs up to turning on monitoring, and the sidebar's account
  // line leads to the sign-in page.
  await page.goto('/')
  await page.getByTestId('sidebar-sign-in').click()
  await expect(page).toHaveURL(/\/account-login/)
  await expect(
    page.getByRole('button', { name: 'Continue with Google' })
  ).toBeVisible()
  await page.getByLabel('Email address').fill(email)
  await page.getByRole('button', { name: 'Send sign-in link' }).click()
  await expect(page.getByText('Check your email')).toBeVisible()

  // The emailed link lands on /account-login, which finishes the sign-in.
  const mail = await waitForEmail(email, { newerThan: requestedAt })
  const signedInPage = await page.context().newPage()
  await signedInPage.goto(supabaseMagicLink(mail.html ?? ''))
  await expect(async () => {
    const response = await signedInPage.request.get('/api/account/status')
    expect(response.ok()).toBe(true)
    expect((await response.json()).signed_in).toBe(true)
  }).toPass({ timeout: 60_000 })
  // The sign-in page leaves on its own once it has refreshed the account; a page opened
  // before that would be replaced by where it goes.
  await expect(signedInPage).not.toHaveURL(/\/account-login/, {
    timeout: 30_000,
  })

  // Make the included AI the active provider, as choosing it in Settings does.
  const provider = await signedInPage.request.post('/api/env/ai-provider', {
    data: { provider: 'readyset' },
  })
  expect(provider.ok()).toBe(true)

  await signedInPage.goto('/configure?panel=ai')
  const account = signedInPage.getByRole('region', { name: 'Account' })
  await expect(account.getByText(email, { exact: true })).toBeVisible()
  await expect(account.getByText('Signed in', { exact: true })).toBeVisible()
  await expect(
    signedInPage.getByRole('switch', { name: 'Hosted by Readyset' })
  ).toBeChecked()
  await expect(signedInPage.getByTestId('sidebar-account-email')).toHaveText(
    email
  )
  return signedInPage
}

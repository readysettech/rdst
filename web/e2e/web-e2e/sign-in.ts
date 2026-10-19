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
  const requestedAt = new Date()

  // Sign-in is optional, so signed out the app runs as usual and the sidebar's
  // account line leads to the sign-in page.
  await page.goto('/')
  await page.getByTestId('sidebar-sign-in').click()
  await expect(page).toHaveURL(/\/account-login/)
  await expect(
    page.getByRole('button', { name: 'Continue with Google' })
  ).toBeVisible()
  await page.getByLabel('Email address').fill(email)
  await page.getByRole('button', { name: 'Send sign-in link' }).click()

  // A new address can upgrade the anonymous session on the spot; otherwise the
  // page asks for the emailed link.
  const linkSent = page.getByText('Check your email')
  let emailed = false
  await expect(async () => {
    emailed = await linkSent.isVisible()
    if (emailed) return
    const response = await page.request.get('/api/account/status')
    expect(response.ok()).toBe(true)
    expect((await response.json()).signed_in).toBe(true)
  }).toPass({ timeout: 30_000 })

  const signedInPage = await page.context().newPage()
  if (emailed) {
    // The emailed link lands on /account-login, which finishes the sign-in.
    const mail = await waitForEmail(email, { newerThan: requestedAt })
    await signedInPage.goto(supabaseMagicLink(mail.html ?? ''))
  }
  await expect(async () => {
    const response = await signedInPage.request.get('/api/account/status')
    expect(response.ok()).toBe(true)
    expect((await response.json()).signed_in).toBe(true)
  }).toPass({ timeout: 60_000 })

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

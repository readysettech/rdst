/**
 * What the tester's commands may reach beyond the app. The tester reads text
 * the app, its emails and its databases show, so these hold whatever it is
 * talked into.
 */

/** Hosts `rt goto` opens besides the app itself; the run's keyservice joins them. */
export const GOTO_HOSTS = ['docs.readyset.io']

/** The app's own pages, its docs, and the run's keyservice. */
export function opensFromApp(
  url: string,
  appBase: string | undefined,
  keyserviceUrl: string | undefined
) {
  const target = new URL(url)
  if (appBase) {
    const app = new URL(appBase)
    if (target.protocol === app.protocol && target.host === app.host) {
      return true
    }
  }
  const keyservice = keyserviceUrl ? new URL(keyserviceUrl).host : null
  return (
    target.protocol === 'https:' &&
    (GOTO_HOSTS.includes(target.host) || target.host === keyservice)
  )
}

/**
 * A site this run's emails linked to, such as the sign-in provider behind a
 * magic link. A person can open those again from their browser.
 */
export function linkedFromMail(url: string, mailLinks: Iterable<string>) {
  const target = new URL(url)
  if (target.protocol !== 'https:') return false
  for (const link of mailLinks) {
    if (URL.canParse(link) && new URL(link).origin === target.origin) {
      return true
    }
  }
  return false
}

/**
 * A deep link is an argument to the app's executable, which reads anything
 * else, such as a leading --, as a Chromium switch.
 */
export function isDeepLink(url: string) {
  return /^rdst:\/\//.test(url)
}

/** psql runs a -c string that begins with a backslash as its own command. */
export function isPsqlCommand(sql: string) {
  return sql.trimStart().startsWith('\\')
}

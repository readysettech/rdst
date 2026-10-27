import { compiler, type MarkdownToJSX } from 'markdown-to-jsx/react'
import type { ComponentProps } from 'react'
import './markdown.css'

export const proseClassName = 'rs-markdown'

type MarkdownProps = {
  children: string
  options?: MarkdownToJSX.Options
  /**
   * For text a model or a database wrote: no raw HTML, links only to the web, mail, or this
   * app, and images only from this app or inline data, so showing it loads nothing remote.
   */
  untrusted?: boolean
}

const LINK_PROTOCOLS = new Set(['http:', 'https:', 'mailto:'])

function resolveUrl(url: unknown): URL | null {
  if (typeof url !== 'string' || typeof window === 'undefined') return null
  try {
    return new URL(url, window.location.href)
  } catch {
    return null
  }
}

const isThisApp = (url: URL) =>
  url.protocol === window.location.protocol && url.host === window.location.host

const UntrustedLink = ({ href, children, ...props }: ComponentProps<'a'>) => {
  const url = resolveUrl(href)
  if (!url || !(LINK_PROTOCOLS.has(url.protocol) || isThisApp(url)))
    return <span>{children}</span>
  return (
    <a {...props} href={url.href} rel="noopener noreferrer">
      {children}
    </a>
  )
}

const UntrustedImage = ({ src, alt, title }: ComponentProps<'img'>) => {
  const url = resolveUrl(src)
  const allowed =
    url &&
    (url.protocol === 'data:'
      ? url.pathname.startsWith('image/')
      : isThisApp(url))
  if (allowed) return <img src={url.href} alt={alt} title={title} />
  return alt ? <span>{alt}</span> : null
}

const untrustedOptions = (
  options: MarkdownToJSX.Options = {}
): MarkdownToJSX.Options => ({
  ...options,
  disableParsingRawHTML: true,
  overrides: { ...options.overrides, a: UntrustedLink, img: UntrustedImage },
})

export const Markdown = ({ children, options, untrusted }: MarkdownProps) => {
  return (
    <div className={proseClassName}>
      {compiler(children, untrusted ? untrustedOptions(options) : options)}
    </div>
  )
}

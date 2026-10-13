import { useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'

type GoPath = '/' | '/inbox' | '/actions' | '/database' | '/help'

/** The second key of a `g` chord and where it goes. */
export const GO_TARGETS: Record<string, GoPath> = {
  h: '/',
  i: '/inbox',
  a: '/actions',
  d: '/database',
}

/** How long `g` waits for its second key. */
export const CHORD_WINDOW_MS = 1000

export function inEditable(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false
  const element = target as HTMLElement
  const tag = element.tagName
  return (
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    element.isContentEditable ||
    Boolean(element.closest('[contenteditable="true"], .cm-editor'))
  )
}

/** Pure chord step, for tests: the next armed state and the path to go to. */
export function goChord(
  armed: boolean,
  key: string
): { armed: boolean; to: GoPath | null } {
  if (!armed) return { armed: key === 'g', to: key === '?' ? '/help' : null }
  return { armed: false, to: GO_TARGETS[key] ?? null }
}

/**
 * `g` then `h` / `i` / `a` / `d` goes to Home, the Inbox, Actions or the Database page;
 * `?` opens the shortcut list on Help.
 */
export function useGoShortcuts() {
  const navigate = useNavigate()
  useEffect(() => {
    let armed = false
    let timer: number | undefined
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (inEditable(event.target)) return
      const next = goChord(armed, event.key)
      armed = next.armed
      window.clearTimeout(timer)
      if (armed)
        timer = window.setTimeout(() => {
          armed = false
        }, CHORD_WINDOW_MS)
      if (next.to) {
        event.preventDefault()
        void navigate({
          to: next.to,
          hash: next.to === '/help' ? 'keys' : undefined,
        })
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      window.clearTimeout(timer)
    }
  }, [navigate])
}

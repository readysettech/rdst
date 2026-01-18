import { useEffect, useRef } from 'react'

const DEFAULT_EVENTS = ['mousedown', 'touchstart']

export const useClickOutside = <T extends HTMLElement = any>(
  handler: () => void,
  events: string[] = DEFAULT_EVENTS,
  nodes: (HTMLElement | null)[] = []
) => {
  const ref = useRef<T>(null)

  const shouldTriggerHandler = (
    target: HTMLElement,
    nodes: (HTMLElement | null)[]
  ): boolean => {
    if (
      target.hasAttribute('data-ignore-outside-clicks') ||
      !document.body.contains(target)
    ) {
      return false
    }

    if (nodes.length > 0) {
      return nodes.every((node) => node && !node.contains(target))
    }

    return ref.current ? !ref.current.contains(target) : false
  }

  // biome-ignore lint/correctness/useExhaustiveDependencies: <!>
  useEffect(() => {
    const handleEvent = (event: Event) => {
      const target = event.target as HTMLElement

      if (shouldTriggerHandler(target, nodes)) {
        handler()
      }
    }

    events.forEach((event) => document.addEventListener(event, handleEvent))

    return () => {
      events.forEach((event) =>
        document.removeEventListener(event, handleEvent)
      )
    }
  }, [handler, events, nodes])

  return ref
}

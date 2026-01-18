import { type RefObject, useEffect, useRef } from 'react'

export const useOnResize = (
  ref: RefObject<Element | null>,
  callback: (args: { currentHeight: number; previousHeight: number }) => void
) => {
  const observer = useRef<ResizeObserver | null>(null)
  const previousHeightRef = useRef<number | null>(null)

  useEffect(() => {
    if (!ref.current) return

    observer.current = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const currentHeight = entry.contentRect.height
        const previousHeight = previousHeightRef.current ?? currentHeight

        previousHeightRef.current = currentHeight

        callback({ currentHeight, previousHeight })
      }
    })

    observer.current.observe(ref.current)

    return () => {
      if (observer.current) {
        observer.current.disconnect()
        observer.current = null
      }
    }
  }, [ref, callback])
}

import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { useWindowEvent } from './use-window-event'

const eventListerOptions = {
  passive: true,
}

export const useViewportSize = () => {
  const [windowSize, setWindowSize] = useState({
    width: 0,
    height: 0,
  })

  const setSize = useCallback(() => {
    setWindowSize({
      width: window.innerWidth || 0,
      height: window.innerHeight || 0,
    })
  }, [])

  useWindowEvent('resize', setSize, eventListerOptions)
  useWindowEvent('orientationchange', setSize, eventListerOptions)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(setSize, [])

  return windowSize
}

function subscribe(callback: () => void) {
  window.addEventListener('resize', callback)
  window.addEventListener('orientationchange', callback)

  return () => {
    window.removeEventListener('resize', callback)
    window.removeEventListener('orientationchange', callback)
  }
}

export const useViewportSize2 = () => {
  return useSyncExternalStore(subscribe, () => ({
    width: window.innerWidth || 0,
    height: window.innerHeight || 0,
  }))
}

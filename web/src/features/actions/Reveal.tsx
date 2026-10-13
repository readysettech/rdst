// Content that opens and closes by growing to its height, so a row never snaps open.
import { AnimatePresence, m } from '@rs/ui-new/motion'
import type { ReactNode } from 'react'

export function Reveal({
  open,
  children,
}: {
  open: boolean
  children: ReactNode
}) {
  return (
    <AnimatePresence initial={false}>
      {open ? (
        <m.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.22, ease: [0.2, 0, 0, 1] }}
          className="overflow-hidden"
        >
          {children}
        </m.div>
      ) : null}
    </AnimatePresence>
  )
}

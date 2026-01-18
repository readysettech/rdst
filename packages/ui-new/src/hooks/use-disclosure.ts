'use client'

import { useCallback, useEffect, useState } from 'react'

type UseDisclosureProps = {
  open?: boolean
  onOpenChange?: (open: boolean) => void
  blockClose?: boolean
}

export const useDisclosure = ({
  open: controlledOpen,
  onOpenChange,
  blockClose = false,
}: UseDisclosureProps) => {
  const [internalOpen, setInternalOpen] = useState(controlledOpen ?? false)

  const isControlled = controlledOpen !== undefined

  const open = isControlled ? controlledOpen : internalOpen

  const setOpen = useCallback(
    (newOpen: boolean) => {
      if (blockClose && !newOpen) return

      if (isControlled) {
        onOpenChange?.(newOpen)
      } else {
        setInternalOpen(newOpen)
      }
    },
    [isControlled, onOpenChange, blockClose]
  )

  useEffect(() => {
    if (isControlled && controlledOpen !== internalOpen) {
      setInternalOpen(controlledOpen)
    }
  }, [controlledOpen, isControlled, internalOpen])

  return [open, setOpen] as const
}

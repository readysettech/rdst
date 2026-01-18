import type { ReactNode } from 'react'
import { create } from 'zustand'
import type {
  ToastActionElement,
  ToastProps,
} from '../components/feedback/toast'

const TOAST_LIMIT = 1
const TOAST_REMOVE_DELAY = 1000000

type ToasterToast = ToastProps & {
  id: string
  title?: ReactNode
  description?: ReactNode
  action?: ToastActionElement
}

let count = 0
function genId() {
  count = (count + 1) % Number.MAX_SAFE_INTEGER
  return count.toString()
}

type State = {
  toasts: ToasterToast[]
  addToast: (toast: ToasterToast) => void
  updateToast: (toast: Partial<ToasterToast>) => void
  dismissToast: (toastId?: string) => void
  removeToast: (toastId: string) => void
}

const toastTimeouts = new Map<string, ReturnType<typeof setTimeout>>()

const addToRemoveQueue = (
  toastId: string,
  removeToast: (toastId: string) => void
) => {
  if (toastTimeouts.has(toastId)) return

  const timeout = setTimeout(() => {
    toastTimeouts.delete(toastId)
    removeToast(toastId)
  }, TOAST_REMOVE_DELAY)

  toastTimeouts.set(toastId, timeout)
}

// Zustand store setup
export const useToastStore = create<State>((set, get) => ({
  toasts: [],
  addToast: (toast: ToasterToast) => {
    set((state) => ({
      toasts: [toast, ...state.toasts].slice(0, TOAST_LIMIT),
    }))
  },
  updateToast: (toast: Partial<ToasterToast>) => {
    set((state) => ({
      toasts: state.toasts.map((t) =>
        t.id === toast.id ? { ...t, ...toast } : t
      ),
    }))
  },
  dismissToast: (toastId?: string) => {
    if (toastId) {
      addToRemoveQueue(toastId, get().removeToast)
    } else {
      get().toasts.forEach((toast) => {
        addToRemoveQueue(toast.id, get().removeToast)
      })
    }

    set((state) => ({
      toasts: state.toasts.map((t) =>
        t.id === toastId || toastId === undefined ? { ...t, open: false } : t
      ),
    }))
  },
  removeToast: (toastId: string) => {
    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== toastId),
    }))
  },
}))

type Toast = Omit<ToasterToast, 'id'>

function toast({ ...props }: Toast) {
  const id = genId()
  const { addToast, updateToast, dismissToast } = useToastStore.getState()

  const update = (props: ToasterToast) => updateToast({ ...props, id })

  const dismiss = () => dismissToast(id)

  addToast({
    ...props,
    id,
    open: true,
    onOpenChange: (open: boolean) => {
      if (!open) dismiss()
    },
  })

  return {
    id,
    dismiss,
    update,
  }
}

function useToast() {
  const { toasts, dismissToast } = useToastStore()

  return {
    toasts,
    toast,
    dismiss: dismissToast,
  }
}

export { toast, useToast }

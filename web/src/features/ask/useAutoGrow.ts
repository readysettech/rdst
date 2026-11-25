// Fits a textarea's height to its text, within the textarea's own min and max heights;
// past the max it scrolls and keeps the end of the text in view while typing there.
import { type RefObject, useLayoutEffect } from 'react'

export function useAutoGrow(
  ref: RefObject<HTMLTextAreaElement | null>,
  value: unknown
) {
  useLayoutEffect(() => {
    const box = ref.current
    if (!box) return
    box.style.height = 'auto'
    box.style.height = `${box.scrollHeight + box.offsetHeight - box.clientHeight}px`
    if (box.selectionEnd === box.value.length) box.scrollTop = box.scrollHeight
  }, [ref, value])
}

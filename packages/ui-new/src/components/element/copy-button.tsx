import { useState } from 'react'
import { Button } from './button'

interface CopyButtonProps {
  text: string
  /** Optional visible resting label. Omit it for the compact icon-only form. */
  label?: string
  copiedLabel?: string
}

export const CopyButton = ({
  text,
  label,
  copiedLabel = 'Copied',
}: CopyButtonProps) => {
  const [isCopied, setIsCopied] = useState(false)

  const copyTextToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setIsCopied(true)

      setTimeout(() => setIsCopied(false), 2000)
    } catch {
      setIsCopied(false)
    }
  }

  return (
    <Button
      label={isCopied ? copiedLabel : (label ?? 'Copy')}
      variant="primary"
      modifier="link"
      size="small"
      icon={isCopied ? 'tick-double' : 'copy'}
      iconPosition={isCopied || label ? 'left' : 'icon'}
      onClick={() => copyTextToClipboard(text)}
      className="no-underline"
    />
  )
}

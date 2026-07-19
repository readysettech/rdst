import { useState } from 'react'
import { Button } from './button'

export const CopyButton = ({ text }: { text: string }) => {
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
      label={isCopied ? 'Copied' : 'Copy'}
      modifier="link"
      size="small"
      icon={isCopied ? 'tick-double' : 'copy'}
      iconPosition={isCopied ? 'left' : 'icon'}
      onClick={() => copyTextToClipboard(text)}
      className="no-underline"
    />
  )
}

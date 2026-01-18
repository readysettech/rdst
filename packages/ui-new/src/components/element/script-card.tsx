import { cn } from '@rs/tailwind-base'
import { m } from 'motion/react'
import { useEffect, useRef, useState } from 'react'
import { Show } from '../../control-flow/show'
import { useToast } from '../../hooks/use-toast'
import { Button } from './button'
import { Card } from './card'
import { Highlight, type HighlightLanguageType } from './highlight'
import { HStack } from './stack'
import { Text } from './text'

type ScriptCardProps = {
  value: string
  lang?: HighlightLanguageType
  title?: string
  hideCopyButton?: boolean
  showDownloadButton?: boolean
  downloadButtonLabel?: string
  isMarketing?: boolean
}
// & Omit<SyntaxHighlighterProps, 'children'>

export const ScriptCard = ({
  hideCopyButton,
  lang,
  title,
  isMarketing,
  showDownloadButton,
  downloadButtonLabel,
  ...props
}: ScriptCardProps) => {
  const [isExpanded, setIsExpanded] = useState(false)
  const [isCopied, setIsCopied] = useState(false)
  const [canExpand, setCanExpand] = useState(false)

  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (ref.current) {
      setCanExpand(ref.current.scrollHeight > 460)
    }
  }, [props.value])

  const { toast } = useToast()

  const copyTextToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setIsCopied(true)
      toast({
        title: 'Copied',
        description: 'The script has been copied to your clipboard.',
        variant: 'positive',
      })
      setTimeout(() => setIsCopied(false), 2000)
    } catch {
      toast({
        title: 'Failed to copy',
        description: 'Failed to copy the script to your clipboard.',
        variant: 'negative',
      })
    }
  }

  const downloadScript = () => {
    if (!props.value) return

    const blob = new Blob([props.value], { type: 'text/x-sh' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = downloadButtonLabel || 'script.sh'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  return (
    <m.div
      className={cn([
        'overflow-hidden',
        'relative',
        'max-w-[inherit]',
        'border-(length:--border-base)',
        'border-border-layout-1',
        'rounded-[1.25rem]',
      ])}
      initial={{
        height: canExpand ? 64 * 4 : 'auto',
      }}
      animate={{
        height: isExpanded || !canExpand ? 'auto' : 64 * 4,
      }}
    >
      {canExpand && (
        <m.div
          className={cn([
            'absolute',
            'inset-0',
            'bg-linear-to-t',
            'from-surface-layout-2',
            'to-transparent',
            'z-10',
            'flex',
            'justify-center',
            'items-center',
          ])}
          animate={{
            height: isExpanded ? 24 * 4 : 'auto',
            top: isExpanded ? 'auto' : '0',
          }}
        >
          <Button
            label={isExpanded ? 'Collapse' : 'Expand'}
            modifier="ghost"
            onClick={() => setIsExpanded(!isExpanded)}
          />
        </m.div>
      )}

      <Card>
        <div className={cn(['absolute', 'right-2', 'top-2', 'z-10'])}>
          <HStack>
            <Show when={!hideCopyButton}>
              <Button
                label="Copied"
                modifier="ghost"
                size="small"
                icon={isCopied ? 'tick-double' : 'copy'}
                iconPosition={isCopied ? 'left' : 'icon'}
                onClick={() => copyTextToClipboard(props.value)}
              />
            </Show>
            <Show when={showDownloadButton}>
              <Button
                label="Download Script"
                modifier="ghost"
                size="small"
                icon="arrow-down"
                iconPosition="left"
                onClick={downloadScript}
              />
            </Show>
          </HStack>
        </div>
        {title && (
          <div
            className={cn([
              'z-10',
              'py-4',
              'px-6',
              'border-b-(length:--border-base)',
              'border-border-layout-soft',
              'flex',
              'items-center',
            ])}
          >
            <Text level="subtitle-2" className="text-content-layout-3">
              {title}
            </Text>
          </div>
        )}
        <div ref={ref}>
          <div
            className={cn(
              isMarketing ? 'p-0' : 'p-6',
              isExpanded ? 'pb-28' : 'pb-6'
            )}
          >
            <Highlight lang={lang} isMarketing={isMarketing}>
              {props.value}
            </Highlight>
          </div>
        </div>
      </Card>
    </m.div>
  )
}

import prism from 'prismjs'
import 'prismjs/components/prism-bash'
import 'prismjs/components/prism-json'
import 'prismjs/components/prism-markdown'
import 'prismjs/components/prism-python'
import 'prismjs/components/prism-sql'
import 'prismjs/components/prism-typescript'
import 'prismjs/components/prism-jsx'
import 'prismjs/components/prism-tsx'
import { cn } from '@rs/tailwind-base'
import { useEffect, useRef } from 'react'
import './highlight.css'
import type { WithChildren } from '../../helpers/types'
import { Scrollable } from '../overlay/scrollable'

export type HighlightLanguageType =
  | 'json'
  | 'sql'
  | 'bash'
  | 'markdown'
  | 'js'
  | 'python'
  | 'typescript'
  | 'jsx'
  | 'tsx'

type HighlightProps = {
  lang?: HighlightLanguageType
  isMarketing?: boolean
  // Padding applied inside the marketing-mode horizontal scroll viewport.
  scrollPadding?: string
} & WithChildren

export const Highlight = ({
  lang = 'json',
  children,
  isMarketing,
  scrollPadding = 'p-6',
}: HighlightProps) => {
  const codeRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (typeof window !== 'undefined' && codeRef.current) {
      prism.highlightElement(codeRef.current)
    }
  }, [lang, children])

  const className = cn('rs-highlight', isMarketing && 'rs-highlight--marketing')

  if (isMarketing) {
    return (
      <pre className={className}>
        <Scrollable orientation="horizontal" className={scrollPadding}>
          <code ref={codeRef} className={`language-${lang}`}>
            {children}
          </code>
        </Scrollable>
      </pre>
    )
  }

  return (
    <pre className={className}>
      <code ref={codeRef} className={`language-${lang}`}>
        {children}
      </code>
    </pre>
  )
}

import { compiler, type MarkdownToJSX } from 'markdown-to-jsx/react'
import './markdown.css'

export const proseClassName = 'rs-markdown'

type MarkdownProps = {
  children: string
  options?: MarkdownToJSX.Options
}

export const Markdown = ({ children, options }: MarkdownProps) => {
  return <div className={proseClassName}>{compiler(children, options)}</div>
}

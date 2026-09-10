import { PromptComposer } from './PromptComposer'

interface MessageInputProps {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  isLoading?: boolean
  placeholder?: string
}

/**
 * The conversation's composer: the shared {@link PromptComposer}, sized for a
 * follow-up question — two lines to start, five at most, with the send action
 * beside it rather than under it.
 */
export function MessageInput({
  value,
  onChange,
  onSubmit,
  isLoading,
  placeholder,
}: MessageInputProps) {
  return (
    <PromptComposer
      value={value}
      onChange={onChange}
      onSubmit={onSubmit}
      label="Follow-up question"
      placeholder={placeholder || 'Ask about this analysis'}
      busy={isLoading}
      submitLabel="Send"
      submitIcon="arrow-right"
      submitIconPosition="right"
      submitSize="small"
      fieldClassName="min-h-[2lh] max-h-[5lh]"
      autoGrow
    />
  )
}

import type { ChangeEvent, KeyboardEvent } from 'react';
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea';
import { Button } from '@rs/ui-new/button';
import { VStack } from '@rs/ui-new/stack';

interface MessageInputProps {
  value: string;
  onChange: (e: ChangeEvent<HTMLTextAreaElement>) => void;
  onSubmit: () => void;
  isLoading?: boolean;
  placeholder?: string;
}

export function MessageInput({ value, onChange, onSubmit, isLoading, placeholder }: MessageInputProps) {
  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSubmit();
    }
  };

  return (
    <VStack className="gap-2 items-stretch">
      <BaseInputTextarea
        value={value}
        onChange={onChange}
        onKeyDown={handleKeyDown}
        placeholder={placeholder || "Ask a question..."}
        disabled={isLoading}
      />
      <Button
        onClick={onSubmit}
        disabled={isLoading || !value.trim()}
        variant="primary"
        modifier="solid"
        size="base"
        label="Send"
        icon="arrow-right"
        iconPosition="right"
        loading={isLoading}
        fullWidth
      />
    </VStack>
  );
}

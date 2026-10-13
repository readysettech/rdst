// One preference as a ghost menu at the right of a Settings row: the current choice as
// the button, every choice in the menu, the active one ticked.
import { Button } from '@rs/ui-new/button'
import { Dropdown } from '@rs/ui-new/dropdown'

export function PreferenceMenu<T extends string | number>({
  label,
  value,
  options,
  optionLabel,
  disabled,
  onChange,
}: {
  /** The row's title, for the button's accessible name. */
  label: string
  value: T
  options: readonly T[]
  optionLabel: (option: T) => string
  disabled?: boolean
  onChange: (next: T) => void
}) {
  return (
    <Dropdown>
      <Dropdown.Trigger asChild>
        <Button
          label={optionLabel(value)}
          icon="chevron-down"
          iconPosition="right"
          modifier="ghost"
          size="small"
          disabled={disabled}
          aria-label={`${label}: ${optionLabel(value)}`}
        />
      </Dropdown.Trigger>
      <Dropdown.Content align="end" className="min-w-40">
        {options.map((option) => (
          <Dropdown.Item
            key={String(option)}
            label={optionLabel(option)}
            active={option === value}
            rightIcon={option === value ? 'tick' : undefined}
            onSelect={() => onChange(option)}
          />
        ))}
      </Dropdown.Content>
    </Dropdown>
  )
}

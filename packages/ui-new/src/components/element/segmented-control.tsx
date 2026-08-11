'use client'

import { tv, type VariantProps } from '@rs/tailwind-base'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import {
  type FocusEvent,
  type KeyboardEvent,
  memo,
  useRef,
  useState,
} from 'react'
import { Icon } from '../svg/icon'

// One control, two semantic modes. `tabs` (default) is the view-switcher idiom
// — WAI-ARIA tablist with manual activation: arrow keys move a roving-tabindex
// focus, Enter/Space (or click) select. `radio` is the filter-value idiom —
// radiogroup/radio with aria-checked and activation-follows-focus, per the
// native radio-button convention. Both share the pill look; only the roles,
// selected-state attribute, and activation model differ.

const segmentedControlStyles = tv({
  slots: {
    root: [
      'flex',
      'gap-1',
      'p-1',
      'rounded-2xl',
      'border',
      'border-border-layout-1',
      'bg-surface-layout-2/60',
      'w-fit',
    ],
    segment: [
      'flex',
      'items-center',
      'gap-1.5',
      'rounded-xl',
      'text-button-small',
      'transition-colors',
      'focus-visible:outline-none',
      'focus-visible:shadow-focus',
    ],
  },
  variants: {
    size: {
      base: { segment: ['px-4', 'h-8'] },
      small: { segment: ['px-3', 'h-7'] },
    },
    selected: {
      true: {
        segment: ['bg-surface-primary-soft', 'text-content-primary-soft'],
      },
      false: {
        segment: ['text-content-layout-3', 'hover:text-content-layout-2'],
      },
    },
    disabled: {
      false: { segment: ['cursor-pointer'] },
      true: {
        segment: ['opacity-50', 'cursor-not-allowed', 'pointer-events-none'],
      },
    },
  },
  defaultVariants: {
    size: 'base',
    selected: false,
    disabled: false,
  },
})

// Icon tracks the segment size — 14px at base, 12px at small.
const iconClassBySize: Record<SegmentedControlSize, string> = {
  base: 'w-3.5 h-3.5',
  small: 'w-3 h-3',
}

type SegmentedControlSize = NonNullable<
  VariantProps<typeof segmentedControlStyles>['size']
>

type Segment<T extends string = string> = {
  value: T
  label: string
  icon?: IconStrokeName
}

type SegmentedControlProps<T extends string> = {
  /** Selected segment value — this is a controlled component. */
  value: T
  onValueChange: (value: T) => void
  segments: Array<Segment<T>>
  /** `tabs` (default) for view switching, `radio` for a filter value. */
  mode?: 'tabs' | 'radio'
  size?: SegmentedControlSize
  disabled?: boolean
  /** Panel controlled by the active tab (tabs mode only). */
  panelId?: string
  /** Names the group for assistive tech — required. */
  'aria-label': string
  className?: string
}

function SegmentedControlImpl<T extends string>({
  value,
  onValueChange,
  segments,
  mode = 'tabs',
  size = 'base',
  disabled = false,
  panelId,
  'aria-label': ariaLabel,
  className,
}: SegmentedControlProps<T>) {
  const styles = segmentedControlStyles({ size, disabled })
  const isTabs = mode !== 'radio'
  const segmentRefs = useRef<Array<HTMLButtonElement | null>>([])

  // Tabs mode uses manual activation (WAI-ARIA): arrow keys move this roving
  // focus without selecting, and it resets to the selected segment once focus
  // leaves the group. Radio mode has no independent focus target — it keeps
  // activation-follows-focus, the native radio-button convention.
  const [focusedValue, setFocusedValue] = useState<T | null>(null)

  const activeIndex = segments.findIndex((s) => s.value === value)
  const rovingValue = isTabs
    ? (focusedValue ?? (activeIndex >= 0 ? value : segments[0]?.value))
    : value

  // Move focus and select in one step — activation-follows-focus, for radio
  // mode's arrow keys and for any programmatic activation.
  function activate(index: number) {
    const target = segments[index]
    if (!target) return
    segmentRefs.current[index]?.focus()
    onValueChange(target.value)
  }

  // Move the roving focus only — tabs mode's arrow keys, per manual activation.
  function moveFocus(index: number) {
    const target = segments[index]
    if (!target) return
    setFocusedValue(target.value)
    segmentRefs.current[index]?.focus()
  }

  function handleKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (disabled) return
    const count = segments.length
    const currentIndex = segments.findIndex((s) => s.value === rovingValue)
    const from = currentIndex < 0 ? 0 : currentIndex
    let next: number
    switch (e.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        next = (from + 1) % count
        break
      case 'ArrowLeft':
      case 'ArrowUp':
        next = (from - 1 + count) % count
        break
      case 'Home':
        next = 0
        break
      case 'End':
        next = count - 1
        break
      case 'Enter':
      case ' ': {
        // Activate whichever segment actually holds DOM focus. The tracked
        // roving state can be reset by a window blur while focus stays on the
        // segment (alt-tab away and back), so the DOM is the truth here.
        if (!isTabs) return
        const focusedIndex = segmentRefs.current.findIndex(
          (el) => el === e.target
        )
        const target = focusedIndex >= 0 ? segments[focusedIndex] : undefined
        if (target) {
          e.preventDefault()
          onValueChange(target.value)
        }
        return
      }
      default:
        return
    }
    e.preventDefault()
    if (isTabs) {
      moveFocus(next)
    } else {
      activate(next)
    }
  }

  // Reset the roving focus to the selected segment once focus leaves the
  // group entirely (not just moving between segments within it).
  function handleBlur(e: FocusEvent<HTMLDivElement>) {
    if (!isTabs) return
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return
    setFocusedValue(null)
  }

  // Re-sync the roving state to whichever segment receives focus, so a
  // window blur/refocus cycle (which resets the state but leaves DOM focus
  // in place) can't leave arrows computing from the wrong segment.
  function handleFocus(e: FocusEvent<HTMLDivElement>) {
    if (!isTabs) return
    // e.target is typed as the container, but focus events bubble up from the
    // segment buttons — compare as a plain element.
    const focusTarget = e.target as HTMLElement
    const focusedIndex = segmentRefs.current.findIndex(
      (el) => el === focusTarget
    )
    const target = focusedIndex >= 0 ? segments[focusedIndex] : undefined
    if (target && target.value !== focusedValue) setFocusedValue(target.value)
  }

  return (
    <div
      role={isTabs ? 'tablist' : 'radiogroup'}
      aria-label={ariaLabel}
      aria-orientation={isTabs ? 'horizontal' : undefined}
      className={styles.root({ class: className })}
      onKeyDown={handleKeyDown}
      onBlur={handleBlur}
      onFocus={handleFocus}
    >
      {segments.map((segment, index) => {
        const selected = segment.value === value
        // Only the roving-focus segment is in the tab order; a group with no
        // match keeps the first segment reachable so the control is never a
        // keyboard trap.
        const focusable =
          !disabled &&
          (isTabs
            ? segment.value === rovingValue
            : selected || (activeIndex < 0 && index === 0))
        return (
          <button
            key={segment.value}
            id={
              isTabs && panelId ? `${panelId}-tab-${segment.value}` : undefined
            }
            ref={(el) => {
              segmentRefs.current[index] = el
            }}
            type="button"
            role={isTabs ? 'tab' : 'radio'}
            aria-selected={isTabs ? selected : undefined}
            aria-checked={isTabs ? undefined : selected}
            aria-controls={isTabs && panelId ? panelId : undefined}
            tabIndex={focusable ? 0 : -1}
            disabled={disabled}
            onClick={() => !disabled && onValueChange(segment.value)}
            className={styles.segment({ selected, disabled })}
          >
            {segment.icon && (
              <Icon
                name={segment.icon}
                label=""
                aria-hidden="true"
                className={iconClassBySize[size]}
              />
            )}
            <span>{segment.label}</span>
          </button>
        )
      })}
    </div>
  )
}

const SegmentedControl = memo(
  SegmentedControlImpl
) as typeof SegmentedControlImpl

export { SegmentedControl, segmentedControlStyles }
export type { SegmentedControlProps, Segment as SegmentedControlSegment }

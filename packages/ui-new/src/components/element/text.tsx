import { Slot } from '@radix-ui/react-slot'
import { tv, type VariantProps } from '@rs/tailwind-base'
import { forwardRef, memo, type Ref } from 'react'
import type {
  WithAsChild,
  WithChildren,
  WithClassName,
} from '../../helpers/types'

const textStyles = tv({
  variants: {
    level: {
      // Display-size numeral for the single hero stat per screen (the /analyze
      // score, /demo multiplier). Maps --text-stat-hero (48/500), tabular so
      // digits line up. [design-system §3/§8 #11; VIS-012, VIS-116]
      'stat-hero': ['text-stat-hero', 'tabular-nums'],
      'display-large': 'text-display-large',
      'display-medium': 'text-display-medium',
      'display-small': 'text-display-small',
      'headline-1': 'text-headline-1',
      'headline-2': 'text-headline-2',
      'headline-3': 'text-headline-3',
      'headline-4': 'text-headline-4',
      'headline-5': 'text-headline-5',
      'subtitle-1': 'text-subtitle-1',
      'subtitle-2': 'text-subtitle-2',
      'button-large': 'text-button-large',
      'button-medium': 'text-button-medium',
      'button-small': 'text-button-small',
      'label-large': 'text-label-large',
      'label-medium': 'text-label-medium',
      'label-small': 'text-label-small',
      'label-extra-small': 'text-label-extra-small',
      'body-large': 'text-body-large',
      'body-medium': 'text-body-medium',
      'body-small': 'text-body-small',
      caption: 'text-caption',
      overline: ['text-overline', 'uppercase'],
      'mono-small': ['text-mono-small', 'font-mono'],
      'mono-medium': ['text-mono-medium', 'font-mono'],
      'mono-large': ['text-mono-large', 'font-mono'],
    },
    italic: {
      true: 'italic',
    },
    underline: {
      true: 'underline',
    },
  },
  defaultVariants: {
    level: 'body-medium',
  },
})

export type AsType =
  | 'h1'
  | 'h2'
  | 'h3'
  | 'h4'
  | 'h5'
  | 'h6'
  | 'p'
  | 'span'
  | 'div'
  | 'label'
  | 'small'
  | 'strong'

type HTMLElementTagNameMap = {
  h1: HTMLHeadingElement
  h2: HTMLHeadingElement
  h3: HTMLHeadingElement
  h4: HTMLHeadingElement
  h5: HTMLHeadingElement
  h6: HTMLHeadingElement
  p: HTMLParagraphElement
  span: HTMLSpanElement
  div: HTMLDivElement
  label: HTMLLabelElement
  small: HTMLElement
  strong: HTMLElement
}

type TextVariants = VariantProps<typeof textStyles>

type TextProps<T extends AsType = 'p'> = TextVariants & {
  as?: T
} & WithChildren &
  WithAsChild &
  WithClassName

const Text = forwardRef(
  <T extends AsType>(
    {
      level,
      italic,
      underline,
      as: Component = 'p' as T,
      className,
      asChild,
      children,
      ...restProps
    }: TextProps<T>,
    ref: Ref<HTMLElementTagNameMap[T]>
  ) => {
    const styles = textStyles({
      level,
      italic,
      underline,
      className,
    })

    const Comp = asChild ? Slot : Component

    return (
      <Comp ref={ref as any} {...restProps} className={styles}>
        {children}
      </Comp>
    )
  }
)

const MemoizedText = memo(Text)

export { MemoizedText as Text, textStyles }

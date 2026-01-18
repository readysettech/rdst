import * as RadixAvatar from '@radix-ui/react-avatar'
import { tv, type VariantProps } from '@rs/tailwind-base'

const avatarStyles = tv({
  slots: {
    root: [
      'relative',
      'inline-flex',
      'items-center',
      'justify-center',
      'align-middle',
      'overflow-hidden',
      'select-none',
      'bg-surface-layout-1',
    ],
    image: ['aspect-square', 'h-full', 'w-full', 'object-cover'],
    fallback: [
      'flex',
      'h-full',
      'w-full',
      'items-center',
      'justify-center',
      'bg-surface-layout-2',
      'text-content-layout-1',
      'text-label-small',
    ],
  },
  variants: {
    size: {
      extraSmall: {
        root: ['h-6', 'w-6', 'rounded'],
        fallback: ['rounded-sm'],
      },
      small: {
        root: ['h-8', 'w-8', 'rounded-md'],
        fallback: ['rounded-md'],
      },
      medium: {
        root: ['h-10', 'w-10', 'rounded-lg'],
        fallback: ['rounded-lg'],
      },
      large: {
        root: ['h-12', 'w-12', 'rounded-lg'],
        fallback: ['rounded-lg'],
      },
    },
  },
  defaultVariants: {
    size: 'small',
  },
})

export type AvatarProps = {
  name: string
  src?: string
  className?: string
  imageClassName?: string
  fallbackClassName?: string
} & VariantProps<typeof avatarStyles>

const getInitials = (name: string): string => {
  const words = name.trim().split(/\s+/)

  if (words.length > 1) {
    return (words[0]?.charAt(0) || '') + (words[1]?.charAt(0) || '')
  }
  if (words.length === 1) {
    return words[0]?.charAt(0) || ''
  }
  return ''
}

export const Avatar = (props: AvatarProps) => {
  const { className, fallbackClassName, imageClassName, name, size, src } =
    props
  const styles = avatarStyles({ size })

  return (
    <RadixAvatar.Root className={styles.root({ class: className })}>
      <RadixAvatar.Image
        className={styles.image({ class: imageClassName })}
        src={src}
        alt={`${name}'s Image`}
      />
      <RadixAvatar.Fallback
        className={styles.fallback({ class: fallbackClassName })}
      >
        {getInitials(name)}
      </RadixAvatar.Fallback>
    </RadixAvatar.Root>
  )
}

import { AnimatePresence, m } from 'motion/react'
import { getTransition } from '../../motion/transition'
import { Spinner } from '../feedback/spinner'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { Icon } from './icon'

type IconWithSpinnerProps = {
  loading?: boolean
  hasIcon?: boolean
  rightGap?: boolean
  leftGap?: boolean
  icon: IconStrokeName
  label: string
}

const variants = {
  initial: { opacity: 0, width: 0 },
  animate: { opacity: 1, width: 'auto' },
  exit: { opacity: 0, width: 0 },
}

export const IconWithSpinner = ({
  loading,
  hasIcon,
  rightGap,
  leftGap,
  icon,
  label,
}: IconWithSpinnerProps) => {
  const transition = getTransition()

  return (
    <AnimatePresence mode="wait">
      {loading && (
        <m.div
          initial="initial"
          animate="animate"
          exit="exit"
          variants={variants}
          transition={transition}
          className="flex"
        >
          {leftGap && <div className="h-1 w-1" />}
          <Spinner color="layout" />
          {rightGap && <div className="h-1 w-1" />}
        </m.div>
      )}
      {!loading && hasIcon && (
        <m.div
          initial="false"
          animate="animate"
          exit="exit"
          variants={variants}
          transition={transition}
          className="flex"
        >
          {leftGap && <div className="h-1 w-1" />}
          <Icon name={icon} label={label} />
          {rightGap && <div className="h-1 w-1" />}
        </m.div>
      )}
    </AnimatePresence>
  )
}

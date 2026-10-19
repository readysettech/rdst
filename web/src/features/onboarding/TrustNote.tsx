// Who's behind RDST, in one line, for the moments people decide whether to trust it: the
// tour and the sign-in ask. Every claim here is published by Readyset (the readyset
// repository's history page).
import { cn } from '@rs/tailwind-base'
import { ReadysetLogo } from '@rs/ui-new/logo'
import { Text } from '@rs/ui-new/text'
import type { ReactNode } from 'react'
import { PRIVACY_DOC_URL } from './onboardingState'

export const READYSET_HISTORY_URL =
  'https://github.com/readysettech/readyset/blob/main/history.md'

const link =
  'underline decoration-border-layout-2 underline-offset-2 transition-colors duration-fast hover:text-content-layout-1'

export const AUTHORITY_LINE =
  "RDST is made by Readyset. Our founding team comes from MIT's PDOS lab, where the Noria database research began."

/** The authority line, with a link to where Readyset comes from and any `children` links. */
export function AuthorityNote({
  className,
  children,
}: {
  className?: string
  children?: ReactNode
}) {
  return (
    <div
      className={cn('flex gap-2.5 text-content-layout-3', className)}
      data-testid="authority-note"
    >
      <span className="mt-0.5 shrink-0 text-content-layout-2">
        <ReadysetLogo width={14} height={14} />
      </span>
      <div className="min-w-0">
        <Text as="p" level="caption">
          {AUTHORITY_LINE}
        </Text>
        <Text as="p" level="caption" className="mt-1 flex flex-wrap gap-x-4">
          <a
            href={READYSET_HISTORY_URL}
            target="_blank"
            rel="noreferrer"
            className={link}
          >
            Our story
          </a>
          {children}
        </Text>
      </div>
    </div>
  )
}

/** The compact note beside an email or sign-in ask: who we are, and how data is handled. */
export function SignInTrustNote({ className }: { className?: string }) {
  return (
    <AuthorityNote className={className}>
      <a
        href={PRIVACY_DOC_URL}
        target="_blank"
        rel="noreferrer"
        className={link}
      >
        How RDST handles your data
      </a>
    </AuthorityNote>
  )
}

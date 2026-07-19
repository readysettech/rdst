import { useNavigate } from '@tanstack/react-router';
import { InlineNotice } from '@rs/ui-new/error-state';

/**
 * The routable-error primitive for the return-trip half of the identity flow
 * (configure-and-identity). A stable machine-readable `error_code` from the
 * backend maps to one of three notices, each of which **names the object**,
 * carries icon + text + one action (never color alone), and **deep-links to
 * the fix while carrying a `returnTo` intent** so the user lands back on the
 * feature they were using. Built on the shared `@rs/ui-new` `InlineNotice`
 * (B7/T24) — no second error surface invented. [USE-100, USE-099, USE-021, USE-077]
 */
// The three kinds correspond to the stable backend codes: `password-needed`
// ↔ the 423 `TARGET_PASSWORD_REQUIRED` from `target_guard.py`;
// `key-needed` ↔ `NO_API_KEY`; `trial-exhausted` ↔ `TRIAL_EXHAUSTED`
// (both from `resolve_api_key()`).
export type RoutableNoticeKind =
  | 'password-needed'
  | 'key-needed'
  | 'trial-exhausted';

interface RoutableNoticeProps {
  kind: RoutableNoticeKind;
  /** The offending connection name (password case) — named in the notice. */
  target?: string | null;
  /** Where to send the user after the fix; defaults to the current location. */
  returnTo?: string;
  /** Override the default title copy. */
  title?: string;
  /** Override the default message copy. */
  message?: string;
  /** Secondary in-place action (e.g. "Set here"), rendered beside the route. */
  onRetry?: () => void;
  retryLabel?: string;
  /** Runs just before routing (e.g. close a parent dialog). */
  onBeforeRoute?: () => void;
  className?: string;
}

export function RoutableNotice({
  kind,
  target,
  returnTo,
  title,
  message,
  onRetry,
  retryLabel,
  onBeforeRoute,
  className,
}: RoutableNoticeProps) {
  const navigate = useNavigate();
  const currentReturn =
    returnTo ??
    (typeof window !== 'undefined'
      ? `${window.location.pathname}${window.location.search}`
      : undefined);

  const name = target ?? 'This connection';
  const kindDefaults = {
    'password-needed': {
      errorClass: 'database' as const,
      icon: 'key' as const,
      title: 'Connection needs a password',
      message: `${name} needs a password to run this.`,
      actionLabel: 'Fix connection',
      search: { edit: target ?? undefined, returnTo: currentReturn },
    },
    'trial-exhausted': {
      errorClass: 'rdst-service' as const,
      icon: 'sparkles' as const,
      title: 'Free trial used up',
      message:
        'Your free-trial credit is used up — add your own Anthropic key to keep going.',
      actionLabel: 'Add your key',
      search: { section: 'ai' as const, returnTo: currentReturn },
    },
    'key-needed': {
      errorClass: 'provider' as const,
      icon: 'key' as const,
      title: 'This needs an AI key',
      message:
        'Add an Anthropic key (or start a free trial) to use this AI feature.',
      actionLabel: 'Add AI key',
      search: { section: 'ai' as const, returnTo: currentReturn },
    },
  }[kind];

  return (
    <InlineNotice
      errorClass={kindDefaults.errorClass}
      accent="warning"
      icon={kindDefaults.icon}
      title={title ?? kindDefaults.title}
      message={message ?? kindDefaults.message}
      action={{
        label: kindDefaults.actionLabel,
        icon: 'arrow-right',
        onClick: () => {
          onBeforeRoute?.();
          navigate({ to: '/configure', search: kindDefaults.search });
        },
      }}
      onRetry={onRetry}
      retryLabel={retryLabel}
      className={className}
    />
  );
}

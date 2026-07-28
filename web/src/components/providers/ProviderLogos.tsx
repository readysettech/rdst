/**
 * Inline provider marks for the target-source picker. Brand colors are part of
 * the recognition, so these live outside the monochrome icon sprite; the AWS
 * glyph stays `fill-current` because AWS has no single-color mark that reads at
 * this size.
 */

type ProviderLogoProps = {
  size?: number
  className?: string
}

export const SupabaseLogo = ({ size = 20, className }: ProviderLogoProps) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width={size}
    height={size}
    viewBox="0 0 109 113"
    fill="#3ECF8E"
    role="img"
    aria-label="Supabase"
    className={className}
  >
    <path d="M63.708 110.284c-2.86 3.601-8.658 1.628-8.727-2.97l-1.007-67.251h45.219c8.19 0 12.759 9.46 7.665 15.875l-43.15 54.346Z" />
    <path d="M45.317 2.071c2.86-3.601 8.658-1.628 8.727 2.97l.441 67.251H9.831c-8.19 0-12.759-9.46-7.665-15.875L45.317 2.071Z" />
  </svg>
)

export const AwsLogo = ({ size = 20, className }: ProviderLogoProps) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    role="img"
    aria-label="AWS"
    className={`fill-current ${className ?? ''}`}
  >
    <path d="M17.2 17H7.1a4.6 4.6 0 0 1-.7-9.1 5.6 5.6 0 0 1 10.7 1.3 4 4 0 0 1 .1 7.8Zm-10.1-2h10a2 2 0 0 0 .1-4l-.9-.1-.1-.9a3.6 3.6 0 0 0-7-.5l-.2.8-.8.1a2.6 2.6 0 0 0 .9 4.6Z" />
    <path d="M4 19.2a.8.8 0 0 1 1-.4 17.7 17.7 0 0 0 14 0 .8.8 0 0 1 .7 1.4 19.3 19.3 0 0 1-15.3 0 .8.8 0 0 1-.4-1Z" />
  </svg>
)

export const NeonLogo = ({ size = 20, className }: ProviderLogoProps) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    role="img"
    aria-label="Neon"
    className={className}
  >
    <rect
      x="1.5"
      y="1.5"
      width="21"
      height="21"
      rx="5"
      stroke="#00E599"
      strokeWidth="2"
    />
    <path
      d="M7 17V7l7.5 8.6V7"
      stroke="#00E599"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
    <circle cx="17" cy="17" r="1.6" fill="#00E599" />
  </svg>
)

export const DigitalOceanLogo = ({
  size = 20,
  className,
}: ProviderLogoProps) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width={size}
    height={size}
    viewBox="0 0 32 32"
    fill="#0080FF"
    role="img"
    aria-label="DigitalOcean"
    className={className}
  >
    <path d="M16 32v-6.2c6.57 0 11.67-6.51 9.15-13.42a9.29 9.29 0 0 0-5.53-5.53C12.71 4.33 6.2 9.43 6.2 16H0C0 5.53 10.12-2.62 21.09.81c4.79 1.5 8.6 5.31 10.1 10.1C34.62 21.88 26.47 32 16 32Z" />
    <path d="M16 25.83h-6.17v-6.17H16v6.17ZM9.83 30.58H5.09v-4.75h4.74v4.75ZM5.09 25.83H1.12v-3.97h3.97v3.97Z" />
  </svg>
)

export const AzureLogo = ({ size = 20, className }: ProviderLogoProps) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="#0078D4"
    role="img"
    aria-label="Azure"
    className={className}
  >
    <path d="M9.05 3.5h5.16l-5.36 15.9a.82.82 0 0 1-.78.56H3.99a.82.82 0 0 1-.78-1.08l4.98-14.8a.82.82 0 0 1 .78-.58h.08Z" opacity="0.75" />
    <path d="M16.9 14.9H8.72a.38.38 0 0 0-.26.66l5.26 4.9a.82.82 0 0 0 .56.22h4.72l-2.1-5.78Z" />
    <path d="M14.21 3.5a.82.82 0 0 0-.78.56L8.47 18.86l5.4-.02 1-2.94h5.2L16.9 14.9h-3.14l3.4-9.98a.82.82 0 0 0-.78-1.08l-2.17.66Z" opacity="0.9" />
  </svg>
)

export const GcpLogo = ({ size = 20, className }: ProviderLogoProps) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    role="img"
    aria-label="Google Cloud"
    className={className}
  >
    <path d="M15.4 8.1h.7l2-2 .1-.85A9 9 0 0 0 4.4 9.6a1.1 1.1 0 0 1 .7 0l4-.66s.2-.34.31-.32a5 5 0 0 1 6.7-.52Z" fill="#EA4335" />
    <path d="M20.9 9.6a9 9 0 0 0-2.7-4.35l-2.8 2.8a5 5 0 0 1 1.84 3.96v.5a2.5 2.5 0 0 1 0 5h-5l-.5.5v3l.5.49h5a6.5 6.5 0 0 0 3.66-11.9Z" fill="#4285F4" />
    <path d="M6.7 22h5v-4h-5a2.5 2.5 0 0 1-1-.22l-.7.2-2 2-.18.7A6.47 6.47 0 0 0 6.7 22Z" fill="#34A853" />
    <path d="M6.7 9a6.5 6.5 0 0 0-3.92 11.66l2.9-2.9a2.5 2.5 0 0 1 3.3-3.3l2.9-2.9A6.48 6.48 0 0 0 6.7 9Z" fill="#FBBC05" />
  </svg>
)

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
    <path
      d="M9.05 3.5h5.16l-5.36 15.9a.82.82 0 0 1-.78.56H3.99a.82.82 0 0 1-.78-1.08l4.98-14.8a.82.82 0 0 1 .78-.58h.08Z"
      opacity="0.75"
    />
    <path d="M16.9 14.9H8.72a.38.38 0 0 0-.26.66l5.26 4.9a.82.82 0 0 0 .56.22h4.72l-2.1-5.78Z" />
    <path
      d="M14.21 3.5a.82.82 0 0 0-.78.56L8.47 18.86l5.4-.02 1-2.94h5.2L16.9 14.9h-3.14l3.4-9.98a.82.82 0 0 0-.78-1.08l-2.17.66Z"
      opacity="0.9"
    />
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
    <path
      d="M15.4 8.1h.7l2-2 .1-.85A9 9 0 0 0 4.4 9.6a1.1 1.1 0 0 1 .7 0l4-.66s.2-.34.31-.32a5 5 0 0 1 6.7-.52Z"
      fill="#EA4335"
    />
    <path
      d="M20.9 9.6a9 9 0 0 0-2.7-4.35l-2.8 2.8a5 5 0 0 1 1.84 3.96v.5a2.5 2.5 0 0 1 0 5h-5l-.5.5v3l.5.49h5a6.5 6.5 0 0 0 3.66-11.9Z"
      fill="#4285F4"
    />
    <path
      d="M6.7 22h5v-4h-5a2.5 2.5 0 0 1-1-.22l-.7.2-2 2-.18.7A6.47 6.47 0 0 0 6.7 22Z"
      fill="#34A853"
    />
    <path
      d="M6.7 9a6.5 6.5 0 0 0-3.92 11.66l2.9-2.9a2.5 2.5 0 0 1 3.3-3.3l2.9-2.9A6.48 6.48 0 0 0 6.7 9Z"
      fill="#FBBC05"
    />
  </svg>
)

export const ClaudeLogo = ({ size = 20, className }: ProviderLogoProps) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="#D97757"
    role="img"
    aria-label="Claude"
    className={className}
  >
    <path d="M4.709 15.955l4.72-2.647.08-.23-.08-.128H9.2l-.79-.048-2.698-.073-2.339-.097-2.266-.122-.571-.121L0 11.784l.055-.352.48-.321.686.06 1.52.103 2.278.158 1.652.097 2.449.255h.389l.055-.157-.134-.098-.103-.097-2.358-1.596-2.552-1.688-1.336-.972-.724-.491-.364-.462-.158-1.008.656-.722.881.06.225.061.893.686 1.908 1.476 2.491 1.833.365.304.145-.103.019-.073-.164-.274-1.355-2.446-1.446-2.49-.644-1.032-.17-.619a2.97 2.97 0 01-.104-.729L6.283.134 6.696 0l.996.134.42.364.62 1.414 1.002 2.229 1.555 3.03.456.898.243.832.091.255h.158V9.01l.128-1.706.237-2.095.23-2.695.08-.76.376-.91.747-.492.584.28.48.685-.067.444-.286 1.851-.559 2.903-.364 1.942h.212l.243-.242.985-1.306 1.652-2.064.73-.82.85-.904.547-.431h1.033l.76 1.129-.34 1.166-1.064 1.347-.881 1.142-1.264 1.7-.79 1.36.073.11.188-.02 2.856-.606 1.543-.28 1.841-.315.833.388.091.395-.328.807-1.969.486-2.309.462-3.439.813-.042.03.049.061 1.549.146.662.036h1.622l3.02.225.79.522.474.638-.079.485-1.215.62-1.64-.389-3.829-.91-1.312-.329h-.182v.11l1.093 1.068 2.006 1.81 2.509 2.33.127.578-.322.455-.34-.049-2.205-1.657-.851-.747-1.926-1.62h-.128v.17l.444.649 2.345 3.521.122 1.08-.17.353-.608.213-.668-.122-1.374-1.925-1.415-2.167-1.143-1.943-.14.08-.674 7.254-.316.37-.729.28-.607-.461-.322-.747.322-1.476.389-1.924.315-1.53.286-1.9.17-.632-.012-.042-.14.018-1.434 1.967-2.18 2.945-1.726 1.845-.414.164-.717-.37.067-.662.401-.589 2.388-3.036 1.44-1.882.93-1.086-.006-.158h-.055L4.132 18.56l-1.13.146-.487-.456.061-.746.231-.243 1.908-1.312-.006.006z" />
  </svg>
)

export const CodexLogo = ({ size = 20, className }: ProviderLogoProps) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width={size}
    height={size}
    viewBox="0 0 24 24"
    role="img"
    aria-label="Codex"
    className={`fill-current ${className ?? ''}`}
  >
    <path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.1419.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z" />
  </svg>
)

/** The coding agent's own mark, for buttons that hand work to it: the button's label names
 * the agent, so the mark stays out of its accessible name. */
export const AgentLogo = ({
  agent,
  size = 16,
  className,
}: ProviderLogoProps & { agent: 'claude' | 'codex' }) => (
  <span
    aria-hidden="true"
    className={`inline-flex shrink-0 ${className ?? ''}`}
  >
    {agent === 'claude' ? (
      <ClaudeLogo size={size} />
    ) : (
      <CodexLogo size={size} />
    )}
  </span>
)

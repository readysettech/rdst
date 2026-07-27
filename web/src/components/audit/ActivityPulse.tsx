export function ActivityPulse({
  label = 'In progress',
  compact = false,
}: {
  label?: string
  compact?: boolean
}) {
  return (
    <output
      aria-label={label}
      className="inline-flex items-center gap-1 shrink-0"
    >
      {[0, 1, 2].map((index) => (
        <span
          key={index}
          className={`${compact ? 'h-1 w-1' : 'h-1.5 w-1.5'} rounded-full bg-content-primary-soft animate-pulse`}
          style={{ animationDelay: `${index * 160}ms` }}
        />
      ))}
    </output>
  )
}

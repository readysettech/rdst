export function ActivityPulse({ label = 'In progress' }: { label?: string }) {
  return (
    <output
      aria-label={label}
      className="inline-flex items-center gap-1 shrink-0"
    >
      {/* Without the pulse, three inert dots read as an overflow menu, so
          reduced motion leaves a single steady status light instead. [E-35] */}
      {[0, 1, 2].map((index) => (
        <span
          key={index}
          className={`h-1.5 w-1.5 rounded-full bg-content-primary-soft animate-pulse motion-reduce:animate-none ${
            index > 0 ? 'motion-reduce:hidden' : ''
          }`}
          style={{ animationDelay: `${index * 160}ms` }}
        />
      ))}
    </output>
  )
}

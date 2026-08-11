const noiseTexture =
  "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' stitchTiles='stitch'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>\")"

export function RisingSurfaceBackdrop() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0">
      <div
        className="absolute inset-0"
        style={{
          background: [
            'radial-gradient(90% 150% at 88% -20%, color-mix(in oklab, var(--color-surface-rising-solid), white 32%) 0%, transparent 58%)',
            'radial-gradient(75% 130% at 8% 115%, color-mix(in oklab, var(--color-surface-rising-solid), white 20%) 0%, transparent 52%)',
            'radial-gradient(65% 120% at 55% 60%, color-mix(in oklab, var(--color-surface-rising-solid), black 45%) 0%, transparent 74%)',
          ].join(', '),
        }}
      />
      <div
        className="absolute inset-0 mix-blend-overlay"
        style={{
          opacity: 0.55,
          backgroundImage: noiseTexture,
          backgroundSize: '160px 160px',
        }}
      />
    </div>
  )
}

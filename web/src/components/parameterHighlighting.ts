export interface ParameterHighlight {
  token: string
  colorIndex: number
}

interface ParameterColor {
  badgeBackground: string
  badgeBorder: string
  badgeText: string
  codeBackground: string
  codeBorder: string
  codeText: string
}

const PARAMETER_COLORS: ParameterColor[] = [
  {
    badgeBackground: 'rgba(56, 189, 248, 0.16)',
    badgeBorder: 'rgba(56, 189, 248, 0.45)',
    badgeText: '#7dd3fc',
    codeBackground: 'rgba(56, 189, 248, 0.2)',
    codeBorder: 'rgba(56, 189, 248, 0.6)',
    codeText: '#bae6fd',
  },
  {
    badgeBackground: 'rgba(34, 197, 94, 0.16)',
    badgeBorder: 'rgba(34, 197, 94, 0.45)',
    badgeText: '#86efac',
    codeBackground: 'rgba(34, 197, 94, 0.2)',
    codeBorder: 'rgba(34, 197, 94, 0.6)',
    codeText: '#bbf7d0',
  },
  {
    badgeBackground: 'rgba(251, 191, 36, 0.16)',
    badgeBorder: 'rgba(251, 191, 36, 0.45)',
    badgeText: '#fcd34d',
    codeBackground: 'rgba(251, 191, 36, 0.2)',
    codeBorder: 'rgba(251, 191, 36, 0.6)',
    codeText: '#fde68a',
  },
  {
    badgeBackground: 'rgba(244, 114, 182, 0.16)',
    badgeBorder: 'rgba(244, 114, 182, 0.45)',
    badgeText: '#f9a8d4',
    codeBackground: 'rgba(244, 114, 182, 0.2)',
    codeBorder: 'rgba(244, 114, 182, 0.6)',
    codeText: '#fbcfe8',
  },
  {
    badgeBackground: 'rgba(167, 139, 250, 0.16)',
    badgeBorder: 'rgba(167, 139, 250, 0.45)',
    badgeText: '#c4b5fd',
    codeBackground: 'rgba(167, 139, 250, 0.2)',
    codeBorder: 'rgba(167, 139, 250, 0.6)',
    codeText: '#ddd6fe',
  },
  {
    badgeBackground: 'rgba(45, 212, 191, 0.16)',
    badgeBorder: 'rgba(45, 212, 191, 0.45)',
    badgeText: '#5eead4',
    codeBackground: 'rgba(45, 212, 191, 0.2)',
    codeBorder: 'rgba(45, 212, 191, 0.6)',
    codeText: '#99f6e4',
  },
]

export const PARAMETER_HIGHLIGHT_COLOR_COUNT = PARAMETER_COLORS.length

export function buildParameterHighlights(
  placeholders: string[]
): ParameterHighlight[] {
  const colorByPlaceholder = new Map<string, number>()
  let nextColor = 0

  for (const placeholder of placeholders) {
    if (!colorByPlaceholder.has(placeholder)) {
      colorByPlaceholder.set(placeholder, nextColor)
      nextColor++
    }
  }

  return Array.from(colorByPlaceholder.entries()).map(
    ([token, colorIndex]) => ({
      token,
      colorIndex,
    })
  )
}

export function getParameterColor(colorIndex: number): ParameterColor {
  return PARAMETER_COLORS[colorIndex % PARAMETER_COLORS.length]
}

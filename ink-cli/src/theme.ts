export const theme = {
  // Backgrounds
  bg: '#0a0a0a',
  bgPanel: '#111111',
  bgHover: '#1a1a1a',
  bgInput: '#0f0f0f',

  // Borders
  border: '#2a2a2a',
  borderActive: '#3f3f46',

  // Text
  text: '#ededed',
  textMuted: '#a1a1aa',
  textDim: '#71717a',
  textBright: '#ffffff',

  // Accent (purple/violet — AI branding)
  accent: '#8b5cf6',
  accentBright: '#a78bfa',
  accentDim: '#6d28d9',

  // Semantic colors
  green: '#10b981',
  greenDim: '#065f46',
  yellow: '#f59e0b',
  yellowDim: '#92400e',
  red: '#ef4444',
  redDim: '#7f1d1d',
  blue: '#3b82f6',
  cyan: '#06b6d4',
  orange: '#f97316',

  // Monokai code highlight colors
  codeString: '#e6db74',
  codeKeyword: '#66d9e8',
  codeFunction: '#a6e22e',
  codeComment: '#75715e',
  codeNumber: '#ae81ff',
  codeOperator: '#f92672',
} as const;

export type Theme = typeof theme;

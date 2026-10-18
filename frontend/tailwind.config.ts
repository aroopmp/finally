import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0d1117',
        panel: '#161b22',
        panel2: '#1c2330',
        line: '#30363d',
        muted: '#8b949e',
        text: '#e6edf3',
        accent: '#ecad0a',
        primary: '#209dd7',
        secondary: '#753991',
        up: '#3fb950',
        down: '#f85149',
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
};

export default config;

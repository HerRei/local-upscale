import type { KnipConfig } from 'knip';

const SCRIPT = /<script\b[^>]*>([\s\S]*?)<\/script>/g;
const STYLE = /<style\b[^>]*>[\s\S]*?<\/style>/g;
const MEMBER = /(?<![\w$.])([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)/g;
const RESERVED = new Set(['case', 'default', 'do', 'else', 'in', 'instanceof', 'return', 'typeof']);

// Knip's default Svelte compiler keeps only import statements, so members of
// `import * as api` used by a component would look unused. Analyse complete
// component scripts plus the member accesses in markup (for example
// `on:click={() => api.openResult(path)}`). Component props (`export let`)
// are consumed by parents through markup, so .svelte exports are not reported.
function svelte(text: string): string {
  const scripts = [...text.matchAll(SCRIPT)].map((match) => match[1]);
  const markup = text.replace(SCRIPT, '').replace(STYLE, '');
  const members = [...markup.matchAll(MEMBER)]
    .filter((match) => !RESERVED.has(match[1]))
    .map((match) => `void ${match[1]}.${match[2]};`);
  return [...scripts, ...members].join('\n');
}

const config: KnipConfig = {
  compilers: { svelte },
  // Shared types and defaults composed inside their own module stay exported.
  ignoreExportsUsedInFile: true,
  ignoreIssues: {
    'src/**/*.svelte': ['exports'],
  },
};

export default config;

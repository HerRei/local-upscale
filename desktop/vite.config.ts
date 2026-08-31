import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { svelteTesting } from '@testing-library/svelte/vite';

const host = process.env.TAURI_DEV_HOST;

export default defineConfig({
  plugins: [
    svelte(),
    svelteTesting(),
    {
      name: 'localsr-classic-webview-entry',
      apply: 'build',
      enforce: 'post',
      transformIndexHtml(html) {
        return html.replace(/<script type="module"([^>]*)>/g, '<script defer$1>');
      }
    }
  ],
  clearScreen: false,
  server: {
    host: host || false,
    port: 1420,
    strictPort: true,
    hmr: host ? { protocol: 'ws', host, port: 1421 } : undefined,
    watch: { ignored: ['**/src-tauri/**'] }
  },
  envPrefix: ['VITE_', 'TAURI_ENV_*'],
  build: {
    // LocalSR's native engine already targets macOS 12+; Safari 15 is the
    // matching system WebKit baseline and avoids shipping an unnecessary
    // legacy destructuring transform inside the desktop bundle.
    target: process.env.TAURI_ENV_PLATFORM === 'windows' ? 'chrome105' : 'safari15',
    minify: process.env.TAURI_ENV_DEBUG ? false : 'esbuild',
    sourcemap: Boolean(process.env.TAURI_ENV_DEBUG),
    modulePreload: false,
    rollupOptions: {
      output: {
        format: 'iife'
      }
    }
  }
});

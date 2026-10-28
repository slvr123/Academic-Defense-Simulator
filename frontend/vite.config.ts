import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { loadEnv, type Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

/** M10 N12: index.html's share tags need absolute URLs; %SITE_URL% becomes VITE_SITE_URL
 * (set in render.yaml). Without it the tags fall back to root-relative paths, which
 * LinkedIn and others won't read, so a build says so. */
function siteUrl(mode: string): Plugin {
  const url = (loadEnv(mode, process.cwd(), 'VITE_').VITE_SITE_URL ?? '').replace(/\/+$/, '')
  return {
    name: 'ads-site-url',
    transformIndexHtml(html, context) {
      if (url === '' && context.server === undefined) console.warn('VITE_SITE_URL is not set: share tags use relative URLs')
      return html.replaceAll('%SITE_URL%', url)
    },
  }
}

// https://vite.dev/config/ and https://vitest.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss(), siteUrl(mode)],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // The tests set their own base URL; a developer's .env must not leak into them.
    env: { VITE_API_BASE_URL: 'http://api.test' },
  },
}))

/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

/**
 * Preload the two Latin variable fonts so the first paint uses the real faces.
 * Live numerals would otherwise jump when the fallback font is swapped out.
 */
function preloadFonts(): Plugin {
  return {
    name: 'paul:preload-fonts',
    apply: 'build',
    transformIndexHtml: {
      order: 'post',
      handler(_html, ctx) {
        const fonts = Object.keys(ctx.bundle ?? {}).filter((file) =>
          /atkinson-hyperlegible-(next|mono)-latin-wght-normal[^/]*\.woff2$/.test(file),
        )
        return fonts.map((file) => ({
          tag: 'link',
          attrs: { rel: 'preload', as: 'font', type: 'font/woff2', href: `./${file}`, crossorigin: '' },
          injectTo: 'head-prepend' as const,
        }))
      },
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  // Relative asset URLs: the build can be opened from any folder on any static host.
  base: './',
  plugins: [react(), tailwindcss(), preloadFonts()],
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})

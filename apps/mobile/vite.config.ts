import { readFileSync } from 'node:fs'
import basicSsl from '@vitejs/plugin-basic-ssl'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd())
  if (!env.VITE_NEON_AUTH_URL) throw new Error('Missing VITE_NEON_AUTH_URL; see apps/mobile/.env')
  const authUrl = new URL(env.VITE_NEON_AUTH_URL)
  const { version } = JSON.parse(readFileSync('package.json', 'utf8')) as { version: string }

  return {
    define: { __APP_VERSION__: JSON.stringify(version) },
    // Self-signed HTTPS so phones on the local network get a secure context,
    // which Neon Auth's Secure session cookie requires.
    plugins: [react(), basicSsl()],
    server: {
      host: true, // Listen on the local network, not just localhost.
      proxy: {
        // Dev only: serve Neon Auth from the app's own origin so its session
        // cookie is first-party in any browser, and present the trusted origin
        // Neon Auth expects (see src/neon.ts).
        '/neon-auth': {
          target: authUrl.origin,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/neon-auth/, authUrl.pathname),
          headers: { origin: env.VITE_NEON_AUTH_TRUSTED_ORIGIN },
        },
      },
    },
  }
})

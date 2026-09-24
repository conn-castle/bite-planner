import { readFileSync } from 'node:fs'
import basicSsl from '@vitejs/plugin-basic-ssl'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const { version } = JSON.parse(readFileSync('package.json', 'utf8')) as { version: string }
  // Release this build belongs to (`r<N>`), set by the release workflow; `dev` for local builds.
  const release = process.env.CASTLE_CHEF_RELEASE ?? 'dev'

  return {
    define: { __APP_VERSION__: JSON.stringify(version), __APP_RELEASE__: JSON.stringify(release) },
    // Self-signed HTTPS so phones on the local network get a secure context,
    // which Neon Auth's Secure session cookie requires.
    plugins: [react(), basicSsl()],
    // Unit tests (mode `test`) load no Neon branch, so they get no dev server.
    server: mode === 'test' ? undefined : devServer(loadEnv(mode, process.cwd())),
  }
})

function devServer(env: Record<string, string>) {
  if (!env.VITE_NEON_AUTH_URL) {
    throw new Error('Missing VITE_NEON_AUTH_URL; see apps/mobile/.env.development')
  }
  const authUrl = new URL(env.VITE_NEON_AUTH_URL)
  return {
    host: true, // Listen on the local network, not just localhost.
    proxy: {
      // The Castle Chef API, run locally with `uv run uvicorn castle_chef.api:app`
      // (backend/). Same origin as the app, so no CORS in development.
      '/api': {
        target: process.env.CASTLE_CHEF_API_DEV_URL ?? 'http://localhost:8000',
        rewrite: (path: string) => path.replace(/^\/api/, ''),
      },
      // Dev only: serve Neon Auth from the app's own origin so its session
      // cookie is first-party in any browser, and present the trusted origin
      // Neon Auth expects (see src/neonAuth.ts).
      '/neon-auth': {
        target: authUrl.origin,
        changeOrigin: true,
        rewrite: (path: string) => path.replace(/^\/neon-auth/, authUrl.pathname),
        headers: { origin: env.VITE_NEON_AUTH_TRUSTED_ORIGIN },
      },
    },
  }
}

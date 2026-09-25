import { readFileSync } from 'node:fs'
import basicSsl from '@vitejs/plugin-basic-ssl'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const { version } = JSON.parse(readFileSync('package.json', 'utf8')) as { version: string }
  // Release this build belongs to (`YYYY.M.D.N`), set by the release workflow; `dev` for local builds.
  const release = process.env.CASTLE_CHEF_RELEASE ?? 'dev'
  // `make review` (scripts/review.sh): the review server in front of the dev server
  // provides HTTPS on the LAN at this port, so the dev server itself stays on loopback HTTP.
  const reviewPort = process.env.CASTLE_CHEF_REVIEW_PORT

  return {
    define: { __APP_VERSION__: JSON.stringify(version), __APP_RELEASE__: JSON.stringify(release) },
    // Self-signed HTTPS so phones on the local network get a secure context,
    // which Neon Auth's Secure session cookie requires.
    plugins: [react(), ...(reviewPort ? [] : [basicSsl()])],
    // Unit tests (mode `test`) load no Neon branch, so they get no dev server.
    server: mode === 'test' ? undefined : devServer(loadEnv(mode, process.cwd()), reviewPort),
  }
})

function devServer(env: Record<string, string>, reviewPort: string | undefined) {
  if (!env.VITE_NEON_AUTH_URL) {
    throw new Error('Missing VITE_NEON_AUTH_URL; see apps/mobile/.env.development')
  }
  const authUrl = new URL(env.VITE_NEON_AUTH_URL)
  return {
    // Listen on the local network, or only on loopback behind the review server,
    // whose HTTPS address hot reload then connects through.
    host: reviewPort ? '127.0.0.1' : true,
    hmr: reviewPort ? { protocol: 'wss', clientPort: Number(reviewPort) } : undefined,
    proxy: {
      // The Castle Chef API, run locally with `uv run uvicorn castle_chef.api:app`
      // (backend/). Same origin as the app, so no CORS in development.
      // Set CASTLE_CHEF_API_DEV_URL to use a deployed API (e.g. the AWS stack on dev).
      '/api': {
        target: process.env.CASTLE_CHEF_API_DEV_URL ?? 'http://localhost:8000',
        changeOrigin: true, // a Lambda Function URL routes by its own Host header
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

/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_NEON_AUTH_URL: string
  readonly VITE_NEON_AUTH_TRUSTED_ORIGIN: string
  readonly VITE_API_URL: string
}

/** package.json version, injected by vite.config.ts. Also the persisted cache's buster. */
declare const __APP_VERSION__: string
/** Release this build belongs to (`r<N>`), or `dev`; injected by vite.config.ts. */
declare const __APP_RELEASE__: string

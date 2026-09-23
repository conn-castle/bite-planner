/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_NEON_AUTH_URL: string
  readonly VITE_NEON_DATA_API_URL: string
  readonly VITE_NEON_AUTH_TRUSTED_ORIGIN: string
}

/** package.json version, injected by vite.config.ts. */
declare const __APP_VERSION__: string

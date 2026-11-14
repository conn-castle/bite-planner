import { neonAuth } from './neonAuth'

/**
 * Sign-in, behind an interface so the provider can be replaced. The app only
 * uses these calls; `lib/api/client.ts` sends `getToken()` to the Bite Planner API, which
 * verifies it with the matching backend verifier (bite_planner.auth).
 */

export interface User {
  id: string
  email: string
  name: string
}

export interface AuthClient {
  /** Email a one-time sign-in code. */
  sendCode(email: string): Promise<void>
  /** Sign in with the emailed code. A wrong or expired code throws an error saying so. */
  verifyCode(email: string, code: string): Promise<void>
  /** The signed-in user, or null. */
  getUser(): Promise<User | null>
  /** A current bearer token for the API, or null when signed out. */
  getToken(): Promise<string | null>
  /** Changes the signed-in user's own name (kept by the provider, the one place it's stored). */
  updateName(name: string): Promise<void>
  signOut(): Promise<void>
}

/** The sign-in provider in use. */
export const auth: AuthClient = neonAuth

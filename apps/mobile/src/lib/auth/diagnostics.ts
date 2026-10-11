import type { AuthDiagnosticIn } from '../api/types'
import { errorFields, logger } from '../log'

/** Send the small auth trace while a new user cannot upload normal app logs. */
export async function reportAuth(
  email: string,
  event: AuthDiagnosticIn['event'],
  failure?: unknown,
): Promise<void> {
  const fields = failure === undefined ? {} : errorFields(failure)
  const body: AuthDiagnosticIn = {
    email,
    event,
    ...(typeof fields.status === 'number' ? { status: fields.status } : {}),
    ...(typeof fields.code === 'string' ? { code: fields.code.slice(0, 80) } : {}),
    ...(typeof fields.error === 'string' ? { error: fields.error.slice(0, 200) } : {}),
  }
  try {
    const base = import.meta.env.VITE_API_URL
    if (!base) throw new Error('Missing VITE_API_URL.')
    const response = await fetch(`${base.replace(/\/+$/, '')}/v1/auth/diagnostics`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(3000),
    })
    if (!response.ok) throw new Error(`Auth diagnostics failed (${response.status}).`)
  } catch (error) {
    // The device's saved outbox still has the original auth event for later upload.
    logger.warn('auth.diagnostics_failed', errorFields(error))
  }
}

import { useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useRef, useState } from 'react'
import { auth } from '../../lib/auth/auth'
import { reportAuth } from '../../lib/auth/diagnostics'
import { errorMessage } from '../../lib/errorMessage'
import { haptics } from '../../lib/haptics'
import { isRecord } from '../../lib/isRecord'
import { errorFields, logger } from '../../lib/log'
import { Button } from '../../ui/Button'
import { Notice } from '../../ui/Notice'
import { CODE_LENGTH, CodeInput } from './CodeInput'
import { afterSignIn } from './session'
import styles from './SignInPage.module.css'
import { Welcome } from './Welcome'

function describeError(error: unknown): string {
  return (isRecord(error) && 'message' in error && errorMessage(error)) || 'Something went wrong. Try again.'
}

/**
 * Passwordless sign-in: an email, then the code Neon Auth emails to it. Only
 * accounts invited to a household get past this (see NotInHousehold).
 */
export function SignInPage() {
  const queryClient = useQueryClient()
  const [step, setStep] = useState<'email' | 'code'>('email')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const verifying = useRef(false)

  async function sendCode(event?: FormEvent) {
    event?.preventDefault()
    const address = email.trim()
    setBusy(true)
    setError(null)
    try {
      await auth.sendCode(address)
      logger.info('auth.code_sent')
      void reportAuth(address, 'code_sent')
      setCode('')
      setStep('code')
    } catch (error) {
      logger.warn('auth.code_sent_failed', errorFields(error))
      await reportAuth(address, 'code_send_failed', error)
      haptics.error()
      setError(describeError(error))
    } finally {
      setBusy(false)
    }
  }

  async function verify(value: string) {
    // Autofill completes the code and a tap on Continue can follow: a code works once.
    if (verifying.current) return
    verifying.current = true
    setBusy(true)
    setError(null)
    const address = email.trim()
    let verified = false
    try {
      await auth.verifyCode(email.trim(), value)
      verified = true
      logger.info('auth.code_verified')
      void reportAuth(address, 'code_verified')
      haptics.success()
      await afterSignIn(queryClient)
      logger.info('auth.signed_in')
    } catch (error) {
      logger.warn(verified ? 'auth.session_failed' : 'auth.signed_in_failed', errorFields(error))
      await reportAuth(address, verified ? 'session_failed' : 'verify_failed', error)
      haptics.error()
      setError(describeError(error))
    } finally {
      setBusy(false)
      verifying.current = false
    }
  }

  if (step === 'email') {
    return (
      <Welcome title="Sign in to Castle Chef" subtitle="Enter your email and we'll send you a sign-in code.">
        <form className={styles.form} onSubmit={(event) => void sendCode(event)}>
          <label className={styles.label}>
            Email
            <input
              className={styles.field}
              type="email"
              placeholder="name@example.com"
              autoComplete="email"
              autoCapitalize="none"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>
          {error && <Notice>{error}</Notice>}
          <Button type="submit" block disabled={busy || !email.trim()}>
            {busy ? 'Sending…' : 'Continue'}
          </Button>
        </form>
      </Welcome>
    )
  }

  return (
    <Welcome
      title="Enter the code"
      subtitle={
        <>
          We sent a {CODE_LENGTH}-digit code to <strong>{email.trim()}</strong>. It may take a
          minute, and may land in spam.
        </>
      }
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault()
          void verify(code)
        }}
      >
        <CodeInput value={code} onChange={setCode} onComplete={(value) => void verify(value)} disabled={busy} />
        {error && <Notice>{error}</Notice>}
        <Button type="submit" block disabled={busy || code.length !== CODE_LENGTH}>
          {busy ? 'Signing in…' : 'Continue'}
        </Button>
      </form>
      <div className={styles.links}>
        <Button variant="plain" disabled={busy} onClick={() => void sendCode()}>
          Resend code
        </Button>
        <Button
          variant="plain"
          disabled={busy}
          onClick={() => {
            setStep('email')
            setError(null)
          }}
        >
          Change email
        </Button>
      </div>
    </Welcome>
  )
}

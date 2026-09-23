import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { errorFields, logger } from './log'
import { neon } from './neon'
import { afterSignIn } from './queries'

function describeError(error: unknown): string {
  const message = (error as { message?: string } | null)?.message
  if (message === 'Invalid OTP') return 'That code is wrong or has expired. Send a new one.'
  return message || 'Something went wrong. Try again.'
}

/**
 * Passwordless sign-in: enter your email, then the code Neon Auth emails you.
 * The app is invite-only (`castle-chef family invite`); accounts that were not
 * invited see nothing (see Layout.tsx).
 */
export function SignIn() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [step, setStep] = useState<'email' | 'code'>('email')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // neon-js throws on HTTP errors and returns `error` for others; handle both.
  async function run(
    event: string,
    action: () => Promise<{ error?: unknown }>,
    then: () => void | Promise<void>,
  ) {
    setBusy(true)
    setError(null)
    try {
      const result = await action()
      if (result.error) throw result.error
      logger.info(event)
      await then()
    } catch (e) {
      logger.warn(`${event}_failed`, errorFields(e))
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }

  function sendCode() {
    void run(
      'auth.code_sent',
      () => neon.auth.emailOtp.sendVerificationOtp({ email, type: 'sign-in' }),
      () => {
        setCode('')
        setStep('code')
      },
    )
  }

  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (step === 'email') sendCode()
    else
      void run(
        'auth.signed_in',
        () => neon.auth.signIn.emailOtp({ email, otp: code }),
        async () => {
          await afterSignIn(queryClient)
          await navigate('/')
        },
      )
  }

  return (
    <form onSubmit={submit} className="card stack">
      <h3>Sign in</h3>
      {step === 'email' ? (
        <>
          <p className="muted">Enter your email and we'll send you a sign-in code.</p>
          <input
            type="email"
            placeholder="Email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </>
      ) : (
        <>
          <p className="muted">
            We sent a 6-digit code to {email} from Neon Auth (check spam). It expires in a few
            minutes.
          </p>
          <input
            placeholder="6-digit code"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => setCode(e.target.value.trim())}
            required
          />
        </>
      )}
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy || (step === 'email' ? !email : !code)}>
        {step === 'email' ? 'Email me a code' : 'Sign in'}
      </button>
      {step === 'code' && (
        <>
          <button type="button" className="link" disabled={busy} onClick={sendCode}>
            Send a new code
          </button>
          <button
            type="button"
            className="link"
            onClick={() => {
              setStep('email')
              setError(null)
            }}
          >
            Use a different email
          </button>
        </>
      )}
    </form>
  )
}

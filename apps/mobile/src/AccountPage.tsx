import { Link } from 'react-router'
import { SignOutButton } from './SignOutButton'
import { useSendLogs, useSession } from './queries'

export function AccountPage() {
  const { data: session } = useSession()
  const sendLogs = useSendLogs()

  return (
    <>
      <Link to="/" className="link">
        ← Requests
      </Link>
      <div className="card stack">
        <h3>{session?.user.name}</h3>
        <p className="muted">{session?.user.email}</p>
        <SignOutButton />
      </div>

      <div className="card stack">
        <h3>Having a problem?</h3>
        <p className="muted">
          Send the app's recent logs to whoever runs Castle Chef. They include what the app did,
          not your meals or sign-in codes.
        </p>
        <button onClick={() => sendLogs.mutate()} disabled={sendLogs.isPending}>
          {sendLogs.isPending ? 'Sending…' : 'Send logs'}
        </button>
        {sendLogs.isSuccess && <p className="muted">Logs sent. Thanks!</p>}
        {sendLogs.isError && <p className="error">{sendLogs.error.message}</p>}
      </div>

      <p className="muted version">Castle Chef {__APP_VERSION__}</p>
    </>
  )
}

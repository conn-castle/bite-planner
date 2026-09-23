import { onlineManager } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import { NavLink, Outlet } from 'react-router'
import { SignOutButton } from './SignOutButton'
import { useSession } from './queries'
import { SignIn } from './SignIn'

/** App frame: decides between sign-in, "not invited", and the app itself. */
export function Layout() {
  const { data: session, isPending, error, refetch } = useSession()
  const online = useSyncExternalStore(onlineManager.subscribe, () => onlineManager.isOnline())

  return (
    <main>
      <header>
        <NavLink to="/" className="title">
          <h1>Castle Chef</h1>
        </NavLink>
        {session && (
          <NavLink to="/account" className="link">
            Account
          </NavLink>
        )}
      </header>
      {!online && <p className="offline">You're offline. Showing saved data.</p>}
      {isPending ? (
        <div className="skeleton card" aria-label="Loading" />
      ) : error && !session ? (
        <div className="card stack">
          <p className="error">Could not check your sign-in: {error.message}</p>
          <button onClick={() => void refetch()}>Try again</button>
        </div>
      ) : !session ? (
        <SignIn />
      ) : !session.member ? (
        <div className="card stack">
          <p>
            {session.user.email} isn't part of this household. Ask whoever runs Castle Chef to
            invite you.
          </p>
          <SignOutButton />
        </div>
      ) : (
        <Outlet />
      )}
    </main>
  )
}

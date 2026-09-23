import { Component, type ErrorInfo, type ReactNode } from 'react'
import { errorFields, logger } from './log'

/** Shows a recovery screen instead of a blank page when rendering fails, and logs why. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    logger.error('render.crashed', { ...errorFields(error), componentStack: info.componentStack })
  }

  render() {
    if (!this.state.failed) return this.props.children
    return <CrashScreen />
  }
}

export function CrashScreen() {
  return (
    <main>
      <h1>Castle Chef</h1>
      <div className="card stack">
        <p>Something went wrong. It has been logged.</p>
        <button onClick={() => window.location.reload()}>Reload</button>
      </div>
    </main>
  )
}

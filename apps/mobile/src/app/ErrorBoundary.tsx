import { Component, type ErrorInfo, type ReactNode } from 'react'
import { errorFields, logger } from '../lib/log'
import { Button } from '../ui/Button'
import { Welcome } from '../features/session/Welcome'

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
    return (
      <Welcome
        title="Something went wrong"
        subtitle="The problem has been logged. Reloading usually fixes it."
      >
        <Button block onClick={() => window.location.reload()}>
          Reload
        </Button>
      </Welcome>
    )
  }
}

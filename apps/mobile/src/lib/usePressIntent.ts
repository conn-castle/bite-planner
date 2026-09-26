import { useEffect, useRef } from 'react'

// How long a finger rests on something before it counts as pressing it, not scrolling past.
const INTENT_MS = 80

/**
 * Pointer handlers that call `onIntent` once a press is held briefly: e.g. to
 * start loading what a row opens before the tap ends. A touch that turns into
 * a scroll (the browser cancels the pointer) calls nothing.
 */
export function usePressIntent(onIntent: () => void) {
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const stop = () => clearTimeout(timer.current)
  useEffect(() => stop, [])
  return {
    onPointerDown: () => {
      stop()
      timer.current = setTimeout(onIntent, INTENT_MS)
    },
    onPointerCancel: stop,
    onPointerLeave: stop,
  }
}

import { useEffect, useState } from 'react'
import type { ISODate } from './api/types'
import { localDate } from './dates'

/** The current time, updated every `intervalMs` while the component is mounted. */
export function useNow(intervalMs: number): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
  return now
}

// How often useToday checks whether the phone's calendar day has changed.
const DAY_CHECK_MS = 60_000

/**
 * Today in the phone's time zone, kept current while the component is mounted
 * (so a screen left open moves on at midnight). It re-renders only when the day changes.
 */
export function useToday(): ISODate {
  const [today, setToday] = useState(() => localDate(new Date()))
  useEffect(() => {
    const timer = setInterval(() => setToday(localDate(new Date())), DAY_CHECK_MS)
    return () => clearInterval(timer)
  }, [])
  return today
}

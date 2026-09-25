import { useMemo } from 'react'
import { EmptyState } from '../../ui/EmptyState'
import { Page } from '../../ui/Page'
import { PlateArt } from '../../ui/art'
import { daysOfWeek, formatWeekRange, isSameDay } from './week'
import styles from './WeekPage.module.css'

/** This week's plan. Planning doesn't exist yet, so the week is always empty for now. */
export function WeekPage() {
  const today = useMemo(() => new Date(), [])
  const days = useMemo(() => daysOfWeek(today), [today])

  return (
    <Page title="This week" largeTitle>
      <p className={styles.range}>{formatWeekRange(days)}</p>
      <ol className={styles.days} aria-label="This week">
        {days.map((day) => {
          const isToday = isSameDay(day, today)
          return (
            <li
              key={day.toISOString()}
              className={`${styles.day} ${isToday ? styles.today : ''} ${day < today && !isToday ? styles.past : ''}`}
              aria-current={isToday ? 'date' : undefined}
            >
              <span className={styles.weekday}>
                {day.toLocaleDateString(undefined, { weekday: 'narrow' })}
              </span>
              <span className={styles.date}>{day.getDate()}</span>
            </li>
          )
        })}
      </ol>
      <EmptyState art={<PlateArt />} title="Nothing planned yet">
        Dinners for the week will show up here.
      </EmptyState>
    </Page>
  )
}

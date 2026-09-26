import { useEffect, useRef, useState } from 'react'
import type { MealRating } from '../lib/api/types'
import { Icon, type IconName } from './Icon'
import styles from './Rating.module.css'

/** The three ratings a person gives a meal they've had ("not for us" is the ✕ while picking). */
type Choice = Exclude<MealRating, 'not_for_us'>

const CHOICES: { rating: Choice; icon: IconName; label: string }[] = [
  { rating: 'down', icon: 'down', label: "Didn't like" },
  { rating: 'up', icon: 'up', label: 'Liked' },
  { rating: 'favorite', icon: 'heart', label: 'Favorite' },
]

// "Not for us" on a meal that was ordered anyway (it lost a race with placing
// the order) reads as didn't like.
const choice = (rating: MealRating | null) =>
  CHOICES.find((each) => each.rating === (rating === 'not_for_us' ? 'down' : rating))

interface RatingChoicesProps {
  value: MealRating | null
  /** Null when the chosen one is tapped again: back to not rated. */
  onChange: (rating: Choice | null) => void
  /** What's rated, for screen readers, e.g. the meal's name. */
  label: string
  className?: string
}

/** Thumbs down, thumbs up, and the heart: one choice of three, lit in its color; tapping it again clears it. */
export function RatingChoices({ value, onChange, label, className }: RatingChoicesProps) {
  const chosen = choice(value)?.rating
  return (
    <span className={`${styles.choices} ${className ?? ''}`} role="group" aria-label={`Rate ${label}`}>
      {CHOICES.map(({ rating, icon, label: name }) => (
        <button
          key={rating}
          type="button"
          className={`${styles.choice} ${styles[rating]} ${chosen === rating ? styles.on : ''} cc-hit`}
          aria-pressed={chosen === rating}
          aria-label={name}
          onClick={() => onChange(chosen === rating ? null : rating)}
        >
          <Icon name={icon} />
        </button>
      ))}
    </span>
  )
}

interface RatingButtonProps {
  value: MealRating | null
  onChange: (rating: Choice | null) => void
  label: string
}

/**
 * One round button showing the rating ("?" before there is one). Tapped, it
 * grows sideways into the three choices; choosing one, or tapping anywhere
 * else, shrinks it back.
 */
export function RatingButton({ value, onChange, label }: RatingButtonProps) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLSpanElement>(null)
  const shown = choice(value)

  useEffect(() => {
    if (!open) return
    const close = (event: PointerEvent) => {
      if (!(event.target instanceof Node && root.current?.contains(event.target))) setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [open])

  return (
    <span ref={root} className={`${styles.button} ${open ? styles.open : ''}`}>
      {open ? (
        <RatingChoices
          value={value}
          label={label}
          onChange={(rating) => {
            onChange(rating)
            setOpen(false)
          }}
        />
      ) : (
        <button
          type="button"
          className={`${styles.current} ${shown ? `${styles[shown.rating]} ${styles.on}` : ''} cc-hit`}
          aria-label={shown ? `${shown.label}. Change rating` : `Rate ${label}`}
          aria-expanded={false}
          onClick={() => setOpen(true)}
        >
          {shown ? <Icon name={shown.icon} /> : <span className={styles.unrated}>?</span>}
        </button>
      )}
    </span>
  )
}

/** A meal's rating as a small mark in a row, or nothing when it isn't rated. */
export function RatingMark({ rating }: { rating: MealRating | null }) {
  const shown = choice(rating)
  if (!shown) return null
  return (
    <span className={`${styles.mark} ${styles[shown.rating]}`} role="img" aria-label={shown.label}>
      <Icon name={shown.icon} />
    </span>
  )
}

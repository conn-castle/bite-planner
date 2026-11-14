import { type AnimationEvent, useEffect, useRef, useState } from 'react'
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
  onAnimationEnd?: (event: AnimationEvent<HTMLSpanElement>) => void
}

/** Thumbs down, thumbs up, and the heart: one choice of three, lit in its color; tapping it again clears it. */
export function RatingChoices({ value, onChange, label, className, onAnimationEnd }: RatingChoicesProps) {
  const chosen = choice(value)?.rating
  return (
    <span
      className={`${styles.choices} ${className ?? ''}`}
      role="group"
      aria-label={`Rate ${label}`}
      onAnimationEnd={onAnimationEnd}
    >
      {CHOICES.map(({ rating, icon, label: name }) => (
        <button
          key={rating}
          type="button"
          className={`${styles.choice} ${styles[rating]} ${chosen === rating ? styles.on : ''} bp-hit`}
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

type Stage = 'closed' | 'open' | 'closing'

/** Where closing starts: its animation, or closed at once when motion is reduced (there's no animation to wait for). */
const closing = (): Stage => (window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'closed' : 'closing')

/**
 * One round button showing the rating ("?" before there is one). Tapped, it
 * grows sideways into the three choices. Choosing one, or tapping anywhere
 * else, closes it: the button (with the new rating) is back at once, and the
 * choices shrink into it over the top, the opening played backwards.
 */
export function RatingButton({ value, onChange, label }: RatingButtonProps) {
  const [stage, setStage] = useState<Stage>('closed')
  const root = useRef<HTMLSpanElement>(null)
  const shown = choice(value)

  useEffect(() => {
    if (stage !== 'open') return
    const closeOutside = (event: PointerEvent) => {
      if (!(event.target instanceof Node && root.current?.contains(event.target))) setStage(closing())
    }
    document.addEventListener('pointerdown', closeOutside)
    return () => document.removeEventListener('pointerdown', closeOutside)
  }, [stage])

  return (
    <span ref={root} className={styles.button}>
      {stage !== 'open' && (
        <button
          type="button"
          className={`${styles.current} ${shown ? `${styles[shown.rating]} ${styles.on}` : ''} bp-hit`}
          aria-label={shown ? `${shown.label}. Change rating` : `Rate ${label}`}
          aria-expanded={false}
          onClick={() => setStage('open')}
        >
          {shown ? <Icon name={shown.icon} /> : <span className={styles.unrated}>?</span>}
        </button>
      )}
      {stage !== 'closed' && (
        <RatingChoices
          value={value}
          label={label}
          className={`${styles.pill} ${stage === 'closing' ? styles.closing : ''}`}
          // The pill's own animation ending, not its icons'.
          onAnimationEnd={(event) => event.target === event.currentTarget && stage === 'closing' && setStage('closed')}
          onChange={(rating) => {
            onChange(rating)
            setStage(closing())
          }}
        />
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

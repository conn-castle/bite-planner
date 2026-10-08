import { type ReactNode, useId } from 'react'
import type { MealDish, MealIcon, MealPicture, MealSummary, PicturedIngredient } from '../lib/api/types'
import styles from './art.module.css'
import { MEAL_ICONS } from './mealIcons'
import { INK, MEAL_COLORS, mix, pictureParts, pictureTint, seededRandom } from './mealPicture'

/** The app's mark and illustrations: one line weight, ink on warm fills. */

export function BrandMark({ size = 64 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="Castle Chef">
      <defs>
        <linearGradient id="cc-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#f47a4d" />
          <stop offset="1" stopColor="#e2461f" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="18" fill="url(#cc-mark)" />
      {/* A chef's toque whose band is a castle wall. */}
      <path
        d="M21 31.5c-4.6 0-7.2-5.6-3.9-9.1-.6-5.4 5.3-8.9 9.8-6.1 2.2-4.7 8-4.7 10.2 0 4.5-2.8 10.4.7 9.8 6.1 3.3 3.5.7 9.1-3.9 9.1z"
        fill="#fff"
      />
      <path d="M21 50V35h4v-2.5h4.2V35h5.6v-2.5H39V35h4v15z" fill="#fff" />
    </svg>
  )
}

/** A neutral plate: a meal from before pictures, and the art of the agent's waits. */
export function PlateArt() {
  return (
    <svg viewBox="0 0 64 64" fill="none" aria-hidden="true">
      <circle cx="32" cy="32" r="24" fill="var(--cc-bg)" stroke="var(--cc-hairline)" strokeWidth="1" />
      <circle cx="32" cy="32" r="16" stroke="var(--cc-fill-strong)" strokeWidth="1.5" />
    </svg>
  )
}

/**
 * A meal's picture (reviewed 2026-10-07): its main ingredient large and in front, on
 * its bed, with two sides behind it, in its dish; on a tint of its first ingredient
 * that fills the slot it's in (the slot is positioned, and clips). A meal from before
 * pictures is the neutral plate.
 */
export function MealArt({ meal }: { meal: Pick<MealSummary, 'id' | 'picture'> }) {
  if (!meal.picture) return <PlateArt />
  return (
    <span className={styles.picture} style={{ background: pictureTint(meal.picture) }} aria-hidden="true">
      <MealPictureSvg picture={meal.picture} seed={meal.id} />
    </span>
  )
}

// [x, y, scale, tilt] of each icon on a plate, bowl, or skillet: the hero up and to the
// right; the sides behind it, the first lying along the dish's lower-left edge.
type Spot = readonly [number, number, number, number]
const HERO: Spot = [35, 29, 1.35, -8]
const SIDES: readonly Spot[] = [
  [21, 43, 0.95, 75],
  [40, 45, 0.9, 15],
]
// Tacos, side-on: the hero and sides rise out of the shell.
const TACO_HERO: Spot = [35, 22, 1.2, -8]
const TACO_SIDES: readonly Spot[] = [
  [44, 28, 1.0, 15],
  [20, 29, 0.95, -12],
]
const BED = 'M18 22c6-10 26-10 30 0 5 10-2 24-14 24S12 32 18 22z'
const LINE = { stroke: INK, strokeWidth: 1.6, strokeLinejoin: 'round', strokeLinecap: 'round' } as const

function MealPictureSvg({ picture, seed }: { picture: MealPicture; seed: string }) {
  const clipId = `meal-bed-${useId().replace(/[^a-zA-Z0-9-]/g, '')}`
  const { hero, bed, sides, shell } = pictureParts(picture)
  const random = seededRandom(seed)
  if (picture.dish === 'tacos') {
    return (
      <svg viewBox="0 0 64 64" fill="none">
        {sides.map((side, index) => (
          <PictureIcon key={index} item={side} spot={TACO_SIDES[index] ?? TACO_SIDES[0]} weight={1.5} />
        ))}
        <PictureIcon item={hero} spot={TACO_HERO} weight={1.6} />
        <path d="M10 34a22 20 0 0 0 44 0z" fill={MEAL_COLORS[shell.color]} {...LINE} />
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 64 64" fill="none">
      <Dish dish={picture.dish} />
      <clipPath id={clipId}>
        <path d={BED} />
      </clipPath>
      <g clipPath={`url(#${clipId})`}>
        <Surface item={bed} random={random} />
      </g>
      <path d={BED} stroke="rgba(20,19,17,.3)" />
      {sides.map((side, index) => (
        <PictureIcon key={index} item={side} spot={SIDES[index] ?? SIDES[0]} weight={1.3} />
      ))}
      <PictureIcon item={hero} spot={HERO} weight={1.6} />
    </svg>
  )
}

/** A plate (its rim and the ring of its well), a bowl, or a skillet with its handle, from above. */
function Dish({ dish }: { dish: Exclude<MealDish, 'tacos'> }) {
  switch (dish) {
    case 'plate':
      return (
        <>
          <circle cx="32" cy="32" r="27" fill="var(--cc-bg)" {...LINE} />
          <circle cx="32" cy="32" r="21" stroke="rgba(20,19,17,.16)" />
        </>
      )
    case 'bowl':
      return (
        <>
          <circle cx="32" cy="32" r="27" fill="#f3f1ec" {...LINE} />
          <circle cx="32" cy="32" r="22.5" fill="var(--cc-bg)" stroke="rgba(20,19,17,.28)" />
        </>
      )
    case 'skillet':
      return (
        <>
          <path d="M50 50l9 9" stroke={INK} strokeWidth="5.5" strokeLinecap="round" />
          <circle cx="32" cy="32" r="26" fill="#4a4540" {...LINE} />
          <circle cx="32" cy="32" r="21" stroke="rgba(255,255,255,.18)" />
        </>
      )
  }
}

function PictureIcon({ item, spot: [x, y, scale, tilt], weight }: { item: PicturedIngredient; spot: Spot; weight: number }) {
  return (
    <g
      transform={`translate(${x} ${y}) rotate(${tilt}) scale(${scale})`}
      fill="none"
      stroke={INK}
      strokeWidth={weight / scale}
      strokeLinejoin="round"
      strokeLinecap="round"
    >
      {MEAL_ICONS[item.icon](MEAL_COLORS[item.color])}
    </g>
  )
}

/** The bed's surface across the whole picture (clipped to the bed): grains of rice, strands of pasta, a sauce's shine, or small pieces. */
function Surface({ item, random }: { item: PicturedIngredient; random: () => number }) {
  const color = MEAL_COLORS[item.color]
  const dark = mix(color, INK, 0.38)
  const light = mix(color, '#ffffff', 0.4)
  const base = <rect width="64" height="64" fill={color} />
  switch (item.icon) {
    case 'fish':
    case 'steak':
    case 'drumstick':
      return (
        <>
          {base}
          {Array.from({ length: 31 }, (_, index) => (
            <path key={index} d={`M${index * 4.2 - 64} 64l64 -64`} stroke={light} strokeWidth="1.1" />
          ))}
        </>
      )
    case 'pasta':
      return (
        <>
          {base}
          {Array.from({ length: 19 }, (_, index) => (
            <path
              key={index}
              d={`M0 ${2 + index * 3.4}q2 -1.6 4 0${'t4 0'.repeat(15)}`}
              stroke={dark}
              strokeWidth=".6"
            />
          ))}
        </>
      )
    case 'sauce':
      return (
        <>
          {base}
          {[0, 1, 2].map((index) => (
            <path
              key={index}
              d={`M${(random() * 64).toFixed(2)} ${(random() * 64).toFixed(2)}a3 2 0 0 1 4 0`}
              stroke="#ffffff"
              strokeOpacity=".55"
              strokeWidth=".9"
            />
          ))}
        </>
      )
  }
  const step = item.icon === 'rice' ? 3 : 4
  const pieces: ReactNode[] = []
  for (let y = step / 2; y < 64; y += step) {
    for (let x = step / 2; x < 64; x += step) {
      const at = `translate(${(x + (random() - 0.5) * step * 0.6).toFixed(2)} ${(y + (random() - 0.5) * step * 0.6).toFixed(2)}) rotate(${(random() * 180).toFixed(1)})`
      pieces.push(<Piece key={`${x} ${y}`} icon={item.icon} at={at} color={color} light={light} dark={dark} />)
    }
  }
  return (
    <>
      {base}
      {pieces}
    </>
  )
}

function Piece({ icon, at, color, light, dark }: { icon: MealIcon; at: string; color: string; light: string; dark: string }) {
  switch (icon) {
    case 'rice':
      return <ellipse transform={at} rx="1.25" ry=".7" fill={light} stroke={dark} strokeWidth=".35" />
    case 'leafy':
    case 'stalks':
      return (
        <path
          transform={at}
          d="M-1.8 .9c0-1.8 1.4-2.8 3.4-2.8 0 1.8-1.4 2.8-3.4 2.8z"
          fill={mix(color, INK, 0.12)}
          stroke={dark}
          strokeWidth=".35"
        />
      )
    case 'beans':
      return <ellipse transform={at} rx="1.5" ry="1" fill={mix(color, '#ffffff', 0.15)} stroke={dark} strokeWidth=".4" />
    case 'tofu':
    case 'cheese_cube':
    case 'cheese_wedge':
    case 'pepper':
      return <rect transform={at} x="-1.1" y="-1.1" width="2.2" height="2.2" rx=".4" fill={light} stroke={dark} strokeWidth=".35" />
    case 'mushroom':
      return <path transform={at} d="M-1.6 .5a1.6 1.6 0 0 1 3.2 0z" fill={light} stroke={dark} strokeWidth=".35" />
    default:
      return <circle transform={at} r="1.3" fill={light} stroke={dark} strokeWidth=".35" />
  }
}

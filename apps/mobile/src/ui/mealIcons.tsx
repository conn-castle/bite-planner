import type { ReactNode } from 'react'
import type { MealIcon } from '../lib/api/types'
import { INK, mix } from './mealPicture'

/** Bone and fat: a warm white. */
const BONE = '#fbf6ec'
const WHITE = '#ffffff'

/**
 * Each icon of a meal's picture in its ingredient's color, drawn in a 24-unit box
 * centered on 0,0. The caller's group sets the ink line (ui/art.tsx: one line
 * weight, ink on warm fills); parts with no line say so.
 */
export const MEAL_ICONS: Record<MealIcon, (color: string) => ReactNode> = {
  fish: (color) => (
    <>
      <path d="M-10 0c4-6 12-6 15 0-3 6-11 6-15 0z" fill={color} />
      <path d="M5 0l6-5v10z" fill={color} />
      <circle cx="-5" cy="-1" r="1" fill={INK} stroke="none" />
    </>
  ),
  shrimp: (color) => (
    <>
      <path d="M8-3C8-10-2-11-6.5-6-10.5-1-8.5 6-2.5 8c3 1 5.5 0 6.5-2C.5 6-3 4-3 .3-3-3.7 1.5-5 3-2z" fill={color} />
      <path d="M-6.5-3.5l3.4 1M-7 1.6h3.6M-4.6 6l2-3M-1.5-7.5l.6 3.4" strokeWidth=".9" />
      <path d="M4 6l5.5 3-1.2-5.6z" fill={color} />
      <circle cx="5.2" cy="-5.2" r=".9" fill={INK} stroke="none" />
    </>
  ),
  drumstick: (color) => (
    <>
      <rect x="-1.5" y="-9.5" width="3" height="9.5" rx="1.3" fill={BONE} transform="translate(2 -2) rotate(45)" />
      <circle cx="7.4" cy="-9.8" r="2.1" fill={BONE} />
      <circle cx="9.8" cy="-7.4" r="2.1" fill={BONE} />
      <g transform="translate(-3.4 3.4) rotate(-45)">
        <path d="M9 0C6-2.5 4-6.5-2-7S-9.5-3.5-9.5 0-7 7-2 7 6 2.5 9 0z" fill={color} />
        <path d="M-7-3.5c1.5-2 4-2.5 6-2.1" stroke={WHITE} strokeOpacity=".75" />
      </g>
    </>
  ),
  // A T-bone's teardrop, a band of fat around the meat (as on the pork chop), and the bone.
  steak: (color) => {
    const outline = 'M-10 7C-11 4-8 1-7-1.5-6-5.5-5-9.5 0-10.2 5-11 11-8 11.5-3 12 1 10 5 6 7.5 2 10-5 11-8 10.8-9.5 10.6-10 9-10 7z'
    return (
      <>
        <path d={outline} fill={mix(color, WHITE, 0.62)} />
        <path d={outline} transform="translate(1 0) scale(.8) translate(-1 0)" fill={color} strokeWidth="1.1" />
        <circle cx="2" cy="-2.5" r="2.6" fill={WHITE} strokeWidth="1.1" />
      </>
    )
  },
  pork_chop: (color) => {
    const outline = 'M-11 6C-12-2-6-9 2-9c7 0 10 5 10 10 0 6-5 9-11 9-4 0-6-2-8-1s-4 0-4-3z'
    return (
      <>
        <path d={outline} fill={mix(color, WHITE, 0.6)} />
        <path d={outline} transform="translate(2 .5) scale(.72) translate(-2 -.5)" fill={color} strokeWidth="1.1" />
      </>
    )
  },
  ground_meat: (color) => (
    <>
      <path d="M-11 7c0-7 4-11 11-11s11 4 11 11z" fill={color} />
      <g strokeWidth="1">
        {(
          [
            [-7, 1, 2.6],
            [-2, -3, 2.8],
            [4, -2, 2.6],
            [8, 3.5, 2.2],
            [1, 3, 2.4],
            [-4.5, 5, 2],
            [5, 6, 1.6],
          ] as const
        ).map(([x, y, r], index) => (
          <circle key={index} cx={x} cy={y} r={r} fill={index % 2 ? mix(color, WHITE, 0.22) : color} />
        ))}
      </g>
    </>
  ),
  tofu: (color) => cube(color),
  egg: (color) => (
    <>
      <path d="M-10-1c-1-6 5-9 9-8 4-2 10 1 10 6 2 4-1 9-6 9-3 2-8 2-10-1-3-1-4-3-3-6z" fill={WHITE} />
      <circle cx="0" cy="0" r="4" fill={color} />
      <path d="M-1.6-1.8c.6-.8 1.4-1 2.2-.9" stroke={WHITE} strokeOpacity=".8" />
    </>
  ),
  beans: (color) =>
    (
      [
        [-5.5, -3.8, -20],
        [5.2, -4.2, 25],
        [0, 5.6, -5],
      ] as const
    ).map(([x, y, angle]) => (
      <g key={`${x} ${y}`} transform={`translate(${x} ${y}) rotate(${angle})`}>
        <path d="M-4.5-1c-1-2.5 1-4.5 3.5-4 1.5.3 2 1.5 3.5 1.5 2.5 0 4 2.5 2 4.5-2.5 2.5-8 2-9-2z" fill={mix(color, WHITE, 0.1)} />
        <path d="M-3.6 1.2c.8 1.4 3 2 5 1.4" stroke={WHITE} strokeOpacity=".45" strokeWidth=".9" />
        <path d="M-1.6-2.4c.8.4 1.6.5 2.4.4" stroke={WHITE} strokeWidth="1.3" />
      </g>
    )),
  chickpeas: (color) =>
    (
      [
        [-4.5, -2],
        [4, -2.5],
        [0, 4.5],
      ] as const
    ).map(([x, y]) => (
      <g key={`${x} ${y}`} transform={`translate(${x} ${y})`}>
        <circle r="4.2" fill={color} />
        <path d="M1.6-3.5l2.4-1.3-.5 2.4z" fill={color} strokeWidth=".8" />
        <path d="M-.6-3.8c.8 2 .8 4.4-.4 6.6" strokeWidth=".8" strokeOpacity=".6" />
      </g>
    )),
  rice: (color) => (
    <>
      <path d="M-11 6c1-8 6-12 11-12s10 4 11 12z" fill={color} />
      <path d="M-5 0h2M2-2h2M-1 3h2M5 2h2" />
    </>
  ),
  // A pile of elbow macaroni.
  pasta: (color) =>
    (
      [
        [-5, -5, 20],
        [5, -5.5, 250],
        [6.5, 3.5, 95],
        [-1, 6, 330],
        [-7.5, 3, 170],
      ] as const
    ).map(([x, y, angle]) => (
      <g key={`${x} ${y}`} transform={`translate(${x} ${y}) rotate(${angle}) scale(.9) translate(2.5 -.5)`}>
        <path d="M-6 4A7 7 0 0 1 1-3L1 1A3 3 0 0 0-2 4z" fill={color} />
        <path d="M-4.6 1.2l2.6 1M-3.2-.8l2 1.6M-1.2-2.2l1 2" strokeWidth=".7" strokeOpacity=".5" />
        <ellipse cx="1" cy="-1" rx="1" ry="2" fill={mix(color, INK, 0.35)} strokeWidth=".8" />
      </g>
    )),
  sauce: (color) => <path d="M0-10c5 7 8 10 8 14a8 8 0 0 1-16 0c0-4 3-7 8-14z" fill={color} />,
  tortilla: (color) => (
    <>
      <circle r="10.5" fill={color} />
      {(
        [
          [-4, -4, 1.4],
          [3, -6, 1],
          [5, 2, 1.5],
          [-2, 4, 1.1],
          [-6, 2, 0.8],
          [1, -1, 0.9],
          [6.5, -3, 0.7],
        ] as const
      ).map(([x, y, r]) => (
        <circle key={`${x} ${y}`} cx={x} cy={y} r={r} fill={mix(color, '#5a3a1a', 0.45)} stroke="none" />
      ))}
    </>
  ),
  leafy: (color) => (
    <>
      <path d="M-9 8c0-10 8-17 18-17 0 10-7 17-18 17z" fill={color} />
      <path d="M-9 8L5-5" />
    </>
  ),
  stalks: (color) =>
    [-3.6, 0, 3.6].map((y, index) => (
      <path
        key={y}
        transform={`translate(${index - 1} ${y})`}
        d="M-10 2.5C-4 1 4-2 9.5-6c.8.6.8 1.6.2 2.4C4.5.5-3.5 4-9.5 4.3c-.9 0-1.2-1.2-.5-1.8z"
        fill={color}
      />
    )),
  round: (color) => (
    <>
      <circle cx="0" cy="1" r="8" fill={color} />
      <path d="M0-7c1-3 4-4 6-4-1 3-3 4-6 4z" fill="#6f9a4a" />
    </>
  ),
  pepper: (color) => (
    <>
      <path d="M-6-4c-3 6 0 13 6 14 6-1 9-8 6-14-2-3-10-3-12 0z" fill={color} />
      <path d="M0-5c0-3 1-5 3-6" />
    </>
  ),
  mushroom: (color) => (
    <>
      <path d="M-10 0c0-7 5-10 10-10s10 3 10 10z" fill={color} />
      <path d="M-4 0v7c0 2 8 2 8 0V0" fill="#efe6d6" />
    </>
  ),
  root: (color) => (
    <>
      <path d="M-8 9L6-6c3 0 4 2 3 4L-7 10c-1 0-1 0-1-1z" fill={color} />
      <path d="M7-6l2-5M8-5l5-2" />
    </>
  ),
  broccoli: (color) => (
    <>
      <path d="M-2.5 1h5l1.5 9.5h-8z" fill={mix(color, WHITE, 0.45)} />
      <circle cx="-6" cy="-1" r="4.6" fill={color} />
      <circle cx="6" cy="-1.5" r="4.6" fill={color} />
      <circle cx="-2.5" cy="-6" r="5" fill={color} />
      <circle cx="3.5" cy="-6.5" r="4.6" fill={color} />
      <circle cx="0" cy="-1" r="4" fill={color} />
    </>
  ),
  corn: (color) => (
    <>
      <path d="M-2 10C-8 8-10 2-9-3c3 3 6 8 7 13zM2 10C8 8 10 2 9-3c-3 3-6 8-7 13z" fill="#7aa34c" />
      <ellipse cx="0" cy="-2" rx="5.4" ry="10" fill={color} />
      <path d="M-2.6-10v16M0-11.5v18M2.6-10v16M-5-4h10M-5 0h10M-4.6 4h9.2M-4.6-8h9.2" strokeWidth=".6" strokeOpacity=".55" />
    </>
  ),
  bread: (color) => (
    <>
      <path d="M-9 9V-2c-3-1-3-7 1-8 3-1 6-1 8 0 3-1 6-1 8 0 4 1 4 7 1 8V9z" fill={color} />
      <path
        d="M-6.5 6.5V-3.2c-2-.6-2-4 .7-4.6 2.6-.5 4.6-.5 5.8.4 1.2-.9 3.2-.9 5.8-.4 2.7.6 2.7 4 .7 4.6v9.7z"
        fill={mix(color, WHITE, 0.6)}
        strokeWidth="1"
      />
    </>
  ),
  bacon: (color) => (
    <>
      <path d="M-11-4.5q3-3 5.5 0t5.5 0 5.5 0 5.5 0v8q-2.7 3-5.5 0t-5.5 0-5.5 0-5.5 0z" fill={color} />
      <path d="M-11-.5q3-3 5.5 0t5.5 0 5.5 0 5.5 0" stroke="#fbeee2" strokeWidth="1.8" />
    </>
  ),
  cheese_wedge: (color) => (
    <>
      <path d="M-10 6L8-8c3 4 3 10 2 14z" fill={color} />
      <circle cx="3" cy="2" r="1.4" />
    </>
  ),
  cheese_cube: (color) => cube(color),
}

function cube(color: string): ReactNode {
  return (
    <>
      <path d="M-7-3l7-4 7 4v8l-7 4-7-4z" fill={color} />
      <path d="M-7-3l7 4 7-4M0 1v8" />
    </>
  )
}

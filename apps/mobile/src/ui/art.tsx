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

export function PlateArt() {
  return (
    <svg viewBox="0 0 148 148" fill="none">
      <circle cx="74" cy="74" r="70" fill="var(--cc-fill)" />
      <circle cx="74" cy="76" r="38" fill="#fff" stroke="var(--cc-hairline)" strokeWidth="1" />
      <circle cx="74" cy="76" r="27" stroke="var(--cc-fill-strong)" strokeWidth="2" />
      <path
        d="M28 50v16a5 5 0 0 0 10 0V50M33 50v52"
        stroke="var(--cc-ink)"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
      <path
        d="M120 50c-6 4-7 16-7 24h7v28"
        stroke="var(--cc-ink)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M68 70c4-9 13-11 18-10-1 7-7 13-18 10z" fill="var(--cc-accent)" />
      <path d="M68 70c3-3 7-6 12-7" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

/** A neutral plate standing in for a meal's picture until meals have icons. */
export function MealArt() {
  return (
    <svg viewBox="0 0 64 64" fill="none" aria-hidden="true">
      <circle cx="32" cy="32" r="24" fill="var(--cc-bg)" stroke="var(--cc-hairline)" strokeWidth="1" />
      <circle cx="32" cy="32" r="16" stroke="var(--cc-fill-strong)" strokeWidth="1.5" />
    </svg>
  )
}

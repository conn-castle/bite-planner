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

/** A neutral plate standing in for a meal's picture until meals have icons. */
export function MealArt() {
  return (
    <svg viewBox="0 0 64 64" fill="none" aria-hidden="true">
      <circle cx="32" cy="32" r="24" fill="var(--cc-bg)" stroke="var(--cc-hairline)" strokeWidth="1" />
      <circle cx="32" cy="32" r="16" stroke="var(--cc-fill-strong)" strokeWidth="1.5" />
    </svg>
  )
}

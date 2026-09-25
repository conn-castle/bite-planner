import { useRef, useState } from 'react'
import styles from './CodeInput.module.css'

export const CODE_LENGTH = 6

interface CodeInputProps {
  value: string
  onChange: (value: string) => void
  onComplete: (value: string) => void
  disabled?: boolean
}

/**
 * A one-time-code field drawn as six boxes. It is a single real input, so iOS
 * can fill the code from Mail and pasting works.
 */
export function CodeInput({ value, onChange, onComplete, disabled }: CodeInputProps) {
  const input = useRef<HTMLInputElement>(null)
  const [focused, setFocused] = useState(false)

  function change(text: string) {
    const digits = text.replace(/\D/g, '').slice(0, CODE_LENGTH)
    onChange(digits)
    if (digits.length === CODE_LENGTH) onComplete(digits)
  }

  return (
    <div className={styles.code} onClick={() => input.current?.focus()}>
      <input
        ref={input}
        className={styles.input}
        value={value}
        onChange={(event) => change(event.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        inputMode="numeric"
        autoComplete="one-time-code"
        aria-label="Sign-in code"
        maxLength={CODE_LENGTH}
        disabled={disabled}
        autoFocus
      />
      {Array.from({ length: CODE_LENGTH }, (_, index) => {
        const active = focused && index === Math.min(value.length, CODE_LENGTH - 1)
        return (
          <span
            key={index}
            className={`${styles.box} ${active ? styles.active : ''} ${value[index] ? styles.filled : ''}`}
            aria-hidden="true"
          >
            {value[index] ?? ''}
          </span>
        )
      })}
    </div>
  )
}

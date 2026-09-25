import { describe, expect, it } from 'vitest'
import { ApiError, isRetryable } from './errors'

describe('isRetryable', () => {
  it('retries a request that may never have reached the API', () => {
    expect(isRetryable(new TypeError('Failed to fetch'), 0)).toBe(true)
    expect(isRetryable(new TypeError('Failed to fetch'), 2)).toBe(true)
  })

  it('gives up after three attempts', () => {
    expect(isRetryable(new TypeError('Failed to fetch'), 3)).toBe(false)
  })

  it('does not repeat a request the API answered', () => {
    expect(isRetryable(new ApiError(429, 'You already have 3 requests waiting.'), 0)).toBe(false)
    expect(isRetryable(new ApiError(503, 'The request could not be started.'), 0)).toBe(false)
  })
})
